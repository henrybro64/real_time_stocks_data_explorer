# AWS heartbeat monitor (outbound-only)

Optional **home → AWS** heartbeat for the FastAPI `/health` endpoint when the
home k3s node sits behind NAT with **no public domain, static public IP, or
inbound port**. Additive only: it does not replace local observability, app
logging, Azure SQL rankings archive, PostgreSQL, or Redis, and it does not move
the app to AWS.

## Architecture

**The home node initiates all communication with AWS. AWS never connects back.**

```text
Home k3s CronJob (every 15 minutes)
   │
   ├─ GET http://stock-ticker-backend:8000/health   (in-cluster only)
   └─ outbound HTTPS PutItem
          │
          ▼
      DynamoDB Standard (1 RCU / 1 WCU, TTL ~7 days)
          ▲
          │ Query newest item (Limit 1)
        Lambda stale-check (128 MB, 5s, no VPC)
          ▲
          │ every 15 minutes
 EventBridge Scheduler
```

If the home node loses power, Internet, k3s, the backend, or the CronJob, new
heartbeats stop. Lambda then classifies the latest record as **stale** or
**missing**. No Cloudflare Tunnel, port-forward, or public `/health` URL is
required.

Terraform: [`infra/aws-health-monitor/README.md`](../infra/aws-health-monitor/README.md)  
Home CronJob: [`k8s/aws-health-heartbeat.yaml`](../k8s/aws-health-heartbeat.yaml)  
Do not mix with [`infra/azure-rankings-archive/README.md`](../infra/azure-rankings-archive/README.md).

## Cost and Free Tier

Expected **normal** monthly usage (home CronJob + AWS schedule both at 15 min):

```text
Home → DynamoDB PutItem:   ~2,880 writes/month
EventBridge Scheduler:     ~2,880 invocations/month
Lambda requests:           ~2,880/month
Lambda compute worst case at 128 MB × 5 s:
  2,880 × 5 × 0.128 ≈ 1,843 GB-seconds/month
DynamoDB Query:            ~2,880 small reads/month
DynamoDB storage:          ~7 days of tiny records
CloudWatch Logs:           one short JSON line per Lambda run, 3-day retention
```

Far below currently published Free Tier allowances for Scheduler, Lambda, and
DynamoDB Standard provisioned capacity (25 RCU / 25 WCU / 25 GB).

**Free Tier is an allowance, not a hard billing cap.** AWS does not guarantee
zero charges on a paid account. Do not treat `$0 expected` as `$0 guaranteed`.

## Billing-safety checklist (before apply)

1. Create a **$0 or $1 monthly budget** with email; enable Free Tier usage alerts
   if available.
2. Optional: set `budget_notification_email` in `terraform.tfvars`.
3. `terraform plan` and confirm there is **no** NAT Gateway, API Gateway, EC2,
   load balancer, SNS, SQS, Secrets Manager, VPC, DynamoDB on-demand, streams,
   PITR, DAX, or `health_url` / home-node URL variable.
4. After apply, check Cost Explorer: estimated charges should stay `$0` in normal
   operation. Otherwise `terraform destroy`.

## Deploy AWS side

Needs AWS credentials and Terraform **>= 1.7.0**. No public URL variable.

