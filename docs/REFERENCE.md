# Reference: Dormant Account Detection Dashboard

This reference holds the deployment detail behind [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md): parameters, deployment behavior, updates, and cleanup. For how the solution works, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Contents

- [Deployment permissions](#deployment-permissions)
- [AWS Config aggregator](#aws-config-aggregator)
- [Parameter reference](#parameter-reference)
- [Deployment behavior](#deployment-behavior)
- [Update the deployment](#update-the-deployment)
- [Upgrade notes](#upgrade-notes)
- [Stale trust after recreating the main stack](#stale-trust-after-recreating-the-main-stack)
- [Retained resources](#retained-resources)

---

## Deployment permissions

Each account needs permission to deploy its template. The role templates create IAM roles and customer managed policies. The main stack also creates Lambda functions, a Step Functions state machine, an EventBridge rule, SNS topics, AWS KMS keys, CloudWatch alarms, a CloudWatch dashboard, and CloudWatch Logs log groups. `AdministratorAccess` works. For least privilege, use a CloudFormation service role scoped to these services and pass it with `--role-arn`.

## AWS Config aggregator

`CollectConfig` queries the aggregator named `ConfigAggregatorName` in the Config Aggregator account, in the Region where the main stack runs. The aggregator can be an organization aggregator or an account-based aggregator. It must collect data from every target account, from every Region you count (all Regions when `TargetRegions` is `[]`), and from global resources.

AWS Control Tower creates aggregators only in its home Region. Their names depend on the landing zone version:

| Landing zone | Aggregator | Account | Coverage |
|---|---|---|---|
| Before 4.0 | `aws-controltower-GuardrailsComplianceAggregator` | Audit | Enrolled accounts (account-based) |
| Before 4.0 | `aws-controltower-ConfigAggregatorForOrganizations` | Management | Whole organization |
| 4.0 and later | Service-linked aggregator | AWS Config integration account (the Audit account for existing landing zones) | Whole organization. Run [task 1.6](DEPLOYMENT_GUIDE.md#task-16-check-the-aggregator-coverage) before you deploy |

The `ConfigAggregatorName` default is the AWS Control Tower Audit account aggregator (before landing zone 4.0). For any other aggregator, set the parameter to its name. A wrong name still deploys and runs, but the run prints `"status":"FAILURE"` and every account counts in `AccountsFailed`. Check the name with [task 1.4](DEPLOYMENT_GUIDE.md#task-14-find-the-aws-config-aggregator) before you deploy.

## Parameter reference

`aws cloudformation deploy` keeps the previous value of every parameter you don't pass on an update.

### Main stack: accounts, targets, and notifications

| Parameter | Required | Default | Description |
|---|---|---|---|
| `ManagementAccountId` | ✅ | — | 12-digit AWS Organizations management account ID (AWS Organizations and AWS Cost Explorer APIs) |
| `ConfigAggregatorAccountId` | ✅ | — | 12-digit ID of the account that hosts the AWS Config aggregator. It can be any account |
| `DashboardAccountId` | — | _(empty)_ | Account where this stack and its dashboard deploy. Empty = the deploying account. If set, the `DashboardAccountMatchesCurrentAccount` rule requires it to be the deploying account. The three role templates require the same parameter |
| `TargetOuIds` | ✅* | `[]` | JSON array of OU IDs (`ou-xxxx-xxxxxxxx`), or the Root ID (`r-xxxx`) for the whole organization. Child OUs are included |
| `TargetAccountIds` | ✅* | `[]` | JSON array of account IDs to monitor in addition to OU members. Each account needs a StackSet instance. If it isn't under a StackSet target OU, deploy to it with `--deployment-targets '{"OrganizationalUnitIds":["<OU_ID>"],"Accounts":["<ACCOUNT_ID>"],"AccountFilterType":"INTERSECTION"}'` after the previous StackSet operation succeeds |
| `ExcludedAccountIds` | — | `[]` | JSON array of account IDs to skip. The management account is always skipped |
| `OrganizationId` | — | _(empty)_ | Organization ID (`o-xxxxxxxxxx`). When set, `CollectApiCallMetrics` and `CollectIAM` can assume roles only in accounts in this organization (`aws:ResourceOrgID` condition). Recommended |
| `ExternalId` | — | _(empty)_ | External ID passed as `sts:ExternalId` (`NoEcho`). Must match `ExternalId` in all three role templates. Empty = no external ID. Otherwise 2–1224 characters: letters, numbers, and `+=,.@:/_-`. Avoid commas, because the StackSet `--parameters` shorthand splits values at commas. To recover a lost value, read the `sts:ExternalId` condition from `aws iam get-role --role-name DormantAccountDetectionRole --query Role.AssumeRolePolicyDocument --profile "$MGMT_PROFILE"` |
| `NotificationEmail` | — | _(empty)_ | Email address for dormancy and alarm notifications. Empty = metrics-only mode (no SNS topics or alarms) |
| `ConfigAggregatorName` | — | `aws-controltower-GuardrailsComplianceAggregator` | Name of the AWS Config aggregator (1-256 characters: letters, numbers, `_`, and `-`). See [AWS Config aggregator](#aws-config-aggregator) |
| `CrossAccountRoleName` | — | `DormantAccountDetectionRole` | Role name in the management and target accounts (1-64 characters: letters, numbers, and `+=,.@_-`). Must match `RoleName` in the management and target account role templates |
| `ConfigAggregatorRoleName` | — | `DormantAccountDetection-ConfigAggregatorRole` | Role name in the Config Aggregator account. Must match `RoleName` in the Config Aggregator role template. It differs from `CrossAccountRoleName`, so the role doesn't clash when the Config Aggregator account is also the management account or a target account. Empty = `CrossAccountRoleName` |
| `TargetRegions` | — | `[]` | JSON array of Regions for `ApiCallCount` and the AWS Config counts. `[]` = all Regions (see [Account and Region scope](ARCHITECTURE.md#account-and-region-scope)) |
| `ScheduleExpression` | — | `cron(0 2 * * ? *)` | Collection schedule (daily at 2:00 AM UTC) |

*At least one of `TargetOuIds` or `TargetAccountIds` is required (`AtLeastOneTargetRequired` rule).

### Main stack: alarms

| Parameter | Default | Description |
|---|---|---|
| `EnableDormancyAlarms` | `true` | Create the alarms, SNS topics, and alert widgets. Ignored when `NotificationEmail` is empty |
| `EnableCollectionFailureAlarm` | `true` | `AccountsFailed` alarm (see [CloudWatch alarms](ARCHITECTURE.md#cloudwatch-alarms)) |
| `EnableStateMachineFailureAlarm` | `true` | `StateMachineFailed` alarm |
| `EnableHeartbeatAlarm` | `true` | `CollectionHeartbeat` alarm (see [CloudWatch alarms](ARCHITECTURE.md#cloudwatch-alarms)) |
| `HeartbeatWindowHours` | `26` | Hours without a completed run before the heartbeat alarm fires (2-168) |

### Main stack: encryption, logging, and capacity

| Parameter | Default | Description |
|---|---|---|
| `LogGroupKmsKeyType` | `CMK` | `CMK` or `AWSManaged` (see [CloudWatch log groups](ARCHITECTURE.md#cloudwatch-log-groups-8)) |
| `LogGroupKmsKeyArn` | _(empty)_ | Existing customer managed key ARN (CMK mode only; empty = the template creates a key) |
| `SnsKmsKeyType` | `CMK` | `CMK` or `AWSManaged`. With `AWSManaged` (`alias/aws/sns`), CloudWatch alarms can't publish to the topics, so the alarms send no email; only the dormancy emails from `PublishMetrics` arrive. A key you supply in `SnsKmsKeyArn` must allow `cloudwatch.amazonaws.com` to use `kms:Decrypt` and `kms:GenerateDataKey*` |
| `SnsKmsKeyArn` | _(empty)_ | Existing customer managed key ARN (CMK mode only; empty = the template creates a key) |
| `KmsKeyRotationPeriodDays` | `365` | Rotation period for keys the template creates (90-2560 days) |
| `EnableStateMachineEncryption` | `true` | Encrypt Step Functions execution history with the log group key |
| `StateMachineKmsDataKeyReusePeriodSeconds` | `300` | Data key reuse period (60-900; CMK mode only) |
| `LogIamPrincipalNames` | `false` | `true` makes `CollectIAM` log active IAM user names, credential types, and role names. Enable it only briefly to troubleshoot a count, because user names are often personal data |
| `LogGroupRetentionDays` | `365` | Retention for all log groups (a value CloudWatch Logs accepts, 1-3653) |
| `LambdaMemoryMB` | `512` | Memory for all Lambda functions (128-10240 MB) |
| `LambdaReservedConcurrency` | `10` | Reserved concurrency per function (1-1000). Set it to at least `MapConcurrency` |
| `MapConcurrency` | `10` | Maximum concurrency of the per-account Map states (1-40) |
| `MetricsNamespace` | `DormantAccountDetection` | Namespace for per-account metrics |
| `SfnMetricsNamespace` | `DormantAccountDetectionSfn` | Namespace for run health metrics |
| `SfnAlertNamespace` | `DormantAccountDetectionAlerts` | Namespace for dormancy alert counts |
| `DashboardName` | `DormantAccountDetectionDashboard` | CloudWatch dashboard name |

### Main stack: dormancy scoring

For how these parameters combine, see [Dormancy scoring](ARCHITECTURE.md#dormancy-scoring-hybrid-self-baselining-model).

| Parameter | Default | Description |
|---|---|---|
| `DormancyWeightSSOPresence` | `35` | Points for any non-admin SSO login |
| `DormancyWeightIAMUserPresence` | `15` | Points for any IAM user credential activity |
| `DormancyWeightApiCallTrend` | `20` | Maximum points for the API call trend |
| `DormancyWeightIAMRoleTrend` | `10` | Maximum points for the IAM role trend |
| `DormancyWeightCostTrend` | `10` | Maximum points for the cost trend |
| `DormancyWeightResourceCreatedTrend` | `10` | Maximum points for the resource creation trend |
| `LookbackDays` | `90` | Activity lookback window (1–365) |
| `NewAccountGracePeriodDays` | `30` | Skip scoring for accounts that joined fewer than this many days ago (0–365; 0 = score all) |
| `TrendWindowFraction` | `0.25` | Fraction of the lookback for each trend window (0.05–0.5) |
| `CriticalDormancyMaxScore` | `20` | Upper bound of CRITICAL (0–99) |
| `HighDormancyMaxScore` | `40` | Upper bound of HIGH (1–99) |
| `MediumDormancyMaxScore` | `60` | Upper bound of MEDIUM (2–99) |
| `LowDormancyMaxScore` | `80` | Upper bound of LOW (3–99; above = ACTIVE) |
| `AdminSSOPrefixes` | _(empty)_ | Comma-separated SSO role prefixes counted in `ActiveAdminSSOUserCount` instead of `ActiveSSOUserCount` |
| `DormantAccountsWidgetMetricWindowDays` | `14` | Lookback for the Dormant Accounts table (1–90) |
| `PriorRatingWindowDays` | `3` | Window for each account's prior rating (1–90). Use at least `14` for a weekly schedule |

Make the six weights sum to 100, because the rating bands assume a 0-100 score. Make the four band thresholds strictly increasing (see [Rating bands](ARCHITECTURE.md#rating-bands)).

### Role templates

| Parameter | Required | Default | Description |
|---|---|---|---|
| `DashboardAccountId` | ✅ | — | 12-digit Dashboard account ID |
| `RoleName` | — | `DormantAccountDetectionRole` (management and target accounts), `DormantAccountDetection-ConfigAggregatorRole` (Config Aggregator account) | Role name (1-64 characters: letters, numbers, and `+=,.@_-`). Must match `CrossAccountRoleName` (management and target accounts) or `ConfigAggregatorRoleName` (Config Aggregator account) |
| `ExternalId` | — | _(empty)_ | External ID required in the trust policy (`NoEcho`). Must match the main stack |

Each role template exports its role ARN as `<StackName>-RoleArn`. StackSet instances get generated stack names (`StackSet-<StackSetName>-<UUID>`), so their export names do too.

## Deployment behavior

- **Order.** The role templates trust the execution role ARNs that the main stack creates, so deploy the main stack first. The three role deployments can run in parallel after it.
- **StackSet.** The guide deploys and deletes StackSet instances in parallel across accounts (`--operation-preferences`), with a 10% failure tolerance. A StackSet runs one operation at a time. List a single Region in `--regions`, because the role is a global IAM resource.
- **SNS subscriptions.** Both confirmation emails come from `no-reply@sns.amazonaws.com`, with the sender names **Dormant Account Detection — CRITICAL Dormancy Alert** and **Dormant Account Detection — HIGH Dormancy Alert**. The failure and heartbeat alarms email only through the CRITICAL topic. Each email has an unsubscribe link that anyone holding the email can use. A subscription you add with `aws sns subscribe` isn't managed by the stack and goes away with the topic.
- **Run results.** Per-account collection errors don't fail the execution. They show as `PARTIAL_FAILURE` (or `FAILURE` when every account failed) with `failedCount` above 0, and in `AccountsFailed` and the per-account `CollectionFailed` metric. A missing role or an `ExternalId` mismatch in the management or Config Aggregator account fails the execution in `GetAccounts`, `CollectCostMetrics`, or `CollectConfigMetrics`. `GetAccountsFailed` always has the generic cause "Failed to resolve target accounts from AWS Organizations"; the underlying error is in the `GetAccounts` log group. For other failures, run `aws stepfunctions get-execution-history --execution-arn <arn> --reverse-order --max-results 20` to find the failing state.
- **Alarms after deployment.** `CollectionHeartbeat` starts in INSUFFICIENT_DATA and can go to ALARM before the first run, because hours without a run count as breaching. It moves to OK once CloudWatch evaluates an hour that contains a completed run. The `AccountsFailed` alarm returns to OK about a minute after each run, so its OK state doesn't prove success. Use the run output or `describe-alarm-history`.
- **`DormancyScore` series.** Accounts in the grace period, and accounts whose IAM or API call collection failed outright or whose sign-in data couldn't be collected, have no series. `list-metrics` returns series with data in the past two weeks, including series from an earlier deployment. Within 3 hours of a run, list only that run's series with `aws cloudwatch list-metrics --namespace DormantAccountDetection --metric-name DormancyScore --recently-active PT3H --region "$REGION" --profile "$DASH_PROFILE"`.
- **Custom widget.** CloudWatch invokes the widget's Lambda function with the viewer's credentials. The stack adds no resource-based policy, and `ReadOnlyAccess` and `CloudWatchReadOnlyAccess` don't include `lambda:InvokeFunction`, so viewers need the statement in the deployment guide. Without it, the rest of the dashboard renders and the widget shows an access-denied error.

## Update the deployment

In a new shell, rerun [Epic 1](DEPLOYMENT_GUIDE.md#epic-1-prepare-the-environment) with your saved external ID. Then rerun the [main stack deployment](DEPLOYMENT_GUIDE.md#task-21-deploy-the-main-stack) with your changed parameters.

To review the change set first, add `--no-execute-changeset` and run the `describe-change-set` command it prints. To apply it, run `aws cloudformation execute-change-set --change-set-name <change-set-arn> --region "$REGION" --profile "$DASH_PROFILE"`.

For the role stacks, rerun [task 3.1 or 3.2](DEPLOYMENT_GUIDE.md#epic-3-deploy-cross-account-roles). For the StackSet, run `update-stack-set` with the same `--template-body`, `--capabilities`, and `--parameters` as in [task 3.3](DEPLOYMENT_GUIDE.md#task-33-deploy-the-target-account-role). If you're upgrading from an earlier version, read [Upgrade notes](#upgrade-notes) first.

## Upgrade notes

Review the change set before you update from an earlier version (`--no-execute-changeset`).

- **New defaults.** `DashboardAccountId` is now optional, `ConfigAggregatorName` defaults to `aws-controltower-GuardrailsComplianceAggregator`, and the Config Aggregator role defaults to `DormantAccountDetection-ConfigAggregatorRole` in both `ConfigAggregatorRoleName` and the Config Aggregator role template `RoleName`. `aws cloudformation deploy` keeps your existing values, so an update changes nothing by itself. To adopt the new role name, redeploy the Config Aggregator role stack with `RoleName=DormantAccountDetection-ConfigAggregatorRole` first, then the main stack with `ConfigAggregatorRoleName=DormantAccountDetection-ConfigAggregatorRole`. A new Config Aggregator role stack next to an existing main stack needs the same main stack update.
- **Pre-hybrid stacks.** `AuditAccountId` is now `ConfigAggregatorAccountId`, and `MonitoringAccountId` is now `DashboardAccountId`, in the main stack and all three role templates. Pass `ConfigAggregatorAccountId` to the main stack and `DashboardAccountId` to the role stacks on the update. For the StackSet, pass `ParameterKey=DashboardAccountId` in `update-stack-set`. The update removes the absolute-threshold parameters (`DormancyWeight*Count`, `DormancyWeightCostIsChanging`, and `DormancyThreshold*`). Their values don't carry over to the hybrid weights, so review the scoring defaults.
- **Config Aggregator role policy.** If a new `config-aggregator-monitoring-role.yaml` changes the managed policy `Description`, CloudFormation must replace the fixed-name `DormantAccountDetection-ConfigAggregatorReadPolicy`, and the update can't complete in place. Delete the Config Aggregator role stack and create it again, between scheduled runs.
- **All-Region default.** With `TargetRegions` at `[]`, `ApiCallCount` and the AWS Config counts now cover every Region, and `ApiCallCount` counts every `AWS/Usage` `CallCount` series instead of at most 500 per Region. Values rise after the update. A rise doesn't lower a trend ratio (ratios are capped at 1.0), and trends settle once both windows hold the new data.

## Stale trust after recreating the main stack

IAM stores each role in a trust policy by its unique ID. A recreated execution role keeps its name but gets a new ID, so the cross-account trust policies still point at the deleted role, and the first run fails with `AccessDenied` on `sts:AssumeRole`. `aws iam get-role --role-name <role>` then shows `AROA...` IDs instead of role ARNs. The role is `DormantAccountDetectionRole` in the management and target accounts and `DormantAccountDetection-ConfigAggregatorRole` in the Config Aggregator account. Redeploying a role stack with unchanged parameters doesn't fix it, because CloudFormation sees no change.

To fix it, recreate all stacks in order ([Epic 6](DEPLOYMENT_GUIDE.md#epic-6-clean-up-resources), then Epics 2-5), or rewrite each trust:

- **Update twice.** Rerun the management or Config Aggregator role deployment with a temporary `ExternalId`, then again with the original value. For the StackSet, do the same with `update-stack-set`. Runs fail between the two updates.
- **Recreate one role stack.** Delete it, wait for `DELETE_COMPLETE`, and run its step again.
- **Edit in place.** Save the trust with `aws iam get-role --role-name <role> --query Role.AssumeRolePolicyDocument --output json > trust.json`. Replace each `AROA...` principal with the role ARN that matches its `Sid` (`AllowGetAccountsAssume`, `AllowCollectCostAssume`, or `AllowCollectConfigAssume`, each mapping to `arn:aws:iam::<DASHBOARD_ACCOUNT_ID>:role/DormantAccountDetection-<Function>-Role`). Keep any `sts:ExternalId` condition. Apply it with `aws iam update-assume-role-policy --role-name <role> --policy-document file://trust.json`, then delete `trust.json`, because it contains the external ID.

## Retained resources

- **Log groups.** The eight log groups have fixed names and `DeletionPolicy: Retain`. Delete them after the stack reaches `DELETE_COMPLETE`, or the next main stack deployment fails because the names exist. To keep the logs, export them first with `aws logs create-export-task`.
- **AWS KMS keys.** Keys the template creates (key type `CMK` with no key ARN supplied) have `DeletionPolicy: Retain`. Schedule them for deletion after the log groups, because the log groups use the log group key. A deletion can be cancelled during the 7-30 day window with `aws kms cancel-key-deletion` (the key comes back disabled). A re-install creates new keys. If you lost the key ARNs, find the keys with `aws kms list-keys` and `aws kms describe-key`: schedule only a key with `KeyManager` `CUSTOMER`, `KeyState` `Enabled`, a `Description` that starts with "AWS KMS customer managed key for encrypting DormantAccountDetection", and a `CreationDate` that matches the deployment. Never schedule a key you supplied.
- **Account-name parameters.** `PublishMetrics` creates `/DormantAccountDetection/AccountNames/<accountId>` parameters at runtime for renamed accounts, so they survive a stack delete.
- **CloudWatch metrics.** CloudWatch has no API to delete metric data. Series stop appearing in `list-metrics` about two weeks after their last datapoint and expire under the CloudWatch retention schedule (up to 15 months). A re-install with the same `MetricsNamespace` reads that history: data within `LookbackDays` counts toward the trends, and a `DormancyRating` within `PriorRatingWindowDays` becomes the prior rating, so the first run can send dormancy emails. For a clean baseline, deploy with new namespace values.
- **Earlier stack names.** To find stacks from a deployment that used other names, run `aws cloudformation describe-stack-resources --physical-resource-id DormantAccountDetection-GetAccounts-Role --query 'StackResources[0].StackName'` in the Dashboard account, the same with `--physical-resource-id DormantAccountDetectionRole` in the management account and `DormantAccountDetection-ConfigAggregatorRole` (or `DormantAccountDetectionRole` for earlier deployments) in the Config Aggregator account, and `aws cloudformation list-stack-sets --status ACTIVE` in the management account.
