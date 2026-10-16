# Security

## Disclaimer

This project is sample/pattern code intended to demonstrate a serverless dormant account detection solution on AWS. It is **not production-ready** without additional security hardening, operational review, and adaptation to your organization's specific requirements and compliance posture.

This pattern accompanies the AWS Prescriptive Guidance (APG) pattern *Automate dormant account detection across an AWS Organization*.

Before deploying this solution in a production environment, review the [Production Hardening Recommendations](#production-hardening-recommendations) section below and engage your security team.

---

## Reporting Security Vulnerabilities

**Do NOT open a public GitHub issue to report a security vulnerability.**

If you discover a potential security issue in this project, please report it to the AWS Security team at:

**[aws-security@amazon.com](mailto:aws-security@amazon.com)**

For more information on the AWS vulnerability reporting process, see:
[https://aws.amazon.com/security/vulnerability-reporting/](https://aws.amazon.com/security/vulnerability-reporting/)

---

## AWS Services Used

This solution deploys and interacts with the following AWS services:

| Service | Purpose in This Solution |
|---------|--------------------------|
| **AWS Lambda** | Six functions orchestrated by Step Functions: `GetAccounts` (enumerates accounts in the Organization), `CollectApiCallMetrics` (collects CloudWatch Usage metrics), `CollectIAM` (collects IAM credential reports and authorization details), `CollectConfig` (collects resource inventory via AWS Config aggregator), `CollectCost` (collects monthly cost data via Cost Explorer), and `PublishMetrics` (writes dormancy metrics to CloudWatch and sends dormancy notifications). A seventh function backs the dashboard's Dormant Accounts custom widget. |
| **AWS Step Functions** | Standard workflow that orchestrates the full data collection pipeline, invoking Lambda functions in sequence with Retry and Catch error handling |
| **Amazon EventBridge** | Scheduled rule that triggers the Step Functions state machine daily at 2:00 AM UTC |
| **Amazon CloudWatch** | Stores custom dormancy metrics, hosts the operational dashboard, provides log groups for Lambda and Step Functions, shows dormancy-count alarm status, and raises alarms on collection failures, state machine failures, and missed runs (heartbeat) |
| **Amazon SNS** | Delivers email notifications to a subscribed address: dormancy notifications that the `PublishMetrics` function publishes when accounts newly fall into the CRITICAL or HIGH bands, and the collection-failure, state machine failure, and heartbeat alarms |
| **AWS KMS** | Encrypts CloudWatch log groups and Step Functions execution history (`LogGroupKmsKey`) and SNS topics (`SnsKmsKey`) using customer managed keys (configurable) |
| **AWS Organizations** | Queried by the `GetAccounts` Lambda to enumerate all accounts in the Organization |
| **AWS Cost Explorer** | Queried by the `CollectCost` Lambda to retrieve monthly cost data per account |
| **AWS Config** | Queried by the `CollectConfig` Lambda through the AWS Config aggregator in the Config Aggregator Account to retrieve resource inventory counts per account |
| **AWS IAM** | Queried by the `CollectIAM` Lambda to generate credential reports and retrieve authorization details (last-used timestamps for users and roles) |
| **AWS Systems Manager Parameter Store** | Stores the account-name registry (`/DormantAccountDetection/AccountNames/<accountId>`) that the `PublishMetrics` Lambda reads and updates so renamed accounts keep their trend history |

---

## Prerequisites and Permissions

Deploying and operating this solution requires elevated permissions across three AWS accounts:

| Account | Required Permission Level | Purpose |
|---------|--------------------------|---------|
| **Dashboard Account** | `AdministratorAccess` (or equivalent) | Deploy the main CloudFormation stack; Lambda execution roles assume cross-account roles in the Management and Config Aggregator Accounts |
| **Management Account** | `AdministratorAccess` (or equivalent) | Deploy the management account monitoring role stack; the role it creates lets the `GetAccounts` and `CollectCost` execution roles read AWS Organizations account and OU data and call `ce:GetCostAndUsage` |
| **Config Aggregator Account** (Management, Audit, or any other account that hosts the aggregator) | `AdministratorAccess` (or equivalent) | Deploy the Config Aggregator monitoring role stack; the role it creates lets the `CollectConfig` execution role call `config:SelectAggregateResourceConfig` on the aggregator |

An **AWS Config Aggregator** must be configured and active in the Config Aggregator Account before deploying this solution. The `CollectConfig` Lambda queries the aggregator to retrieve resource inventory data across all accounts.

Refer to [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) for step-by-step deployment instructions.

---

## Known Security Considerations

The maintainers keep a full STRIDE threat model for this pattern. It covers 17 threats and 27 mitigations across 7 trust zones, with code-validated evidence and a residual-risk decision for every threat. The following table summarizes the findings that matter most to operators.

The findings below fall into two groups: **accepted design decisions** appropriate for this sample pattern, and **open items** (flagged ⚠️) that apply to a default deployment and that an operator should remediate before production use.

| Finding | Severity | Status | Rationale / Accepted Risk or Remediation |
|---------|----------|--------|------------------------------------------|
| ⚠️ Inline Lambda code, no signing or update restriction | CRITICAL | **Open** | All Lambda code is inline (`ZipFile`), and the templates don't restrict `cloudformation:UpdateStack`, `lambda:UpdateFunctionCode`, or `iam:PassRole` on the execution roles. Anyone who can change the stack or a function inherits the solution's organization-wide read access. **Remediation:** use SCPs or permissions boundaries so only a dedicated deployment role can update the stacks and functions, enable stack termination protection, and deploy from a pinned commit. (Threats T001, T009.) |
| ⚠️ Anyone with the email can unsubscribe | MEDIUM | **Open** | Every SNS email includes an unsubscribe link that needs no sign-in. Following it, or a mail gateway that follows links, removes the only notification path, including the failure and heartbeat alarms. **Remediation:** use a team distribution list, alert on SNS `Unsubscribe`, and check subscription status periodically. (Threat T016.) |
| Org-wide reconnaissance aggregation | HIGH | Accepted | Aggregation is the purpose of the solution: the Dashboard account holds a single view of every account's IAM, SSO, API, resource, and cost activity. `CollectIAM` returns and logs only counts by default; it logs IAM user and role names only when `LogIamPrincipalNames=true`. Logs and execution history are encrypted with a customer managed key by default. **Mitigation:** deploy the Dashboard account as a dedicated account in a security OU, limit and audit human access, and enforce MFA. See Production Hardening Recommendations below. (Threat T002.) |
| Member account owners can opt out of scoring | MEDIUM | Accepted | A member account administrator can delete or restrict `DormantAccountDetectionRole`. The account then publishes only `CollectionFailed=1` and is never rated; the `AccountsFailed` alarm reports it on every run. **Recommendation:** protect the role with an SCP that allows changes only from the StackSet execution role. (Threat T005.) |
| Disabled schedule or removed alerting | MEDIUM | Accepted | A Dashboard account administrator can disable the EventBridge rule, change band thresholds, or remove the email subscription. When alarms are enabled, the `DormantAccountDetection-CollectionHeartbeat` alarm fires when no run completes within `HeartbeatWindowHours` (default 26). **Recommendation:** restrict changes to the solution (see the first row) and alert on `DisableRule` and SNS `Unsubscribe`. (Threat T006.) |
| Confused-deputy protection off by default | LOW | Accepted | Member roles trust only the two collector role ARNs, but the collectors' identity policies allow `sts:AssumeRole` on `arn:aws:iam::*:role/<name>` and the `aws:ResourceOrgID` condition applies only when `OrganizationId` is set. Collectors reject or drop any `accountId` that isn't 12 digits, and take the role names, ExternalId, Management and Config Aggregator account IDs, and aggregator name only from their environment variables, never from the invocation payload. Someone who can invoke a collector directly can choose which account to read, but not which role to assume; only `OrganizationId` keeps that account inside the organization. **Recommendation:** set `OrganizationId` and an `ExternalId` in all four templates. (Threat T008.) |
| Dormancy-score evasion by synthetic activity | MEDIUM | Accepted | An account owner who knows the scoring model can keep an unused account ACTIVE: one non-admin IAM Identity Center sign-in per lookback window plus steady background activity scores 85. The model detects declining activity, not sustained flat dormancy. Review accounts that stay in MEDIUM for long periods, and confirm dormancy with owners before closing accounts. (Threat T004.) |
| Forged scores or notifications | LOW | Accepted | A principal in the Dashboard account with `lambda:InvokeFunction` on `PublishMetrics`, `cloudwatch:PutMetricData` on the solution namespaces, or `sns:Publish` on the topics can inject false scores or emails. Lambda invocations and `PutMetricData` are CloudTrail data events that aren't logged by default. Enable data events for attribution. (Threat T003.) |
| `EXTERNAL_ID` in Lambda environment variables | LOW | Accepted | `ExternalId` is optional and empty by default. When set, it provides confused-deputy protection on `sts:AssumeRole`. [AWS IAM does not treat the external ID as a secret](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html): it is visible in the role trust policy and in AWS CloudTrail. It is set only on the five functions that assume cross-account roles, encrypted at rest with the AWS managed key for AWS Lambda, `NoEcho` in all four templates, and isn't passed in the Step Functions payload. Moving it to AWS Systems Manager Parameter Store SecureString would add a manual step before every deployment, because AWS CloudFormation cannot create SecureString parameters, without adding protection for a value that is not a secret. (Threat T007.) |
| Notification email address (personal data) | LOW | Accepted | Supplied as an AWS CloudFormation parameter, not hardcoded in the template, and stored only in the stack parameters and the two Amazon SNS email subscriptions. No Lambda function has it in its environment variables, and it isn't passed in the Step Functions payload. Principals with `cloudformation:DescribeStacks` or `sns:ListSubscriptionsByTopic` in the Dashboard account can read it. Use a team distribution list rather than a personal address. (Threats T007, T013.) |
| Account email (PII) flows through the pipeline | MEDIUM | **Mitigated** | `GetAccounts` `map_org_account()` doesn't return the `email` field, so account email addresses stay out of the Step Functions execution payload, execution history, and CloudWatch Logs. (Mitigation M007.) |
| Renaming an account to reset its score history | MEDIUM | **Mitigated** | Metrics carry `AccountId` and `AccountName`, so a rename starts a new series. `PublishMetrics` detects renames from recent `CollectionFailed` series and records each renamed account's names in the Parameter Store parameter `/DormantAccountDetection/AccountNames/<accountId>`; trend history and prior ratings are read across all recorded names. A rename that happens while collection is stopped for more than two weeks isn't linked. (Threat T015.) |
| `ssm:GetParametersByPath` on the `PublishMetrics` role (KICS "IAM policy allows for data exfiltration") | LOW | Accepted | The `AccountNameRegistry` statement grants `ssm:GetParametersByPath` and `ssm:PutParameter` only on `/DormantAccountDetection/AccountNames` and the parameters under it, in the Dashboard account and Region. The parameters are `String` values holding account names, which are already published as the `AccountName` dimension on every solution metric, and the role has no `kms:Decrypt` for SSM, so it can't read `SecureString` values. The permission is required for rename tracking (Threat T015). The KICS rule flags the action regardless of `Resource`, so the statement carries a `# kics-scan ignore-block` comment with this justification. |
| Collection failures and the management account | MEDIUM | **Mitigated** | The `AccountsFailed` and `StateMachineFailed` alarms use `TreatMissingData: notBreaching`, so they return to `OK` between runs and notify on every failing run. `GetAccounts` removes the management account from the target list, because the member role can't exist there. When a collector can't collect a signal, `PublishMetrics` skips scoring if a sign-in (presence) signal is missing and scores a missing trend signal as neutral, and doesn't write the missing value to the metric history, so a partial failure can't lower a score or send a false dormancy email. (Threats T012, T014, T017.) |
| Account names rendered in the dashboard | MEDIUM | **Mitigated** | Account names are set by account owners. The custom widget escapes every account name and ID with `html.escape`, and SNS emails are plain text. (Threat T010.) |
| CloudWatch log groups deleted with the stack | HIGH | **Mitigated** | All Lambda and Step Functions log groups use `DeletionPolicy: Retain` and `UpdateReplacePolicy: Retain`, so deleting or replacing the stack keeps the audit trail. Delete the retained log groups manually when you no longer need them. (Mitigation M019.) |
| Step Functions execution history encryption | MEDIUM | **Mitigated** | `EnableStateMachineEncryption` (default `true`) sets `EncryptionConfiguration` on the state machine with the CloudWatch Logs customer managed key, so execution input and output in execution history are encrypted at rest with a key you control. The payload carries account IDs, names, and counts, not the ExternalId or notification email. The state machine uses an AWS owned key when `EnableStateMachineEncryption=false` or `LogGroupKmsKeyType=AWSManaged`. (Mitigation M006.) |
| AWS KMS key policies delegate to IAM in the account | LOW | Accepted | Both template-created customer managed keys (`LogGroupKmsKey`, `SnsKmsKey`) replace the single `kms:*` root statement with a key administrators statement (management actions only, following the [AWS KMS default key policy](https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-default.html)) and a key users statement (cryptographic actions only, limited by `kms:CallerAccount`). Every statement scopes `Resource` to `arn:<partition>:kms:<region>:<account>:key/*`. Both statements keep the account principal so IAM policies stay in control and the key can't become unmanageable. To narrow further, replace the account principal with named administrator and user roles, and alert on `DisableKey` and `ScheduleKeyDeletion`. (Threat T011.) |
| Cross-account roles shared by two functions | LOW | Accepted | The Config Aggregator role grants only `config:SelectAggregateResourceConfig`. The management account role is shared by `GetAccounts` and `CollectCost`, and the member role by `CollectIAM` and `CollectApiCallMetrics`, so each function in a pair can use the other's read permissions. All permissions are read-only. (Threat T001.) |
| Lambda functions not in VPC | LOW | Accepted | All Lambda functions call public AWS API endpoints (Organizations, STS, CloudWatch, IAM, Cost Explorer, Config). VPC placement would require NAT Gateways or multiple VPC Interface Endpoints, adding significant cost with no security benefit for this use case. |
| X-Ray tracing disabled | LOW | Accepted | This is a scheduled batch job (daily at 2:00 AM UTC). Health is monitored via CloudWatch Logs and custom Step Functions metrics. X-Ray adds cost with no operational benefit for non-latency-sensitive background jobs. The KICS check (9488c451-074e-4cd3-aee3-7db6104f542c) is disabled with a `# kics-scan disable` comment at the top of the main stack template. |
| No Dead Letter Queue on Lambda functions | LOW | Accepted | The six pipeline functions are invoked synchronously by Step Functions, and the custom widget function is invoked synchronously by CloudWatch dashboards. DLQs only capture failures from asynchronous invocations. Synchronous failures are returned directly to the caller and, in the pipeline, handled by the Retry and Catch policies in the state machine definition. The KICS check (c2eae442-d3ba-4cb1-84ca-1db4f80eae3d) is disabled with a `# kics-scan disable` comment at the top of the main stack template. |

---

## Production Hardening Recommendations

Before deploying this solution in a production environment, consider the following hardening steps:

- **Enable AWS CloudTrail in all Regions** for the Dashboard Account to capture a complete audit trail of all API activity, including Step Functions executions. Lambda invocations and CloudWatch `PutMetricData` calls are data events; enable data event logging for the solution's functions if you need to attribute them.
- **Consider VPC placement for Lambda functions** if your organization requires network isolation. This would require NAT Gateways or VPC Interface Endpoints for each AWS service called (Organizations, STS, CloudWatch, IAM, Cost Explorer, Config).
- **Enable stack termination protection** on the solution stacks to prevent accidental teardown of the monitoring infrastructure.
- **Replace the account principal in the AWS KMS key administrators and key users statements with named IAM roles** if your organization requires key access to be limited beyond IAM policies in the account.
- **Use a high-entropy `ExternalId` and set `OrganizationId`** at deployment time so the cross-account `sts:AssumeRole` path is bound to your organization.
- **Review and tighten IAM role trust policies** to use the most restrictive conditions available for your environment (e.g., `aws:PrincipalArn`, `sts:ExternalId`).
- **Protect `DormantAccountDetectionRole` in member accounts with an SCP** that allows changes to the role only from the StackSet execution role, so account owners can't opt out of scoring.
- **Keep `LogIamPrincipalNames` set to `false`** except while troubleshooting, and shorten retention on `/aws/lambda/DormantAccountDetection-CollectIAM` if you enable it.
- **Enable AWS Security Hub and AWS Config rules in the Dashboard Account** to continuously assess the security posture of the deployed resources.
- **Deploy the Dashboard Account in a dedicated Security OU with restrictive SCPs.** This solution aggregates IAM, API activity, resource inventory, and cost data for every account in the organization into a single CloudWatch namespace. An attacker who compromises the dashboard account gains read access to this aggregated intelligence without needing to pivot to individual accounts. Mitigate this by: (1) placing the dashboard account in a dedicated Security OU with SCPs that deny all non-essential actions; (2) enforcing MFA for all human access to the dashboard account; (3) limiting the number of IAM principals with access to the dashboard account; (4) enabling CloudTrail with log file validation in all Regions; (5) regularly auditing access to the dashboard account and its CloudWatch data with IAM Access Analyzer and IAM last accessed information.

---

## Data Handling

### Data Collected

This solution collects the following data from your AWS Organization:

- AWS account IDs and account names (from AWS Organizations). Account email addresses are not collected (Mitigation M007).
- Account `JoinedTimestamp` (from AWS Organizations) — used to apply the new-account grace period
- IAM user and role last-activity timestamps (from IAM credential reports and authorization details). Metrics and logs hold counts only. When `LogIamPrincipalNames=true`, the `CollectIAM` log group also records the names of active IAM users, their active credential types, and active role names, including IAM Identity Center permission set roles
- SSO role usage, with admin/maintenance roles (matching `AdminSSOPrefixes`) counted separately from human user activity
- API call counts per account (from CloudWatch Usage metrics)
- Resource inventory counts per account (from the AWS Config aggregator)
- Monthly cost data per account (from AWS Cost Explorer) — evaluated as a self-relative trend over the account's own history, not against a fixed cross-account threshold

### Data Flow

Lambda functions query AWS service APIs → results are published as custom CloudWatch metrics → metrics are visualized on the CloudWatch Dashboard in the Dashboard Account.

Data is stored only in CloudWatch metrics, CloudWatch Logs, Step Functions execution history, and (for renamed accounts) Parameter Store `String` parameters under `/DormantAccountDetection/AccountNames` in the Dashboard Account, and in the SNS email notifications sent to the subscribed address. No data is written to S3, DynamoDB, or any other system.

### Encryption at Rest

| Resource | Encryption |
|----------|-----------|
| CloudWatch log groups | Encrypted with an AWS KMS customer managed key (`LogGroupKmsKey`) by default. With `LogGroupKmsKeyType=AWSManaged`, CloudWatch Logs uses its default server-side encryption (no AWS KMS key) |
| Step Functions execution history | Encrypted with `LogGroupKmsKey` by default; an AWS owned key when `EnableStateMachineEncryption=false` or `LogGroupKmsKeyType=AWSManaged` |
| SNS topics | Encrypted with an AWS KMS customer managed key (`SnsKmsKey`) by default, or the AWS managed key for Amazon SNS (`alias/aws/sns`) if configured |
| Lambda environment variables | Encrypted with the AWS managed key for AWS Lambda |
| CloudWatch Metrics | Encrypted by AWS using AWS managed keys (not configurable) |

### Encryption in Transit

- SNS topics deny publishing without TLS through an `aws:SecureTransport` deny statement in the topic policy.
- All Lambda function calls to AWS service APIs use HTTPS (enforced by the AWS SDK).

---

## Resource Cleanup / Teardown

To remove all resources deployed by this solution, follow [Epic 6: Clean up resources](docs/DEPLOYMENT_GUIDE.md#epic-6-clean-up-resources) in the deployment guide.

> **Important:** The KMS customer managed keys (`LogGroupKmsKey` and `SnsKmsKey`) are created with `DeletionPolicy: Retain`. They are **not** deleted when the CloudFormation stack is deleted. After stack deletion, schedule these keys for deletion via the [AWS KMS console](https://console.aws.amazon.com/kms) or the AWS CLI. AWS KMS keys have a mandatory waiting period of 7–30 days before deletion is finalized.
>
> **Log groups are retained on teardown:** the CloudWatch log groups use `DeletionPolicy: Retain`, so deleting the stack keeps the Lambda and Step Functions logs (Mitigation M019). Delete the log groups manually when you no longer need them.

---

## Dependencies

This solution has minimal external dependencies:

| Dependency | Version | Notes |
|------------|---------|-------|
| **boto3** | Lambda runtime default | AWS SDK for Python — included in the Python 3.14 Lambda runtime. No separate installation required. |
| **Python standard library** | 3.14 | `json`, `csv`, `io`, `os`, `time`, `datetime` — no external packages or Lambda layers required. |
| **AWS CloudFormation** | — | Used to deploy all resources. No CDK, Terraform, or other IaC tooling is required. |
| **Amazon S3** | — | Used only to stage the main CloudFormation template at deploy time, because the template exceeds the 51,200-byte direct-upload limit (the console creates a staging bucket automatically; the AWS CLI needs `--s3-bucket`). Lambda code is inline in the template, so no Lambda deployment package is stored. |
| **External build pipeline** | — | Not required. The solution deploys directly from the CloudFormation template with no build or packaging step. |