```bash
cd infra/aws-health-monitor
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

### Important variables

| Variable | Default | Notes |
| --- | --- | --- |
| `aws_region` | `us-east-1` | Also used as CronJob `AWS_REGION` |
| `name_prefix` | `stock-ticker-health` | Resource names |
| `service_name` | `stock-ticker-backend` | DynamoDB PK |
| `stale_after_seconds` | `1800` | 30 minutes; one missed beat is OK |
| `schedule_expression` | `rate(15 minutes)` | Locked in v1 |
| `ttl_days` | `7` | Copy into CronJob Secret as `TTL_DAYS` (sets `expires_at`) |
| `lambda_memory_mb` | `128` | Locked |
| `lambda_timeout_seconds` | `5` | 3–5 |
| `log_retention_days` | `3` | 1, 3, 5, or 7 |
| `scheduler_retry_attempts` | `1` | 0 or 1 |
| `budget_notification_email` | `""` | Optional |

There is **no** `health_url` / `target_url` Terraform variable. Lambda must not
call the home node.

### IAM for the home sender

Terraform creates a dedicated IAM user with **only** `dynamodb:PutItem` on the
heartbeat table. Access keys are **not** created in Terraform (keeps secrets out
of state and git).

```bash
# after apply
terraform output -raw create_access_key_command
# → aws iam create-access-key --user-name stock-ticker-health-heartbeat
```

Store the key only in a Kubernetes Secret (see below). Rotate by creating a new
key, updating the Secret, then deleting the old key:

```bash
aws iam list-access-keys --user-name stock-ticker-health-heartbeat
aws iam delete-access-key --user-name stock-ticker-health-heartbeat --access-key-id AKIA…
```

Revoke immediately by deleting the IAM user (`terraform destroy`) or deleting
all access keys.

## Deploy home CronJob

The CronJob is in the default `k8s/` kustomization and is **not** suspended
(redeploys must not flip an enabled job back off). Secret refs are optional.

Enablement semantics (#325):

| Configuration | Result |
| --- | --- |
| No Secret / `AWS_HEALTH_MONITOR_ENABLED` unset, no credentials | Exit 0, `delivery_status=skipped`, `reason=not_configured` |
| `AWS_HEALTH_MONITOR_ENABLED=false` | Exit 0, `delivery_status=skipped`, `reason=disabled` |
| `AWS_HEALTH_MONITOR_ENABLED=true`, credentials missing | Exit 2, `delivery_status=error`, `error_type=missing_credentials` |
| Enabled (flag true **or** credentials present), PutItem fails | Exit 1, `delivery_status=error` (sanitized; no AWS body) |
| PutItem succeeds | Exit 0, `delivery_status=delivered`, plus `service_status` from `/health` |

**Job `Succeeded` is not proof of AWS delivery.** Exit 0 covers delivered
heartbeats **and** intentional skips. Inspect the latest Job log for
`delivery_status=delivered` before claiming CloudWatch/DynamoDB delivery.
`service_status=unhealthy` with `delivery_status=delivered` means AWS accepted
the record while in-cluster `/health` was not `ok`.

**Schedule:** the CronJob stays at `*/15 * * * *` (aligned with the AWS-side
EventBridge 15-minute stale check). A longer-term hourly cadence remains a
cost/ops goal, not the current default — changing it would require updating
`stale_after_seconds` and Terraform schedule together.

```bash
cp k8s/aws-health-heartbeat-secret.yaml.example k8s/aws-health-heartbeat-secret.yaml
# Fill from terraform outputs (do not invent values):
#   AWS_REGION, TABLE_NAME, SERVICE_NAME, TTL_DAYS
#   plus the access key from create-access-key
# TABLE_NAME=$(terraform -chdir=infra/aws-health-monitor output -raw dynamodb_table_name)
# SERVICE_NAME=$(terraform -chdir=infra/aws-health-monitor output -raw service_name)
# TTL_DAYS=$(terraform -chdir=infra/aws-health-monitor output -raw ttl_days)

kubectl -n stock-ticker apply -f k8s/aws-health-heartbeat-secret.yaml
```

Each run (when the Secret is present):

1. GETs in-cluster `http://stock-ticker-backend:8000/health`
2. Requires HTTP 200 + JSON `status == "ok"` for `healthy` (still writes an
   `unhealthy` heartbeat on local failure)
3. PutItem to DynamoDB over outbound HTTPS (stdlib SigV4; no boto3). The
   CronJob reuses `stock-ticker-backend:local` (GHCR deploy overlay rewrites
   it) so it does not need a separate Docker Hub python pull.

Script source of truth:
[`infra/aws-health-monitor/heartbeat/send_heartbeat.py`](../infra/aws-health-monitor/heartbeat/send_heartbeat.py).
Copy into
[`k8s/aws-health-heartbeat/send_heartbeat.py`](../k8s/aws-health-heartbeat/send_heartbeat.py)
for the ConfigMap (unit tests enforce the two files stay identical).

## Heartbeat record shape

| Field | Meaning |
| --- | --- |
| `service_name` | Partition key |
| `checked_at` | Sort key, ISO-8601 UTC |
| `check_id` | `service_name#checked_at` |
| `status` | `healthy` or `unhealthy` (local `/health`) |
| `http_status` | Integer, omitted on network/timeout |
| `latency_ms` | Integer |
| `error_type` / `message` | Short bounded codes; no bodies or stack traces |
| `expires_at` | Epoch seconds for TTL |

## Lambda classification

Age of the newest DynamoDB item drives `classification`. The local probe result
is a separate field (`heartbeat_status` / record `status`): a **recent**
heartbeat can still be `unhealthy` if `/health` failed on the home node.

