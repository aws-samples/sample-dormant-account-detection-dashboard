# Dormant Account Detection Dashboard

Large AWS Organizations accumulate dormant accounts over time. Projects end, experiments get abandoned, and teams move on, but the accounts remain. Each dormant account incurs cost, increases security exposure, complicates compliance audits, and inflates the account inventory. Finding these accounts by hand across tens or hundreds of accounts isn't practical, so platform, security, and FinOps teams need an automated, repeatable assessment.

This pattern deploys a serverless solution that runs a daily assessment of every account in an organization in AWS Organizations, or a subset filtered by organizational unit (OU) IDs and account IDs. [AWS Control Tower](https://docs.aws.amazon.com/controltower/latest/userguide/what-is-control-tower.html) is supported but not required. The solution collects 7 activity signals per account over a configurable lookback window (90 days by default) and computes a Dormancy Score from 0 (fully dormant) to 100 (fully active) with a hybrid self-baselining model. The model combines absolute human-presence signals (AWS IAM Identity Center and IAM user activity) with self-relative activity trends (API calls, IAM roles, cost, and resource creation), so each account is judged against its own history instead of fixed cross-account thresholds. Scores fall into five rating bands, from CRITICAL to ACTIVE, and newly onboarded accounts get a configurable grace period.

The solution publishes up to 11 per-account metrics to Amazon CloudWatch. It can send Amazon Simple Notification Service (Amazon SNS) email notifications when accounts newly fall into the CRITICAL or HIGH bands, when data collection fails, and when no collection run completes. Six AWS Lambda functions collect data from AWS Organizations, [AWS Cost Explorer](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-what-is.html), AWS Config, AWS Identity and Access Management (IAM), and CloudWatch. An AWS Step Functions state machine orchestrates the functions, and Amazon EventBridge starts the state machine daily. A seventh function supports a dashboard widget that lists the current CRITICAL and HIGH accounts. A pre-built CloudWatch dashboard (22 to 26 widgets) shows account activity, dormancy scores and ratings, collection health, and cost trends, with an account-name filter. The solution deploys entirely through [AWS CloudFormation](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/Welcome.html), with all AWS Lambda function code inline, so it needs no build pipeline.

![Architecture diagram](diagrams/DormantAccountDetectionDashboard%20-%20Numbered.jpg)

## Contents

- [Prerequisites and limitations](#prerequisites-and-limitations)
- [Best practices](#best-practices)
- [Deployment](#deployment)
- [Code repository](#code-repository)
- [Related resources](#related-resources)
- [Security](#security)
- [License](#license)

## Prerequisites and limitations

### Prerequisites

- An active AWS account.
- An organization in AWS Organizations, with or without AWS Control Tower.
- AWS Command Line Interface (AWS CLI) version 2.
- You must have permission to deploy CloudFormation stacks that create IAM roles in three accounts (see [Deployment permissions](docs/REFERENCE.md#deployment-permissions)):
  - **Dashboard account:** runs the solution and the dashboard.
  - **Management account:** your organization's management account.
  - **Config Aggregator account:** hosts the AWS Config aggregator. With AWS Control Tower, use the Audit account.
- An AWS Config aggregator in the Region where you deploy, covering every target account (see [Best practices](#best-practices)).
- AWS Cost Explorer activated in the management account.
- Trusted access for [AWS CloudFormation StackSets](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/what-is-cfnstacksets.html) activated in AWS Organizations.
- An Amazon S3 bucket in the Dashboard account and Region to stage the main template. The template is larger than the 51,200-byte limit for templates passed directly to AWS CloudFormation. The CloudFormation console creates a staging bucket for you.

### Limitations

- The services used in this pattern might not be available in all AWS Regions. For Region availability, see [AWS Services by Region](https://aws.amazon.com/about-aws/global-infrastructure/regional-product-services/).
- Metrics, alarms, and the dashboard deploy to one Region. The solution still collects API call counts and AWS Config counts from every Region, unless you list Regions in `TargetRegions`.
- The trend half of the score detects decline, not sustained flat dormancy. An account with flat activity, even at zero, keeps full trend points, so it scores at least 50 (MEDIUM) with the default weights.
- Trend scoring needs history. For about 44 days after a fresh deployment, the solution does not rate any account HIGH or CRITICAL with the default parameters.
- Accounts that joined the organization within `NewAccountGracePeriodDays` (default 30 days) publish activity metrics, but the solution does not score them.
- The cost signal covers the previous full calendar month. `CollectCost` queries Cost Explorer for the last complete month, so `MonthlyCostUSD` changes once a month and the cost trend reacts to a spending change about a month later. Cost Explorer also offers daily and month-to-date data, but the solution uses complete months so the value stays stable.
- Accounts or Regions that the AWS Config aggregator does not cover report 0 resources.
- The service-managed StackSet doesn't deploy to the management account, so the solution doesn't monitor it.
- Lambda code is inline (`ZipFile`), so external Python dependencies aren't supported.

### Product versions

- Python 3.14 (Lambda function runtime)
- AWS CLI version 2
- AWS CloudFormation (2010-09-09 template format)

## Best practices

Decide these four things before you deploy. Changing the Dashboard account or Region later means a full teardown.

- **Use a dedicated Dashboard account.** Put it in a security OU, not a workload or shared-services account. It holds every target account's IAM and AWS IAM Identity Center activity, API call volume, resource inventory, and cost, so anyone who controls it can survey the whole organization. Limit human access to a small group that uses multi-factor authentication (MFA).
- **Use an aggregator that covers every target account.** It must be in the Region where you deploy and cover every Region and global resources. With AWS Control Tower, enroll every target account. For other aggregators, see [AWS Config aggregator](docs/REFERENCE.md#aws-config-aggregator).
- **Deploy in one Region.** The IAM role names are fixed, so a second copy in another Region of the same account fails.
- **Keep the stack names.** Updates and teardown reuse the guide's `DormantAccountDetection-*` stack names, and CloudFormation can't rename a stack.

Then follow these practices:

- **Restrict changes to the solution.** Use service control policies (SCPs) or permissions boundaries so only a dedicated deployment role can update the stacks and Lambda functions, and enable stack termination protection.
- **Set `OrganizationId` and `ExternalId`.** `OrganizationId` limits the per-account roles to accounts in your organization. `ExternalId` adds an `sts:ExternalId` condition to every cross-account trust policy. The deployment guide configures both.
- **Keep the default customer managed keys.** `LogGroupKmsKeyType` and `SnsKmsKeyType` default to `CMK`, with automatic rotation.
- **Keep identity details out of the logs.** Leave `LogIamPrincipalNames` at `false`. Turn it on only while you troubleshoot a count.
- **Keep the alarms on.** Set `NotificationEmail` to a team distribution list, so the failure and heartbeat alarms reach someone.
- **Exclude administrator sign-ins from scoring.** Set `AdminSSOPrefixes` to the permission set prefixes your administrators use.
- **Keep `TrendWindowFraction` at or below 0.4** (see [Dormancy scoring](docs/ARCHITECTURE.md#dormancy-scoring-hybrid-self-baselining-model)).
- **List only the Regions you use in `TargetRegions`.** Each Region adds CloudWatch API cost in every member account (see [Cost estimate](docs/ARCHITECTURE.md#cost-estimate)).
- **Raise `LambdaReservedConcurrency` and `MapConcurrency` together** for more than 200 accounts, within your Lambda concurrency quota.

For more hardening, see [Production Hardening Recommendations](SECURITY.md#production-hardening-recommendations).

## Deployment

Follow [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md). Clone this repository and run the commands from its root directory.

## Code repository

The code for this pattern is available in the GitHub [sample-dormant-account-detection-dashboard](https://github.com/aws-samples/sample-dormant-account-detection-dashboard) repository.

| Path | Contents |
|---|---|
| `cloudformation/dormant-account-detection-dashboard-stack.yaml` | Main stack: 7 inline Lambda functions, a Step Functions state machine, an EventBridge rule, the CloudWatch dashboard, alarms, Amazon SNS topics, AWS KMS keys, 9 IAM roles, and 9 customer managed policies |
| `cloudformation/management-account-monitoring-role.yaml` | Cross-account role and 2 customer managed policies for the management account |
| `cloudformation/config-aggregator-monitoring-role.yaml` | Cross-account role and 1 customer managed policy for the Config Aggregator account |
| `cloudformation/stackset-target-accounts-monitoring-role.yaml` | Cross-account role and 2 customer managed policies for target member accounts (deployed through StackSets) |
| [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) | Deployment epics and troubleshooting |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture, AWS services, scoring, CloudWatch resources, and cost estimate |
| [docs/REFERENCE.md](docs/REFERENCE.md) | Parameters, deployment behavior, updates, and retained resources |
| [SECURITY.md](SECURITY.md) | Security posture and hardening |
| `diagrams/` | Architecture diagram source (draw.io), PowerPoint version, and the numbered JPG used in the docs |

## Related resources

- [Using CloudWatch in centralized or distributed accounts](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Cross-Account-Cross-Region.html)
- [Managing AWS Organizations](https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_org.html)
- [AWS Control Tower documentation](https://docs.aws.amazon.com/controltower/)
- [Creating Aggregators for AWS Config](https://docs.aws.amazon.com/config/latest/developerguide/aggregated-create.html)
- [AWS Cost Explorer API reference](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_Operations_AWS_Cost_Explorer_Service.html)
- [AWS Step Functions Developer Guide](https://docs.aws.amazon.com/step-functions/latest/dg/welcome.html)
- [Working with AWS CloudFormation StackSets](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/what-is-cfnstacksets.html)
- [Organizing your AWS environment using multiple accounts](https://docs.aws.amazon.com/whitepapers/latest/organizing-your-aws-environment/organizing-your-aws-environment.html)
- [Access to AWS accounts owned by third parties (external ID)](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html)
- [Amazon CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)

## Security

See [SECURITY.md](SECURITY.md) for the security posture of this solution and how to report a security issue, and [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
