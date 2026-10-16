# Architecture: Dormant Account Detection Dashboard

This document describes how the solution works. Its sections follow the AWS Prescriptive Guidance (APG) pattern: Architecture, Tools, and Additional information. To deploy, see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md). For parameters and deployment behavior, see [REFERENCE.md](REFERENCE.md).

## Contents

- [Target technology stack](#target-technology-stack)
- [Target architecture](#target-architecture)
- [Cross-account IAM trust model](#cross-account-iam-trust-model)
- [Automation and scale](#automation-and-scale)
- [AWS services](#aws-services)
- [Additional information](#additional-information)

---

## Target technology stack

- Amazon EventBridge (scheduled rule, daily trigger)
- AWS Step Functions (Standard workflow, state machine orchestration)
- AWS Lambda (7 Python 3.14 functions with inline code: 6 collection and publishing functions, and 1 custom dashboard widget function)
- Amazon CloudWatch (custom metrics, a dashboard with 22 to 26 widgets, up to 5 alarms, and 8 encrypted log groups)
- Amazon Simple Notification Service (Amazon SNS) (2 encrypted topics with email subscriptions, optional)
- AWS Key Management Service (AWS KMS) (customer managed keys for Amazon CloudWatch Logs, Step Functions execution history, and SNS encryption)
- AWS Identity and Access Management (IAM) (9 execution and service roles in the main stack, 3 cross-account roles, and 14 customer managed policies across 4 templates)
- AWS CloudFormation (4 templates) and AWS CloudFormation StackSets (`SERVICE_MANAGED` mode with automatic deployment)
- AWS Organizations (OU traversal for account discovery)
- AWS Config (aggregator for resource inventory)
- AWS Cost Explorer (per-account monthly cost)
- AWS Security Token Service (AWS STS) (cross-account role assumption with an optional external ID)
- AWS Systems Manager Parameter Store (account-name registry that keeps the history of renamed accounts linked)

## Target architecture

![Architecture diagram](../diagrams/DormantAccountDetectionDashboard%20-%20Numbered.jpg)

The solution spans four account tiers: the Dashboard account, the management account, the Config Aggregator account, and the target member accounts. The Dashboard account hosts all compute and observability resources. The numbers match the diagram.

1. An EventBridge rule starts a Step Functions state machine daily at 2:00 AM UTC (`ScheduleExpression`).
2. The state machine invokes 6 Lambda functions in a defined sequence.
3. The GetAccounts function assumes the cross-account role in the management account.
   - 3a. It resolves the target accounts by traversing the specified OUs through the AWS Organizations API, removes `ExcludedAccountIds` and the management account, keeps only active accounts, and reads each account's join date for the new-account grace period.
4. The four collection branches run in parallel.
5. The CollectCost function assumes the cross-account role in the management account.
   - 5a. It calls the Cost Explorer API once for the previous month's cost per linked account.
6. The CollectConfig function assumes the cross-account role in the Config Aggregator account.
   - 6a. It runs `SelectAggregateResourceConfig` queries against the AWS Config aggregator that count total resources and resources created during the lookback window per account.
7. The CollectIAM function assumes the cross-account role in each target account, through a Map state with configurable concurrency (default 10).
   - 7a. It reads the IAM credential report and `GetAccountAuthorizationDetails` to count active IAM users, IAM roles, and IAM Identity Center permission set roles. Permission sets that match `AdminSSOPrefixes` are counted separately and excluded from scoring.
8. The CollectApiCallMetrics function assumes the cross-account role in each target account, through a second Map state.
   - 8a. It sums every CloudWatch `AWS/Usage` `CallCount` series over the lookback window across every enabled Region (or the `TargetRegions` list plus the home Region).
9. The PublishMetrics function scores each account and publishes the per-account, alert, and collection health metrics to CloudWatch. The CloudWatch dashboard and alarms read these metrics. When an account newly falls into the CRITICAL or HIGH band, PublishMetrics sends one combined email through Amazon SNS.
   - 9a. It records the names of renamed accounts in Parameter Store, so their history stays linked.
   - 9b. It reads each account's metric history back from CloudWatch (`GetMetricData` over `LookbackDays`) to compute the trend half of the score.
10. The dashboard's Dormant Accounts widget calls the seventh Lambda function, `DormantAccountDetection-customWidget-DormantAccounts`, when the dashboard loads. The function reads each account's latest `DormancyScore` from CloudWatch metrics and renders a table of the CRITICAL and HIGH accounts.

The Dashboard account components:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           DASHBOARD ACCOUNT                                    │
│                                                                                 │
│  ┌──────────────┐    ┌───────────────────────────────────────────────────────┐  │
│  │  EventBridge │───▶│            Step Functions State Machine               │  │
│  │  (Daily 2AM) │    │                                                       │  │
│  └──────────────┘    │  1. GetAccounts (→ Management Account)                │  │
│                      │        │                                               │  │
│                      │        ▼                                               │  │
│                      │  2. Parallel Collection:                               │  │
│                      │     ├── CollectCost       (batch → Management Account) │  │
│                      │     ├── CollectConfig     (batch → Config Aggr. Acct)  │  │
│                      │     ├── CollectIAM        (Map → each Target Account)  │  │
│                      │     └── CollectApiCall    (Map → each Target Account)  │  │
│                      │                                                        │  │
│                      │  3. PublishMetrics (CloudWatch + Dormancy Scoring)      │  │
│                      └────────────────────────────────────────────────────────┘  │
│                                          │                                       │
│                      ┌───────────────────┼────────────────────────────┐          │
│                      │   CloudWatch      │                            │          │
│                      │   ├── Metrics (up to 11 per account)           │          │
│                      │   ├── Alarms (up to 5)                         │          │
│                      │   ├── Dashboard (22-26 widgets)                │          │
│                      │   └── Log Groups (8 encrypted)                 │          │
│                      │                      │                         │          │
│                      │                      ▼                         │          │
│                      │   SNS Topics (email, from PublishMetrics       │          │
│                      │   and the 3 health alarms)                     │          │
│                      └────────────────────────────────────────────────┘          │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Cross-account IAM trust model

Each AWS Lambda function has its own **dedicated AWS Identity and Access Management (IAM) execution role**. Every execution role trusts only the `lambda.amazonaws.com` service principal. Cross-account access comes from the roles in the other accounts, which trust specific execution role ARNs (see [Cross-account role templates](#cross-account-role-templates)):

| Lambda Function | Execution Role | Assumes Role In | Data Collected |
|---|---|---|---|
| `GetAccounts` | `DormantAccountDetection-GetAccounts-Role` | Management Account | Account list from AWS Organizations |
| `CollectCost` | `DormantAccountDetection-CollectCost-Role` | Management Account | Monthly cost (AWS Cost Explorer) |
| `CollectConfig` | `DormantAccountDetection-CollectConfig-Role` | Config Aggregator Account | Resource inventory (AWS Config aggregator) |
| `CollectApiCallMetrics` | `DormantAccountDetection-CollectApiCallMetrics-Role` | Each Target Account | API call counts (CloudWatch Usage) |
| `CollectIAM` | `DormantAccountDetection-CollectIAM-Role` | Each Target Account | IAM user/role/SSO activity |
| `PublishMetrics` | `DormantAccountDetection-PublishMetrics-Role` | _(local only)_ | Publishes metrics to CloudWatch, reads metric history (`GetMetricData`) for trends and prior ratings, and publishes dormancy notifications (`sns:Publish`, when alarms are enabled) |
| `customWidget-DormantAccounts` | `DormantAccountDetection-CustomWidget-Role` | _(local only)_ | Reads `DormancyScore` metrics (`ListMetrics`, `GetMetricData`) for the dashboard table |

Each `AssumeRole` call uses a predictable role session name, so AWS CloudTrail entries in the target account identify the calling function: `GetAccounts-OrgLookup`, `DormantAccountDetection-CollectCost`, `DormantAccountDetection-CollectConfig`, `AC-<accountId>` (`CollectApiCallMetrics`), and `IAM-<accountId>` (`CollectIAM`).

### Cross-account role templates

| Template | Deployed To | Trusted Principals |
|---|---|---|
| `management-account-monitoring-role.yaml` | Management Account | `GetAccounts-Role`, `CollectCost-Role` |
| `config-aggregator-monitoring-role.yaml` | Config Aggregator Account | `CollectConfig-Role` |
| `stackset-target-accounts-monitoring-role.yaml` | All Target Accounts | `CollectApiCallMetrics-Role`, `CollectIAM-Role` |

Each role template names its role with its `RoleName` parameter (default `DormantAccountDetectionRole`, or `DormantAccountDetection-ConfigAggregatorRole` in the Config Aggregator template). The main stack refers to them through `CrossAccountRoleName` and `ConfigAggregatorRoleName`, and all three templates support an optional `ExternalId` condition (see [Parameter reference](REFERENCE.md#parameter-reference)).


## Automation and scale

The StackSet that deploys the target account role uses `SERVICE_MANAGED` mode with automatic deployment. When a new account joins a monitored OU, StackSets deploys the role to it. The next daily run picks up the account through the GetAccounts OU traversal, and the new-account grace period (`NewAccountGracePeriodDays`, default 30) keeps it out of scoring and notifications until that many days have passed since it joined the organization.

The Map states process accounts in parallel up to `MapConcurrency` (default 10). The pipeline records a failed account as `CollectionFailed=1` without stopping the other accounts (see [Step Functions state machine](#step-functions-state-machine) for retries and the timeout). For large organizations, see the concurrency practice in [Best practices](../README.md#best-practices).

## AWS services

- [AWS CloudFormation](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/Welcome.html) helps you set up AWS resources, provision them quickly and consistently, and manage them throughout their lifecycle across AWS accounts and Regions. CloudFormation StackSets deploys the cross-account role to target member accounts.
- [AWS Lambda](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html) is a compute service that helps you run code without needing to provision or manage servers. In this pattern, it runs the 6 collection and publishing functions and the custom dashboard widget function.
- [AWS Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/welcome.html) is a serverless orchestration service that helps you combine Lambda functions and other AWS services. In this pattern, it runs the collection pipeline with parallel execution, retries, and error handling.
- [Amazon EventBridge](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-what-is.html) is a serverless event bus service. In this pattern, a scheduled rule starts the state machine daily.
- [Amazon CloudWatch](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/WhatIsCloudWatch.html) helps you monitor the metrics of your AWS resources and the applications you run on AWS in real time. In this pattern, it stores the per-account metrics, hosts the dashboard, and evaluates the alarms.
- [Amazon Simple Notification Service (Amazon SNS)](https://docs.aws.amazon.com/sns/latest/dg/welcome.html) helps you coordinate and manage the exchange of messages between publishers and clients. In this pattern, it delivers the dormancy, collection-failure, and heartbeat emails.
- [AWS Key Management Service (AWS KMS)](https://docs.aws.amazon.com/kms/latest/developerguide/overview.html) helps you create and control cryptographic keys to help protect your data. In this pattern, customer managed keys encrypt the log groups, Step Functions execution history, and SNS topics by default.
- [AWS Identity and Access Management (IAM)](https://docs.aws.amazon.com/IAM/latest/UserGuide/introduction.html) helps you securely manage access to your AWS resources by controlling who is authenticated and authorized to use them. In this pattern, it provides per-function execution roles and read-only cross-account roles.
- [AWS Organizations](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_introduction.html) is an account management service that helps you consolidate multiple AWS accounts into an organization that you create and centrally manage. In this pattern, it provides the account hierarchy, OU membership, and account join dates.
- [AWS Config](https://docs.aws.amazon.com/config/latest/developerguide/WhatIsConfig.html) provides a detailed view of the resources in your AWS account and how they're configured. In this pattern, an aggregator provides resource counts.
- [AWS Cost Explorer](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-what-is.html) helps you view and analyze your AWS costs and usage. In this pattern, it provides per-account monthly cost.
- [AWS Security Token Service (AWS STS)](https://docs.aws.amazon.com/STS/latest/APIReference/welcome.html) helps you request temporary, limited-privilege credentials. In this pattern, it issues the credentials for cross-account role assumption.
- [AWS Systems Manager Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html) provides secure, hierarchical storage for configuration data. In this pattern, it stores the names of renamed accounts so their history stays linked.

---

## Additional information

### Lambda functions

All 7 Lambda functions use **Python 3.14** runtime with inline code (CloudFormation `ZipFile`). No build pipeline or Lambda deployment package is required. Six run in the Step Functions pipeline; the seventh backs the dashboard's "Dormant Accounts" custom widget.

| Function | Timeout | Data Source | API Strategy |
|---|---|---|---|
| `GetAccounts` | 180s | Organizations | Recursive OU traversal via paginated `list_accounts_for_parent` |
| `CollectApiCallMetrics` | 300s | CloudWatch `AWS/Usage` | Per Region, paginated `ListMetrics` lists every `CallCount` series (Type `API`), and batched `GetMetricData` calls (up to 500 series each) sum them over the lookback window, so busy accounts with more than 500 series aren't undercounted. Regions: every Region enabled in the Dashboard account by default, or the `TargetRegions` list plus the home Region; no CloudTrail |
| `CollectIAM` | 300s | IAM | Credential Report + `GetAccountAuthorizationDetails` |
| `CollectConfig` | 300s | AWS Config aggregator | `SelectAggregateResourceConfig` SQL queries |
| `CollectCost` | 180s | Cost Explorer | `GetCostAndUsage` for the previous calendar month (us-east-1 only, batch for all accounts) |
| `PublishMetrics` | 300s | CloudWatch, Amazon SNS | `PutMetricData` (batched 1000 per call) + dormancy scoring; `GetMetricData` for trend history and prior ratings; `sns:Publish` for dormancy notifications |
| `customWidget-DormantAccounts` | 60s | CloudWatch | `ListMetrics` + `GetMetricData` on `DormancyScore`; renders the CRITICAL/HIGH table when the dashboard loads |

#### Account and Region scope

- **Target accounts:** `GetAccounts` resolves the accounts under `TargetOuIds` (OU IDs or the organization root ID, traversed recursively) plus any `TargetAccountIds`, removes duplicates and `ExcludedAccountIds`, and keeps only accounts in the `ACTIVE` state. The `AtLeastOneTargetRequired` stack rule rejects a deployment where both `TargetOuIds` and `TargetAccountIds` are empty.
- **Management account:** `GetAccounts` removes the Management account from the target list on every run and logs `management_account_excluded`. The member role isn't deployed there, and the role named `CrossAccountRoleName` in the Management account trusts only `GetAccounts` and `CollectCost`, so the per-account collectors can't collect from it.
- **Regions:** `TargetRegions` (JSON array) scopes `ApiCallCount` and the AWS Config counts.
  - **Default `[]` (all Regions):** `CollectApiCallMetrics` lists the Regions enabled in the Dashboard account (`ec2:DescribeRegions`) and sums `AWS/Usage` `CallCount` across them. Regions a member account can't use, because they aren't enabled for it or an SCP denies them, are skipped and logged (`regionsSkipped`), not counted as failures. Opt-in Regions enabled only in a member account aren't queried. `CollectConfig` runs its queries with no Region filter, so resources in every Region and global resources (such as IAM) are counted.
  - **A list of Regions:** both collectors add the home Region (where the stack runs) to the list, and `CollectConfig` also adds `global`. A listed Region that the member account can't use is reported as a collection error.
  - IAM, Cost Explorer, and Organizations data is global and unaffected by `TargetRegions`.
- **AWS Config aggregator:** `CollectConfig` queries `ConfigAggregatorName` in the Config Aggregator account (see [AWS Config aggregator](REFERENCE.md#aws-config-aggregator)).
- **Lookback window:** `LookbackDays` (default 90, range 1–365) sets the activity window for every collected signal and the history window for the trend calculation.

### Step Functions state machine

The AWS Step Functions state machine `DormantAccountDetection-MetricCollection` orchestrates the collection pipeline with built-in error handling. The Amazon EventBridge rule `DormantAccountDetection-DailyTrigger` starts it on `ScheduleExpression` (default `cron(0 2 * * ? *)`, 2:00 AM UTC every day).

```
GetAccounts → WrapForParallel → ParallelMetricCollection → PublishMetrics
                                  ├── CollectCost (single Lambda call)
                                  ├── CollectConfig (single Lambda call)
                                  ├── CollectIAM (Map, MaxConcurrency=MapConcurrency)
                                  └── CollectApiCallMetrics (Map, MaxConcurrency=MapConcurrency)
```

- **Retry:** Each Lambda task has two retriers, both with backoff rate 2:
  - Lambda service errors (`Lambda.ServiceException`, `Lambda.AWSLambdaException`, `Lambda.SdkClientException`, `Lambda.ClientExecutionTimeoutException`): up to 6 retries from a 2-second interval, on every task.
  - Task-specific second retrier. `GetAccounts` retries `States.TaskFailed` and `Lambda.TooManyRequestsException` 3 times from 10 seconds. `CollectCost` and `CollectConfig` retry the same errors 3 times from 15 seconds. The per-account Map tasks retry only `Lambda.TooManyRequestsException`, 2 times from 10 seconds. `PublishMetrics` retries `States.TaskFailed` 2 times from 10 seconds.
- **Catch:** A `GetAccounts` failure goes to the `GetAccountsFailed` terminal state. Both per-account Map tasks catch `States.ALL` and route to `EmitIAMFailureShape` or `EmitApiCallFailureShape`, which return a result marked `failed: true` for that account; `PublishMetrics` then publishes only `CollectionFailed=1` for it. Both Map states also set `ToleratedFailurePercentage: 100`, so one account's failure doesn't stop the others. `CollectCost`, `CollectConfig`, and `PublishMetrics` have no Catch: an error that remains after the retries fails the execution, which the state-machine-failure alarm reports. Errors a collector catches itself don't fail the run; the collector reports the signals it couldn't collect in `failedSignals` (see the fault-tolerance rules under Dormancy scoring).
- **Concurrency:** Both Map states use `MaxConcurrency` = `MapConcurrency` (default 10, range 1–40). Set `LambdaReservedConcurrency` to at least this value.
- **Payload:** The execution payload doesn't carry `ExternalId` or `NotificationEmail`, so neither value appears in execution history. The five functions that assume cross-account roles read `ExternalId` from their own environment variables; no function has `NotificationEmail`, which goes only to the SNS email subscriptions. Each function's environment holds only the variables it reads.
- **Timeout:** 3 hours (10800 seconds)
- **Logging:** CloudWatch Logs at Level: ALL with `IncludeExecutionData: false`, so state input and output aren't written to the log group
- **Encryption:** Execution history uses a customer managed key or an AWS owned key (see [Step Functions execution history](#step-functions-execution-history))
- **AWS X-Ray:** Disabled (not needed for scheduled batch workloads)

### Encryption

#### CloudWatch log groups (8)

One per Lambda function (7) plus the Step Functions log group `/aws/states/DormantAccountDetection`. All 8 use `LogGroupRetentionDays` (default 365) and have `DeletionPolicy: Retain`, so deleting the stack leaves the log groups and their logs in place.

Configurable via `LogGroupKmsKeyType` parameter:

| Option | Behavior |
|---|---|
| `CMK` (default) | If `LogGroupKmsKeyArn` is provided, uses that key. If empty, auto-creates a dedicated AWS Key Management Service (AWS KMS) customer managed key (`alias/DormantAccountDetection-LogGroupKey`, `DeletionPolicy: Retain`) with configurable rotation period (`KmsKeyRotationPeriodDays`, default 365 days). |
| `AWSManaged` | No AWS KMS key is associated with the log groups. CloudWatch Logs encrypts log data with its default server-side encryption (CloudWatch Logs has no AWS managed key). |

#### Step Functions execution history

When `EnableStateMachineEncryption=true` (default) and `LogGroupKmsKeyType=CMK`, the state machine encrypts execution history with the log group customer managed key (the created key or `LogGroupKmsKeyArn`). `StateMachineKmsDataKeyReusePeriodSeconds` (default 300, range 60–900) sets how long Step Functions reuses a data key before it calls AWS KMS again. With `EnableStateMachineEncryption=false`, or with `LogGroupKmsKeyType=AWSManaged` (no customer managed key to reuse), the state machine uses an AWS owned key.

#### SNS topics (2)

The topics `DormantAccountDetection-CriticalDormancyAlert` and `DormantAccountDetection-HighDormancyAlert`, with their email subscriptions, exist only when alarms are enabled (`EnableDormancyAlarms=true` and a non-empty `NotificationEmail`).

Configurable via `SnsKmsKeyType` parameter:

| Option | Behavior |
|---|---|
| `CMK` (default) | If `SnsKmsKeyArn` is provided, uses that key. If empty, auto-creates a dedicated customer managed key (`alias/DormantAccountDetection-SnsKey`, `DeletionPolicy: Retain`) with the same rotation setting. |
| `AWSManaged` | Uses the AWS managed key for Amazon SNS (`alias/aws/sns`). |

Both SNS topics enforce TLS-only publishing via `aws:SecureTransport` deny policies.

### Dormancy scoring (hybrid self-baselining model)

Each account receives a score from 0 (fully dormant) to 100 (fully active). The score has two halves whose weights sum to 100 by default.

#### Half 1: Absolute human-presence signals (baseline-free, all-or-nothing)

| Default Weight | Signal | Source |
|---|---|---|
| 35 | `ActiveSSOUserCount > 0` (non-admin human sign-in through AWS IAM Identity Center) | IAM `AWSReservedSSO_*` role usage |
| 15 | `ActiveIAMUserCount > 0` (IAM user credential activity) | IAM Credential Report |

These award their full weight if any such activity occurred in the lookback window, nothing otherwise. `ActiveSSOUserCount` counts the IAM Identity Center permission set roles (`AWSReservedSSO_*`) in the account whose `RoleLastUsed` date falls in the lookback window. It counts roles, not distinct people: several users of one permission set count once, and one user with two permission sets counts twice. Single sign-on (SSO) roles whose names start with any prefix in `AdminSSOPrefixes` are excluded from `ActiveSSOUserCount` and counted separately in `ActiveAdminSSOUserCount` (dashboard-only), so routine admin logins don't mask an otherwise dormant account.

#### Half 2: Self-relative activity-trend signals (each account is its own baseline)

| Default Weight | Signal | Source |
|---|---|---|
| 20 | `ApiCallCount` trend | CloudWatch `AWS/Usage CallCount` |
| 10 | `ActiveIAMRoleCount` trend | IAM `GetAccountAuthorizationDetails` |
| 10 | `MonthlyCostUSD` trend | Cost Explorer |
| 10 | `ResourceCreatedCount` trend | AWS Config aggregator |

Points awarded = `weight × trend ratio`, where **trend ratio = mean(last N days) / mean(first N days)** of the account's own history, clamped to the range [0, 1]. `N = max(1, round(TrendWindowFraction × LookbackDays))`. Stable or growing activity earns the full weight; a genuine wind-down earns proportionally fewer points. `TotalResourceCount` is published for visibility but intentionally **not** scored, because standing inventory isn't an activity signal.

`PublishMetrics` reads each signal's daily series for the account from CloudWatch over `LookbackDays` (one batched `GetMetricData` request per account, `Period` 86400, `Stat` `Maximum`). The two windows count datapoints, not calendar days, so a missed collection day shifts which days each window covers. The inputs are themselves window aggregates: `ApiCallCount`, `ActiveIAMRoleCount`, and `ResourceCreatedCount` each cover the full lookback window, and `MonthlyCostUSD` is the previous calendar month. A trend therefore lags a real change in activity.

**Cold-start / fault-tolerance rules:**
- An account needs at least `2 × N` daily datapoints before a trend is computed; below that the ratio is 1.0 (full points), so a fresh deployment or newly onboarded account isn't flagged because of missing history.
- An account with a zero leading-window mean also receives 1.0 (no baseline to decline from).
- If the history request fails for an account, all four of its trend ratios are set to 1.0 for that run, and the `dormancy_score` log entry carries `signalHistoryFetchFailed=true`.
- A collection failure does not lower a score. Collectors retry throttled calls (adaptive retry mode, 10 attempts); a signal that still can't be collected is reported in `failedSignals`. `PublishMetrics` then publishes `CollectionFailed=1` for the account (for every account when the Cost Explorer or AWS Config query fails), doesn't publish that raw signal, so no false zero enters the history, and:
  - skips scoring for the account this run when IAM Identity Center or IAM user sign-ins are missing (log event `dormancy_scoring_skipped_incomplete`), or
  - scores a missing trend signal at the neutral 1.0 (listed in `neutralTrendSignals` in the `dormancy_score` log entry).
  The `AccountsFailed` alarm reports the failure. An account whose collection keeps failing isn't rated until a run succeeds.
- Metrics carry the `AccountId` and `AccountName` dimensions, so renaming an account starts a new series. To keep history linked, `PublishMetrics` lists the `CollectionFailed` series each run (`ListMetrics` returns series with data in the past two weeks), and when an account appears under more than one name it records the names in an AWS Systems Manager Parameter Store `String` parameter, `/DormantAccountDetection/AccountNames/<accountId>`. Trend history and the prior rating are then read across every recorded name and merged by timestamp. Only renamed accounts get a parameter. The custom widget shows each account once, under the name with the newest datapoint.
- This model detects **decline**, not sustained flat dormancy: an account that isn't declining doesn't fall below the 50 presence points by trend alone.
- At the `TrendWindowFraction` maximum of 0.5, the trend half needs a full window of daily datapoints, and a single missed collection flips all four trend ratios to the neutral 1.0.

#### New-account grace period

Accounts within `NewAccountGracePeriodDays` (default 30, range 0–365) of their Organizations `JoinedTimestamp` still have raw signals collected and published (so the dashboard stays populated) but are **not** scored and do **not** count toward CRITICAL/HIGH alert totals. Set to 0 to score all accounts. An account whose `JoinedTimestamp` is unknown or can't be parsed is scored normally.

#### Rating bands

The 0–100 score is bucketed into a 1–5 `DormancyRating` using four strictly increasing upper-bound thresholds (defaults shown):

| Score | Rating | Default band | Meaning |
|---|---|---|---|
| ≤ `CriticalDormancyMaxScore` | CRITICAL (1) | 0–20 | No detectable activity: strong dormancy candidate |
| ≤ `HighDormancyMaxScore` | HIGH (2) | 21–40 | Minimal activity: likely dormant |
| ≤ `MediumDormancyMaxScore` | MEDIUM (3) | 41–60 | Some activity: needs review |
| ≤ `LowDormancyMaxScore` | LOW (4) | 61–80 | Moderate activity: probably active |
| > `LowDormancyMaxScore` | ACTIVE (5) | 81–100 | High activity: clearly in use |

All weights, band boundaries, the lookback window, grace period, and trend-window fraction are CloudFormation parameters (see [Parameter reference](REFERENCE.md#parameter-reference)). The stack doesn't validate this ordering at deploy time, because CloudFormation rules can't compare numeric values. With out-of-order values the rating logic still runs (the first band whose upper bound the score doesn't exceed wins), but one or more bands stay empty.

### Security controls

| Control | Implementation |
|---|---|
| Encryption at rest (logs) | AWS KMS customer managed key (`CMK`, default) or CloudWatch Logs default server-side encryption with no AWS KMS key (`AWSManaged`) |
| Encryption at rest (Step Functions) | Log group customer managed key (`EnableStateMachineEncryption=true` with `CMK`), otherwise an AWS owned key |
| Encryption at rest (SNS) | AWS KMS customer managed key or AWS managed key (configurable) |
| Encryption in transit (SNS) | `aws:SecureTransport` Deny policy on both topics (TLS only) |
| Least privilege IAM | Per-function execution roles with scoped policies |
| Account-name registry | `PublishMetrics` can read and write only `parameter/DormantAccountDetection/AccountNames` and the parameters under it (`ssm:GetParametersByPath`, `ssm:PutParameter`). The parameters hold account IDs and names only. |
| Config Aggregator role | Grants only `config:SelectAggregateResourceConfig`, on `arn:aws:config:*:<account>:config-aggregator/*` (aggregators in the Config Aggregator account) |
| Cross-account trust | Specific per-function role ARNs (not wildcards) |
| Optional ExternalId | All 3 cross-account role templates + main stack support `ExternalId` for confused deputy protection, and the parameter is `NoEcho` in all four templates. When set, all Lambda `assume_role()` calls pass `sts:ExternalId`. The value isn't in the Step Functions payload; each function reads it from its environment variable. |
| Org-scoped AssumeRole | Optional `aws:ResourceOrgID` condition restricts `CollectApiCallMetrics` and `CollectIAM` to within-org accounts only (set via `OrganizationId` parameter). The condition is on those two execution role policies in the main stack; the cross-account role templates have no organization condition. |
| Personal data in payload and logs | `GetAccounts` drops account email addresses, and `NotificationEmail` isn't in the Step Functions payload. Step Functions logging uses `IncludeExecutionData: false`. `CollectIAM` logs only counts unless `LogIamPrincipalNames=true` (default `false`), which adds active IAM user names, their credential types, and role names (including IAM Identity Center permission set roles). |
| Reserved concurrency | Configurable per Lambda (`LambdaReservedConcurrency`) |
| AWS KMS key rotation | Automatic rotation with configurable period (90-2560 days) |
| Step Functions trust | `aws:SourceAccount` condition on the Step Functions execution role prevents confused deputy attacks |
| Customer managed policies | All 7 Lambda, Step Functions, and EventBridge execution roles attach standalone `AWS::IAM::ManagedPolicy` resources. 3 cross-account role templates also use standalone managed policies. No template uses inline policies. |

### CloudWatch resources

#### CloudWatch metrics

Default namespace names are shown; they're configurable through `MetricsNamespace`, `SfnAlertNamespace`, and `SfnMetricsNamespace`.

- `DormantAccountDetection`: up to 11 per-account metrics dimensioned by `AccountId` + `AccountName`: `CollectionFailed`, `ApiCallCount`, `ActiveIAMUserCount`, `ActiveIAMRoleCount`, `ActiveSSOUserCount`, `TotalResourceCount`, `ResourceCreatedCount`, `MonthlyCostUSD`, `DormancyScore`, `DormancyRating`, and `ActiveAdminSSOUserCount` (published only when `AdminSSOPrefixes` is set). Accounts in the new-account grace period skip `DormancyScore` and `DormancyRating`. An account whose per-account Map task failed publishes only `CollectionFailed=1`; an account with a partial (caught) collection error publishes `CollectionFailed=1` alongside the signals that were collected.
- `DormantAccountDetectionAlerts`: dimensionless `CriticalDormancyAccountCount` + `HighDormancyAccountCount`
- `DormantAccountDetectionSfn`: execution health metrics `MetricCollectionSuccess`, `MetricCollectionFailure`, `ExecutionDurationSeconds`, `AccountsProcessed`, and `AccountsFailed`, dimensioned by `StateMachine=AccountMetricCollection`. `PublishMetrics` publishes them at the end of each run.

#### CloudWatch alarms

| Alarm | Trigger | Target |
|---|---|---|
| `DormantAccountDetection-CriticalDormancyAccounts` | `CriticalDormancyAccountCount >= 1` (daily, `Maximum`) | None (status only) |
| `DormantAccountDetection-HighDormancyAccounts` | `HighDormancyAccountCount >= 1` (daily, `Maximum`) | None (status only) |
| `DormantAccountDetection-AccountsFailed` | `AccountsFailed >= 1` (partial collection failure, `Period: 60s`, `TreatMissingData: notBreaching`, schedule-agnostic) | SNS CRITICAL topic |
| `DormantAccountDetection-StateMachineFailed` | `AWS/States` `ExecutionsFailed + ExecutionsTimedOut + ExecutionsAborted` on the `DormantAccountDetection-MetricCollection` state machine (`Period: 60s`, `TreatMissingData: notBreaching`) | SNS CRITICAL topic |
| `DormantAccountDetection-CollectionHeartbeat` | No completed run for `HeartbeatWindowHours`: `ExecutionDurationSeconds` `SampleCount < 1` in the `DormantAccountDetectionSfn` namespace (`StateMachine=AccountMetricCollection`), `Period: 3600`, `EvaluationPeriods` and `DatapointsToAlarm` = `HeartbeatWindowHours`, `TreatMissingData: breaching` | SNS CRITICAL topic |

The two dormancy-count alarms have no alarm action. A CloudWatch alarm invokes its action only on a state transition, so once a count alarm is in `ALARM` it would stay silent when another account becomes dormant. These alarms drive the console alarm state and the two alarm status widgets on the dashboard; dormancy emails come from the `PublishMetrics` function (see below).

The two failure alarms (`EnableCollectionFailureAlarm`, `EnableStateMachineFailureAlarm`, both default `true`) and the heartbeat alarm (`EnableHeartbeatAlarm`, default `true`) require `EnableDormancyAlarms=true` and a non-empty `NotificationEmail`. They catch silent failures that would otherwise leave the dashboard stale without any signal:

- The failure alarms treat missing data as not breaching. No datapoint arrives between runs, so they return to `OK` in the minute after a failed run and notify again on every run that fails.
- The heartbeat alarm covers the opposite case: no run completes at all, for example because the EventBridge rule is disabled or every run fails before `PublishMetrics`. It fires when no run has completed within `HeartbeatWindowHours` (default 26, range 2–168). It fires up to about 2 hours after the window passes, because the alarm needs that many full empty hourly periods and CloudWatch waits briefly before treating missing data as breaching. Keep the window longer than the `ScheduleExpression` interval plus the run time, and disable the alarm for schedules that run less often than about every 6 days.

#### Notifications

When alarms are enabled (`EnableDormancyAlarms=true` and a non-empty `NotificationEmail`), the `PublishMetrics` function sends dormancy notifications itself through `sns:Publish`:

- Before publishing this run's ratings, it reads each account's previous `DormancyRating` within `PriorRatingWindowDays` (see [Parameter reference](REFERENCE.md#parameter-reference)).
- Only accounts whose rating is now CRITICAL or HIGH **and** worse than their previous rating are reported as newly flagged. A steady set of dormant accounts does not re-notify every day.
- Each run sends at most one combined email, through the topic that matches the most severe new crossing (CRITICAL or HIGH). The email also lists accounts that were already flagged.
- The first run after deployment has no rating history, so it records a baseline and sends no email.
- If the previous ratings can't be read while accounts are flagged, the function fails the run instead of silently skipping the notification. The state-machine-failure alarm then reports it.

#### Dashboard layout

Pre-built `DormantAccountDetectionDashboard` with a 30-day default view and an account-name filter. The widget count depends on two options:

| Alarms enabled | `AdminSSOPrefixes` set | Widgets |
|---|---|---|
| No (default, or `NotificationEmail` empty) | No (default) | 22 |
| Yes | No | 24 (adds 2 alarm status widgets beside the Dormant Accounts table) |
| No | Yes | 24 (adds an SSO Activity section with separate non-admin and admin widgets) |
| Yes | Yes | 26 |

The Dormant Accounts table reads each account's most recent day of `DormancyScore` data within `DormantAccountsWidgetMetricWindowDays` (default 14, range 1–90) and uses that day's lowest score. Increase the window if collection runs less often than every few days; otherwise accounts without a recent datapoint drop out of the table.

### Cost estimate

The approximate monthly cost of running the solution with the main stack in the Europe (Frankfurt) Region, eu-central-1, is about $31. The scenario is 50 monitored accounts, default parameters (daily schedule, all 17 enabled Regions), alarms with email notifications, and two customer managed keys. Prices are from the AWS Price List API in October 2026 and leave out free tiers, because in AWS Organizations free tiers are shared across the consolidated bill. This scenario is also available as an [AWS Pricing Calculator estimate](https://calculator.aws/#/estimate?id=8ccb7fb51a756b887f51aba4472f9f6431f380d4). Prices vary by Region, so adjust the Region and usage in the [AWS Pricing Calculator](https://calculator.aws/) for your environment.

| Service | Monthly cost (USD) |
|---|---|
| AWS Lambda (7 functions, 512 MB, about 3,200 invocations and 22,100 GB-seconds) | $0.37 |
| AWS Step Functions (Standard workflow, about 3,300 state transitions) | $0.08 |
| Amazon CloudWatch custom metrics (507 series published once a day, about 21 metric-months) | $6.25 |
| CloudWatch GetMetricData in the Dashboard account (about 12,500 metrics requested) | $0.13 |
| CloudWatch dashboard (1) | $3.00 |
| CloudWatch alarms (5 alarms, 7 alarm metrics) | $0.70 |
| Amazon CloudWatch Logs (8 log groups, 365-day retention) | $0.02 |
| AWS KMS customer managed keys (2, auto-created) and requests | $2.01 |
| Amazon SNS topics (2) | $0.00 |
| AWS Systems Manager Parameter Store (standard parameters, renamed accounts only) | $0.00 |
| Amazon EventBridge (scheduled rule) | $0.00 |
| CloudWatch GetMetricData and ListMetrics in the member accounts (API call counts from 17 Regions) | $18.27 |
| AWS Cost Explorer API in the management account (1 request per day, billed in us-east-1) | $0.30 |
| AWS Config advanced queries in the Config Aggregator account | $0.00 |
| **Total (all accounts, first year)** | **About $31** |

- **Where the cost lands.** About $12.56 appears in the Dashboard account (about $8.40 with free tiers). About $18.27 is spread across the member accounts, because the solution reads each account's `AWS/Usage` API call metrics with that account's role, in each Region. All charges roll up to the management account's consolidated bill.
- **Range.** The member-account figure assumes an average of 1,200 API call metric series per account per day. In testing, accounts ranged from about 400 (quiet) to about 3,200 (busy). Across 50 accounts, that puts the member-account cost at about $6 to $48 per month and the total at about $19 to $61. Each additional account adds about $0.50 per month ($0.26 to $1.10).
- **Reduce it.** List only the Regions you use in `TargetRegions`.
- **Other drivers.** Custom metrics are billed by the hour, so the estimate assumes one run per day; each extra manual run adds about $0.21. `AdminSSOPrefixes` adds one metric series per account. Automatic key rotation raises the AWS KMS key cost to $4.00 in the second year and $6.00 from the third year. Amazon GuardDuty and additional AWS CloudTrail trails also process the solution's management events, for a few cents per month.
- **Pricing Calculator input.** Enter custom metrics as metric-months: 507 series published once a day is about 21 metric-months, not 507. The calculator shows the dashboard as free, but that free tier covers only dashboards with up to 50 metrics, so this dashboard costs $3.00 per month. The calculator doesn't include the Cost Explorer API ($0.30).