| Classification | Meaning |
| --- | --- |
| `recent` | Newest heartbeat age ≤ `stale_after_seconds` (default 30m) |
| `stale` | Newest heartbeat older than the threshold |
| `missing` | No items for `service_name` |

Lambda only **Query**s Limit 1 (newest). It does not PutItem, does not call
home, and logs one JSON line (`event=aws_health_monitor.stale_check`).

## Inspect

```bash
TABLE=$(terraform -chdir=infra/aws-health-monitor output -raw dynamodb_table_name)

aws dynamodb query \
  --table-name "$TABLE" \
  --key-condition-expression "service_name = :s" \
  --expression-attribute-values '{":s":{"S":"stock-ticker-backend"}}' \
  --no-scan-index-forward \
  --limit 10
```

CloudWatch: `terraform output -raw log_group_name`.

## Teardown

```bash
# pause home writer (remove credentials; CronJob stays applied and skips)
kubectl -n stock-ticker delete secret stock-ticker-aws-health-heartbeat --ignore-not-found

cd infra/aws-health-monitor
terraform destroy
```

Delete any leftover access keys if you created them outside Terraform. This does
**not** touch Azure, Postgres, Redis, or the app workloads beyond the optional
CronJob.

## Historical Error pods (#325)

Observed 2026-11-10 on the home k3s cluster: several
`stock-ticker-aws-health-heartbeat-*` pods aged ~4 days were `0/1 Error`, while a
newer Job had already `Completed`. **Do not treat old Error pods alone as proof
the CronJob is still failing.**

Likely historical causes (capture before deleting evidence):

1. Temporary DynamoDB PutItem / DNS / credential failure while the Secret was
   present (script exited non-zero → Job `Error`).
2. Image/script mismatch during a local image rollout window.
3. In-cluster `/health` unreachable briefly (still writes an `unhealthy`
   heartbeat when PutItem succeeds — that is **not** a Job Error).

**Exact root cause of those 2026-11-10 Error pods is still
`aws_e2e_verification_pending`** until an authorized operator captures
`kubectl describe` / logs from a failed Job (or confirms they have already
rotated out). Do not invent pod log contents.

Hardening in-repo (#325):

| Change | Why |
| --- | --- |
| Structured JSON on PutItem failure (`status=error`, `error_type`, no secrets) | Actionable CronJob logs |
| Non-zero exit on AWS write failure | Job stays `Failed` until the next healthy run |
| `activeDeadlineSeconds: 60` | Bound hung Jobs |
| `ttlSecondsAfterFinished: 604800` | Auto-clean finished Jobs after 7 days (keeps ~4-day Error pods diagnosable) |
| `failedJobsHistoryLimit: 5` / `successfulJobsHistoryLimit: 2` | Keep short diagnostic history |
| `startingDeadlineSeconds: 300` | Skip stale schedules after node downtime |

### Manual verification (authorized; do not run without approval)

```bash
# Distinguish historical Error from current health
kubectl -n stock-ticker get cronjob stock-ticker-aws-health-heartbeat -o yaml
kubectl -n stock-ticker get jobs --sort-by=.metadata.creationTimestamp \
  | awk 'NR==1 || /aws-health-heartbeat/'
# Latest heartbeat Job logs (name match, not “last job of any kind”)
JOB=$(kubectl -n stock-ticker get jobs --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
  | awk '/aws-health-heartbeat/ {name=$0} END {print name}')
kubectl -n stock-ticker logs "job/${JOB}" --tail=50

# One-shot manual Job (does not change the CronJob schedule)
kubectl -n stock-ticker create job "stock-ticker-aws-health-heartbeat-manual-$(date +%s)" \
  --from=cronjob/stock-ticker-aws-health-heartbeat
```

Confirm CloudWatch / DynamoDB only if AWS credentials and budget approval exist.
If access is unavailable, record `aws_e2e_verification_pending` — do not claim
delivery succeeded.

**Status of #325 verification:** historical Error-pod root cause and live AWS
PutItem/CloudWatch delivery both remain **unverified** until an authorized
operator captures Job logs and DynamoDB/CloudWatch evidence. In-repo hardening
alone is not live proof.

## Tests

No live AWS. From the repo root:

```bash
pytest backend/tests/unit/infra/test_aws_health_monitor_heartbeat.py \
  backend/tests/unit/infra/test_aws_health_monitor_lambda.py \
  backend/tests/unit/infra/test_aws_health_monitor_terraform.py
```

Routine CI (`backend-pytest`) runs those files. There is **no** GitHub Actions
job that applies Terraform or talks to live AWS.
