# AWS heartbeat monitor (outbound-only)

Isolated Terraform for GitHub issue **#216**. The home k3s CronJob checks local
`/health` and **pushes** a heartbeat to DynamoDB. A small Lambda only reads the
newest item and classifies it as recent / stale / missing.

**AWS never calls the home node.** No public domain, static public IP, inbound
port, Cloudflare hostname, or `health_url` variable is required.

## What Terraform manages

| Resource | Purpose |
| --- | --- |
| DynamoDB Standard table | Heartbeats, 1 RCU / 1 WCU, TTL ~7 days |
| Lambda (Python 3.12, 128 MB, 5s) | Query newest heartbeat; classify |
| CloudWatch log group | One compact line per run, retention 3 days |
| IAM roles/policies | Lambda Query + own logs; Scheduler invoke that Lambda |
| IAM user + PutItem policy | Home CronJob credentials (keys created outside TF) |
| EventBridge Scheduler | `rate(15 minutes)`, at most 1 retry |
| Optional AWS Budget | Only if `budget_notification_email` is set |

Explicitly **not** provisioned:

- VPC, NAT Gateway, Elastic IP, VPC endpoints
- API Gateway, SNS, SQS, Step Functions, Secrets Manager, EC2, load balancers
- DynamoDB on-demand, GSIs, streams, PITR, DAX, global tables
- Custom CloudWatch metrics, dashboards, or alarms
- Any home-node URL / `health_url` Lambda environment variable

## Cost note

On a normal paid AWS account, Free Tier is an **allowance, not a billing cap**.
Create a low AWS Budget / Free Tier alert before `apply`.

Full guide: [docs/aws-health-monitor.md](../../docs/aws-health-monitor.md).

## Apply

```bash
cd infra/aws-health-monitor
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
terraform output create_access_key_command
```

Needs Terraform **>= 1.7.0**. Do not commit `terraform.tfvars` or `*.tfstate`.
Never put the IAM secret access key in git or Terraform outputs.

Home CronJob: [k8s/aws-health-heartbeat.yaml](../../k8s/aws-health-heartbeat.yaml)
and [k8s/aws-health-heartbeat-secret.yaml.example](../../k8s/aws-health-heartbeat-secret.yaml.example).

After apply, copy terraform outputs `aws_region`, `dynamodb_table_name`,
`service_name`, and `ttl_days` into the Secret (plus an access key created
outside Terraform). `ttl_days` is not a Lambda setting — the home PutItem
writes `expires_at`. The IAM user has `force_destroy = true` so
`terraform destroy` can remove it while those external keys still exist.
