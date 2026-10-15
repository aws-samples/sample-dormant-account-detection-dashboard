# Deployment Guide: Dormant Account Detection Dashboard

This guide deploys the four Dormant Account Detection Dashboard templates with the AWS CLI, runs and verifies the first collection, and tears everything down. Deployment takes about 15 minutes for a few target accounts, and removal takes about 8.

Each epic and task matches an epic and story in the AWS Prescriptive Guidance (APG) pattern. For prerequisites and best practices, see the [README](../README.md). For parameters and deployment behavior, see [REFERENCE.md](REFERENCE.md).

| Epic | What you do | Account | Time |
|---|---|---|---|
| [1. Prepare the environment](#epic-1-prepare-the-environment) | Check prerequisites and set shell variables | All three | 3 min |
| [2. Deploy the main dashboard stack](#epic-2-deploy-the-main-dashboard-stack) | `DormantAccountDetection-Dashboard` | Dashboard | 5 min |
| [3. Deploy cross-account roles](#epic-3-deploy-cross-account-roles) | `DormantAccountDetection-ManagementRole`, `-ConfigAggregatorRole`, and the `-TargetAccountRole` StackSet | Management, Config Aggregator | 5 min |
| [4. Configure alerting](#epic-4-configure-alerting) | Confirm the subscriptions and test an email | Dashboard | 1 min |
| [5. Validate the deployment](#epic-5-validate-the-deployment) | Run the first collection and check the results | Dashboard | 3 min |
| [6. Clean up resources](#epic-6-clean-up-resources) | Delete the stacks and retained resources | All three | 8 min |

Run Epic 2 before Epic 3, and Epic 5 after Epic 3 succeeds. [Troubleshooting](#troubleshooting) is at the end.

---

## Epic 1: Prepare the environment

Skills required: AWS administrator

### Task 1.1: Check the prerequisites

Confirm each item in [Prerequisites](../README.md#prerequisites) and the four decisions in [Best practices](../README.md#best-practices).

### Task 1.2: Set the Region and the profiles

Run every command from the repository root, in one shell session, so these variables stay available. Set `REGION` to the Region that holds your AWS Config aggregator (with AWS Control Tower, the home Region), and replace the profile names. The first line lets zsh accept the `#` comments in later commands, and `AWS_PAGER` stops long output from opening a pager.

```bash
[ -n "$ZSH_VERSION" ] && setopt interactivecomments
export AWS_PAGER=""
export REGION=eu-west-1
export DASH_PROFILE='<dashboard-account-profile>'
export MGMT_PROFILE='<management-account-profile>'
export CFG_PROFILE='<config-aggregator-account-profile>'
```

Expect no output.

### Task 1.3: Look up the account IDs and the Organization ID

```bash
export DASHBOARD_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --profile "$DASH_PROFILE")
export MANAGEMENT_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --profile "$MGMT_PROFILE")
export CONFIG_AGGREGATOR_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --profile "$CFG_PROFILE")
export ORGANIZATION_ID=$(aws organizations describe-organization --query Organization.Id --output text --profile "$MGMT_PROFILE")
echo "$DASHBOARD_ACCOUNT_ID $MANAGEMENT_ACCOUNT_ID $CONFIG_AGGREGATOR_ACCOUNT_ID $ORGANIZATION_ID"
```

Expect four values, in the order Dashboard, management, Config Aggregator, and `o-xxxxxxxxxx`. If a command prints `Token has expired` or `Unable to locate credentials`, refresh that profile (for example, `aws sso login --profile <profile>`) and repeat this task.

### Task 1.4: Find the AWS Config aggregator

```bash
export CONFIG_AGGREGATOR_NAME=$(aws configservice describe-configuration-aggregators \
  --query 'ConfigurationAggregators[0].ConfigurationAggregatorName' \
  --output text --region "$REGION" --profile "$CFG_PROFILE")
echo "$CONFIG_AGGREGATOR_NAME"
```

Expect an aggregator name. `None` means `REGION` or `CFG_PROFILE` points at the wrong place: fix task 1.2 and repeat tasks 1.3 and 1.4. If the account has more than one aggregator, list them with `--query 'ConfigurationAggregators[].ConfigurationAggregatorName'` and set the one that covers your target accounts with `export CONFIG_AGGREGATOR_NAME=<name>`. For AWS Control Tower names, see [AWS Config aggregator](REFERENCE.md#aws-config-aggregator).

### Task 1.5: Set the target OUs

List the top-level OUs, then set the ones to monitor as a JSON array. Child OUs are included. To monitor the whole organization, set the Root ID instead.

```bash
ROOT_ID=$(aws organizations list-roots --query 'Roots[0].Id' --output text --profile "$MGMT_PROFILE")
aws organizations list-organizational-units-for-parent --parent-id "$ROOT_ID" \
  --query 'OrganizationalUnits[].[Id,Name]' --output text --profile "$MGMT_PROFILE"
export TARGET_OU_IDS='["ou-xxxx-aaaaaaaa","ou-xxxx-bbbbbbbb"]'
# Whole organization instead: export TARGET_OU_IDS="[\"$ROOT_ID\"]"
```

Expect one line per top-level OU, with its ID and name.

### Task 1.6: Check the aggregator coverage

```bash
aws configservice select-aggregate-resource-config \
  --configuration-aggregator-name "$CONFIG_AGGREGATOR_NAME" \
  --expression "SELECT accountId, COUNT(*) GROUP BY accountId" \
  --query 'Results' --region "$REGION" --profile "$CFG_PROFILE"
```

Expect one result for each account in your target OUs, each with a resource count. An account that's missing reports 0 resources and can look dormant.

### Task 1.7: Set the email and the external ID

```bash
export NOTIFICATION_EMAIL=cloud-ops@example.com
export EXTERNAL_ID=$(openssl rand -hex 16)
```

Expect no output. Print the external ID with `echo "$EXTERNAL_ID"` and save it in your password manager. The stacks hide it, and you need it to update them. In a later shell session, repeat Epic 1 but set the saved value with `export EXTERNAL_ID='<saved-external-id>'` instead of running `openssl`.

### Task 1.8: Set the template staging bucket

CloudFormation stages the main template in an Amazon S3 bucket in the Dashboard account and Region. These commands create the bucket if it doesn't exist. To use an existing bucket, set its name instead.

```bash
export TEMPLATE_BUCKET="dormant-account-detection-cfn-${DASHBOARD_ACCOUNT_ID}-${REGION}"
aws s3api head-bucket --bucket "$TEMPLATE_BUCKET" --profile "$DASH_PROFILE" 2>/dev/null \
  || aws s3 mb "s3://$TEMPLATE_BUCKET" --region "$REGION" --profile "$DASH_PROFILE"
aws s3api get-bucket-location --bucket "$TEMPLATE_BUCKET" --query LocationConstraint --output text --profile "$DASH_PROFILE"
```

Expect `make_bucket: ...` on the first run, then your Region (`None` means `us-east-1`).

### Task 1.9: Check trusted access for StackSets

```bash
aws cloudformation describe-organizations-access --query Status --output text --region "$REGION" --profile "$MGMT_PROFILE"
```

Expect `ENABLED`. If it prints `DISABLED`, run `aws cloudformation activate-organizations-access --region "$REGION" --profile "$MGMT_PROFILE"`.

### Task 1.10: Check the Lambda concurrency quota

The 7 functions each reserve 10 concurrent executions (`LambdaReservedConcurrency`), and Lambda keeps 100 unreserved.

```bash
aws lambda get-account-settings \
  --query '[AccountLimit.ConcurrentExecutions,AccountLimit.UnreservedConcurrentExecutions]' \
  --output text --region "$REGION" --profile "$DASH_PROFILE"
```

Expect a second number of at least 170. If it's lower, request a higher quota with `aws service-quotas request-service-quota-increase --service-code lambda --quota-code L-B99A9384 --desired-value 1000 --region "$REGION" --profile "$DASH_PROFILE"` and wait for approval before task 2.1.

---

## Epic 2: Deploy the main dashboard stack

Skills required: AWS administrator

### Task 2.1: Deploy the main stack

```bash
aws cloudformation deploy \
  --template-file cloudformation/dormant-account-detection-dashboard-stack.yaml \
  --stack-name DormantAccountDetection-Dashboard \
  --s3-bucket "$TEMPLATE_BUCKET" --s3-prefix dormant-account-detection \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" --profile "$DASH_PROFILE" \
  --parameter-overrides \
    ManagementAccountId="$MANAGEMENT_ACCOUNT_ID" \
    ConfigAggregatorAccountId="$CONFIG_AGGREGATOR_ACCOUNT_ID" \
    ConfigAggregatorName="$CONFIG_AGGREGATOR_NAME" \
    OrganizationId="$ORGANIZATION_ID" \
    TargetOuIds="$TARGET_OU_IDS" \
    ExternalId="$EXTERNAL_ID" \
    NotificationEmail="$NOTIFICATION_EMAIL"
```

Expect `Successfully created/updated stack - DormantAccountDetection-Dashboard` after about 5 minutes. If it prints `Failed to create/update the stack`, see the first two rows of [Troubleshooting](#troubleshooting). The defaults for the other parameters suit a first deployment. To change them, see [Parameter reference](REFERENCE.md#parameter-reference).

The daily schedule (2:00 AM UTC) starts now. Finish Epic 3 before then, so the first scheduled run succeeds.

---

## Epic 3: Deploy cross-account roles

Skills required: AWS administrator

Run the three tasks in any order.

### Task 3.1: Deploy the management account role

```bash
aws cloudformation deploy \
  --template-file cloudformation/management-account-monitoring-role.yaml \
  --stack-name DormantAccountDetection-ManagementRole \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" --profile "$MGMT_PROFILE" \
  --parameter-overrides \
    DashboardAccountId="$DASHBOARD_ACCOUNT_ID" \
    ExternalId="$EXTERNAL_ID"
```

Expect `Successfully created/updated stack` after about 1 minute.

### Task 3.2: Deploy the Config Aggregator account role

```bash
aws cloudformation deploy \
  --template-file cloudformation/config-aggregator-monitoring-role.yaml \
  --stack-name DormantAccountDetection-ConfigAggregatorRole \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" --profile "$CFG_PROFILE" \
  --parameter-overrides \
    DashboardAccountId="$DASHBOARD_ACCOUNT_ID" \
    ExternalId="$EXTERNAL_ID"
```

Expect `Successfully created/updated stack` after about 1 minute.

### Task 3.3: Deploy the target account role

Run these commands in the management account. They create a service-managed StackSet, deploy the role to every account in `TARGET_OU_IDS`, and wait until the deployment finishes.

```bash
aws cloudformation create-stack-set \
  --stack-set-name DormantAccountDetection-TargetAccountRole \
  --template-body file://cloudformation/stackset-target-accounts-monitoring-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --permission-model SERVICE_MANAGED \
  --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false \
  --parameters \
    ParameterKey=DashboardAccountId,ParameterValue="$DASHBOARD_ACCOUNT_ID" \
    ParameterKey=ExternalId,ParameterValue="$EXTERNAL_ID" \
  --region "$REGION" --profile "$MGMT_PROFILE"

OPERATION_ID=$(aws cloudformation create-stack-instances \
  --stack-set-name DormantAccountDetection-TargetAccountRole \
  --deployment-targets OrganizationalUnitIds="$TARGET_OU_IDS" \
  --regions "$REGION" \
  --operation-preferences RegionConcurrencyType=PARALLEL,MaxConcurrentPercentage=100,FailureTolerancePercentage=10 \
  --query OperationId --output text \
  --region "$REGION" --profile "$MGMT_PROFILE")

# Wait for the operation (expect SUCCEEDED)
until s=$(aws cloudformation describe-stack-set-operation \
    --stack-set-name DormantAccountDetection-TargetAccountRole \
    --operation-id "$OPERATION_ID" \
    --query StackSetOperation.Status --output text \
    --region "$REGION" --profile "$MGMT_PROFILE"); \
  [ "$s" != QUEUED ] && [ "$s" != RUNNING ] && [ "$s" != STOPPING ]; do sleep 20; done; echo "$s"

# Expect one line per target account with CURRENT and SUCCEEDED
aws cloudformation list-stack-instances \
  --stack-set-name DormantAccountDetection-TargetAccountRole \
  --query 'Summaries[].[Account,Status,StackInstanceStatus.DetailedStatus,StatusReason]' \
  --output text --region "$REGION" --profile "$MGMT_PROFILE"
```

To run this task from a StackSets delegated administrator account instead, use that account's profile in place of `"$MGMT_PROFILE"` and add `--call-as DELEGATED_ADMIN` to each command.

---

## Epic 4: Configure alerting

Skills required: AWS administrator

### Task 4.1: Confirm the email subscriptions

Amazon Simple Notification Service (Amazon SNS) sent two emails to `NOTIFICATION_EMAIL` in task 2.1, with the subject **AWS Notification - Subscription Confirmation**. Choose **Confirm subscription** in both within 48 hours.

Check the status:

```bash
for t in $(aws cloudformation describe-stacks \
    --stack-name DormantAccountDetection-Dashboard \
    --query 'Stacks[0].Outputs[?OutputKey==`CriticalDormancyTopicArn` || OutputKey==`HighDormancyTopicArn`].OutputValue' \
    --output text --region "$REGION" --profile "$DASH_PROFILE"); do
  aws sns list-subscriptions-by-topic --topic-arn "$t" \
    --query 'Subscriptions[].[Endpoint,SubscriptionArn]' --output text \
    --region "$REGION" --profile "$DASH_PROFILE"
done
```

Expect a full subscription ARN for each topic, not `PendingConfirmation`.

### Task 4.2: Test email delivery

Set an alarm to ALARM:

```bash
aws cloudwatch set-alarm-state \
  --alarm-name DormantAccountDetection-StateMachineFailed \
  --state-value ALARM --state-reason 'notification test' \
  --region "$REGION" --profile "$DASH_PROFILE"
```

Expect an ALARM email within a minute. The alarm returns to OK on its own.

---

## Epic 5: Validate the deployment

Skills required: AWS administrator

### Task 5.1: Run the first collection

Run this after task 3.3 shows SUCCEEDED. It starts a run, waits for it to finish (about 2 minutes), and prints the result.

```bash
EXEC_ARN=$(aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:${REGION}:${DASHBOARD_ACCOUNT_ID}:stateMachine:DormantAccountDetection-MetricCollection" \
  --input '{"source":"manual"}' \
  --query executionArn --output text \
  --region "$REGION" --profile "$DASH_PROFILE")

while [ "$(aws stepfunctions describe-execution --execution-arn "$EXEC_ARN" --query status --output text --region "$REGION" --profile "$DASH_PROFILE")" = RUNNING ]; do sleep 15; done

aws stepfunctions describe-execution \
  --execution-arn "$EXEC_ARN" \
  --query '[status,output,error,cause]' --output text \
  --region "$REGION" --profile "$DASH_PROFILE"
```

The wait prints nothing. Expect one line: `SUCCEEDED`, JSON that contains `"status":"SUCCESS"` and `"failedCount":0`, then `None None` (no error, no cause). For any other result, see [Troubleshooting](#troubleshooting).

Expect no dormancy email from this run, and no HIGH or CRITICAL rating for about 44 days (see [Limitations](../README.md#limitations)).

### Task 5.2: Check the aggregator coverage


```bash
aws logs tail /aws/lambda/DormantAccountDetection-CollectConfig \
  --since 3h --filter-pattern '?collection_start ?total_resource_count_complete ?total_resource_count_failed' \
  --region "$REGION" --profile "$DASH_PROFILE"
```

Expect `accountsMatched` in the `total_resource_count_complete` line to equal `accountCount` in the `collection_start` line. A lower value means the aggregator misses accounts (see task 1.6).

### Task 5.3: Check the alarms


```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix DormantAccountDetection- \
  --query 'MetricAlarms[].[AlarmName,StateValue]' \
  --output text --region "$REGION" --profile "$DASH_PROFILE"
```

Expect OK for every alarm except `CollectionHeartbeat`. It can show INSUFFICIENT_DATA or ALARM until CloudWatch evaluates the hour that contains the run (up to about 2 hours), then OK.

### Task 5.4: Open the dashboard

Print the dashboard URL and open it. Expect data in the Dormancy Scoring, API Call Activity, IAM Activity, Resource Inventory, Monthly Cost, and Metric Collection Health sections. The Dormant Accounts table stays empty until an account is rated CRITICAL or HIGH.

```bash
aws cloudformation describe-stacks \
  --stack-name DormantAccountDetection-Dashboard \
  --query 'Stacks[0].Outputs[?OutputKey==`DashboardUrl`].OutputValue' \
  --output text --region "$REGION" --profile "$DASH_PROFILE"
```

### Task 5.5: Grant viewers access to the Dormant Accounts table

Print the widget function ARN:

```bash
aws cloudformation describe-stacks \
  --stack-name DormantAccountDetection-Dashboard \
  --query 'Stacks[0].Outputs[?OutputKey==`DormantAccountsWidgetFunctionArn`].OutputValue' \
  --output text --region "$REGION" --profile "$DASH_PROFILE"
```

Add this statement, with that ARN as `Resource`, to the IAM policy or IAM Identity Center permission set that viewers use in the Dashboard account. When the widget first loads, choose **Allow always**.

```json
{
  "Effect": "Allow",
  "Action": "lambda:InvokeFunction",
  "Resource": "<function-arn>"
}
```

---

## Epic 6: Clean up resources

Skills required: AWS administrator

Run the tasks in order, one at a time, in the shell from Epic 1. In a new shell, run tasks 1.2, 1.3, and 1.8 first. If you turned on termination protection, turn it off for each stack with `aws cloudformation update-termination-protection --no-enable-termination-protection --stack-name <stack-name> --region "$REGION" --profile <profile>`. If a `wait` command exits with an error, or task 6.1 doesn't print SUCCEEDED, stop. Find the cause in the stack events (see [Troubleshooting](#troubleshooting)) and repeat that task.

### Task 6.1: Delete the target account role StackSet

If you added instances for `TargetAccountIds`, delete them first with the same `--deployment-targets` you used to create them. If you ran task 3.3 as a delegated administrator, use that account's profile in place of `"$MGMT_PROFILE"` and add `--call-as DELEGATED_ADMIN` to each command in this task only.

```bash
TARGET_OU_IDS=$(aws cloudformation describe-stack-set \
  --stack-set-name DormantAccountDetection-TargetAccountRole \
  --query 'StackSet.OrganizationalUnitIds' --output json \
  --region "$REGION" --profile "$MGMT_PROFILE" | tr -d ' \n')

OPERATION_ID=$(aws cloudformation delete-stack-instances \
  --stack-set-name DormantAccountDetection-TargetAccountRole \
  --deployment-targets OrganizationalUnitIds="$TARGET_OU_IDS" \
  --regions "$REGION" --no-retain-stacks \
  --operation-preferences RegionConcurrencyType=PARALLEL,MaxConcurrentPercentage=100,FailureTolerancePercentage=10 \
  --query OperationId --output text \
  --region "$REGION" --profile "$MGMT_PROFILE")

# Wait for the operation (expect SUCCEEDED)
until s=$(aws cloudformation describe-stack-set-operation \
    --stack-set-name DormantAccountDetection-TargetAccountRole \
    --operation-id "$OPERATION_ID" \
    --query StackSetOperation.Status --output text \
    --region "$REGION" --profile "$MGMT_PROFILE"); \
  [ "$s" != QUEUED ] && [ "$s" != RUNNING ] && [ "$s" != STOPPING ]; do sleep 20; done; echo "$s"

[ "$s" = SUCCEEDED ] && aws cloudformation delete-stack-set \
  --stack-set-name DormantAccountDetection-TargetAccountRole \
  --region "$REGION" --profile "$MGMT_PROFILE"
```

Expect `SUCCEEDED`.

### Task 6.2: Delete the role stacks

```bash
aws cloudformation delete-stack --stack-name DormantAccountDetection-ConfigAggregatorRole --region "$REGION" --profile "$CFG_PROFILE"
aws cloudformation wait stack-delete-complete --stack-name DormantAccountDetection-ConfigAggregatorRole --region "$REGION" --profile "$CFG_PROFILE"

aws cloudformation delete-stack --stack-name DormantAccountDetection-ManagementRole --region "$REGION" --profile "$MGMT_PROFILE"
aws cloudformation wait stack-delete-complete --stack-name DormantAccountDetection-ManagementRole --region "$REGION" --profile "$MGMT_PROFILE"
```

Expect no output.

### Task 6.3: Delete the main stack

Save the AWS Key Management Service (AWS KMS) key ARNs first, because task 6.4 needs them after the stack is gone. The delete takes about 4 minutes.

```bash
LOG_KEY_ARN=$(aws cloudformation describe-stacks --stack-name DormantAccountDetection-Dashboard \
  --query 'Stacks[0].Outputs[?OutputKey==`LogGroupKmsKeyArn`].OutputValue' --output text \
  --region "$REGION" --profile "$DASH_PROFILE")
SNS_KEY_ARN=$(aws cloudformation describe-stacks --stack-name DormantAccountDetection-Dashboard \
  --query 'Stacks[0].Outputs[?OutputKey==`SnsKmsKeyArn`].OutputValue' --output text \
  --region "$REGION" --profile "$DASH_PROFILE")
echo "LOG_KEY_ARN=$LOG_KEY_ARN SNS_KEY_ARN=$SNS_KEY_ARN"

aws cloudformation delete-stack --stack-name DormantAccountDetection-Dashboard --region "$REGION" --profile "$DASH_PROFILE"
aws cloudformation wait stack-delete-complete --stack-name DormantAccountDetection-Dashboard --region "$REGION" --profile "$DASH_PROFILE"
```

Expect two key ARNs (a key you supplied prints an empty value), then no output from the delete.

### Task 6.4: Delete the retained resources

Delete the eight log groups, any account-name parameters, the two AWS KMS keys, and the staged template. You must delete the log groups before you deploy again. To keep the logs after a permanent teardown, skip the log group commands and leave `LOG_KEY_ARN` out of the key loop.

```bash
# Log groups
for fn in GetAccounts CollectApiCallMetrics CollectIAM CollectConfig CollectCost PublishMetrics customWidget-DormantAccounts; do
  aws logs delete-log-group --log-group-name "/aws/lambda/DormantAccountDetection-${fn}" --region "$REGION" --profile "$DASH_PROFILE"
done
aws logs delete-log-group --log-group-name /aws/states/DormantAccountDetection --region "$REGION" --profile "$DASH_PROFILE"

# Account-name parameters (no output means there were none)
aws ssm get-parameters-by-path --path /DormantAccountDetection/AccountNames \
  --query 'Parameters[].Name' --output text \
  --region "$REGION" --profile "$DASH_PROFILE" | xargs -r -n 10 aws ssm delete-parameters \
  --region "$REGION" --profile "$DASH_PROFILE" --names

# AWS KMS keys
for k in "$LOG_KEY_ARN" "$SNS_KEY_ARN"; do
  [ -n "$k" ] && [ "$k" != None ] && aws kms schedule-key-deletion \
    --key-id "$k" --pending-window-in-days 30 \
    --region "$REGION" --profile "$DASH_PROFILE"
done

# Staged template. Also remove the bucket if task 1.8 created it only for this solution.
aws s3 rm "s3://$TEMPLATE_BUCKET/dormant-account-detection/" --recursive --region "$REGION" --profile "$DASH_PROFILE"
# aws s3 rb "s3://$TEMPLATE_BUCKET" --region "$REGION" --profile "$DASH_PROFILE"
```

Expect `PendingDeletion` lines for the keys, `delete:` lines for the staged template, and no errors. CloudWatch metric data stays after teardown and expires on its own (see [Retained resources](REFERENCE.md#retained-resources)).

### Task 6.5: Verify the teardown


```bash
# No stacks or IAM roles left in any of the three accounts
for p in "$DASH_PROFILE" "$MGMT_PROFILE" "$CFG_PROFILE"; do
  aws cloudformation list-stacks \
    --query "StackSummaries[?contains(StackName,'DormantAccountDetection') && StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus]" \
    --output text --region "$REGION" --profile "$p"
  aws iam list-roles --query "Roles[?starts_with(RoleName,'DormantAccountDetection')].RoleName" \
    --output text --profile "$p"
done

# No StackSet left in the management account, and no account-name parameters in the Dashboard account
aws cloudformation list-stack-sets --status ACTIVE \
  --query "Summaries[?StackSetName=='DormantAccountDetection-TargetAccountRole'].StackSetName" \
  --output text --region "$REGION" --profile "$MGMT_PROFILE"
aws ssm get-parameters-by-path --path /DormantAccountDetection/AccountNames \
  --query 'Parameters[].Name' --output text --region "$REGION" --profile "$DASH_PROFILE"

# No log groups left in the Dashboard account
aws logs describe-log-groups --log-group-name-prefix /aws/lambda/DormantAccountDetection- \
  --query 'logGroups[].logGroupName' --output text --region "$REGION" --profile "$DASH_PROFILE"
aws logs describe-log-groups --log-group-name-prefix /aws/states/DormantAccountDetection \
  --query 'logGroups[].logGroupName' --output text --region "$REGION" --profile "$DASH_PROFILE"

# Each key shows PendingDeletion
for k in "$LOG_KEY_ARN" "$SNS_KEY_ARN"; do
  [ -n "$k" ] && [ "$k" != None ] && aws kms describe-key --key-id "$k" \
    --query KeyMetadata.KeyState --output text --region "$REGION" --profile "$DASH_PROFILE"
done
```

Expect no output, except `PendingDeletion` for each key.

To deploy again, run Epics 2-5 in order and confirm both email subscriptions again. For a clean baseline, see [Retained resources](REFERENCE.md#retained-resources). To change a running deployment instead, see [Update the deployment](REFERENCE.md#update-the-deployment).

---

## Troubleshooting

| Issue | Solution |
|---|---|
| A stack fails to create | Find the cause with `aws cloudformation describe-stack-events --stack-name <stack-name> --query "StackEvents[?contains(ResourceStatus,'FAILED')].[LogicalResourceId,ResourceStatusReason]" --output text --region "$REGION" --profile <profile>`. Delete the failed stack and wait for `DELETE_COMPLETE`. For the main stack, also run [task 6.4](#task-64-delete-the-retained-resources). Then retry. |
| Task 2.1 fails with `already exists` on a log group | An earlier deployment left its log groups. Run the log group commands in [task 6.4](#task-64-delete-the-retained-resources), then task 2.1 again. |
| Task 5.1 prints `PARTIAL_FAILURE` or `FAILURE` | Search the `/aws/lambda/DormantAccountDetection-PublishMetrics` log group for `per_account_collection_failed` (lists the failed accounts and signals) and `lambda_level_errors`. `FAILURE` means every account failed, usually because the AWS Config or Cost Explorer query failed. If the `-CollectConfig` log shows `total_resource_count_failed`, redo [task 1.4](#task-14-find-the-aws-config-aggregator) and rerun task 2.1. If the `-CollectCost` log shows an error, activate Cost Explorer in the management account. |
| Task 5.1 prints `FAILED` | Find the failing state with `aws stepfunctions get-execution-history --execution-arn "$EXEC_ARN" --reverse-order --max-results 20 --region "$REGION" --profile "$DASH_PROFILE"`, then read that function's `/aws/lambda/DormantAccountDetection-<Function>` log group. `AccessDenied` on `sts:AssumeRole` means the role stack in that account has a different `ExternalId` or role name: rerun task 3.1 or 3.2 with the values from Epic 1. |
| `AccessDenied` on `sts:AssumeRole` after you recreate only the main stack | The role stacks still trust the deleted execution roles. Run Epic 6, then Epics 2-5. For other options, see [Stale trust after recreating the main stack](REFERENCE.md#stale-trust-after-recreating-the-main-stack). |
| A stack instance in task 3.3 isn't `CURRENT` | Read its `StatusReason` in the `list-stack-instances` output, fix the cause, and run `create-stack-instances` again. Accounts listed only in `TargetAccountIds` also need an instance. |
| No subscription line for a topic in task 4.1, or no email arrives | Run `aws sns subscribe --topic-arn <topic-arn> --protocol email --notification-endpoint "$NOTIFICATION_EMAIL" --region "$REGION" --profile "$DASH_PROFILE"` and confirm the new email. Dormancy emails arrive only when an account's rating newly worsens into CRITICAL or HIGH. |
| The dashboard or the Dormant Accounts table shows no data | Wait for task 5.1 to finish, because the metrics appear only after PublishMetrics runs. For the table, choose **Allow always** and complete task 5.5. An empty table means no account is rated CRITICAL or HIGH. |
| A new account, or the cost widgets, show no data | New accounts aren't scored during the grace period (see [Limitations](../README.md#limitations)), but their activity metrics appear. Cost widgets show the previous calendar month, which can take up to 24 hours after month end to appear. |
| Log group creation fails with an AWS KMS `AccessDeniedException` | A key you supplied in `LogGroupKmsKeyArn` must allow `logs.<REGION>.amazonaws.com` and, with Step Functions encryption on, `states.amazonaws.com` to use it. |
| The execution times out after 3 hours | Raise `MapConcurrency` (up to 40) and set `LambdaReservedConcurrency` to at least the same value, within your Lambda concurrency quota. |
