# Azure SQL Free ranking archive (PoC)

Test-only infrastructure for issues **#165–#167**. Provisions the minimum Azure
resources needed to hold **public historical market ranking** archives.

This module does **not** change the Stock Ticker PostgreSQL path. Personal data
(users, portfolios, watchlists, alerts, API keys) must never be written here.

## What is provisioned

| Resource | Purpose |
| --- | --- |
| Resource group | Container for the PoC |
| Azure SQL logical server | Host for the archive database |
| Azure SQL Database (`GP_S_Gen5_2` serverless) | Free-offer eligible DB |
| Firewall rule | Single home/dev client IPv4 |

Explicitly **not** provisioned: VMs, AKS, Managed Redis, VPN Gateway, Functions,
Container Apps, Front Door, Application Gateway, or other paid add-ons.

## Free offer + cost guardrails

Terraform sets:

- `sku_name = "GP_S_Gen5_2"` (serverless General Purpose)
- `use_free_limit = true`
- `free_limit_exhaustion_behavior = "AutoPause"` (stop when the free allowance is exhausted; do **not** bill overage)
- `max_size_gb = 32`
- `auto_pause_delay_in_minutes = 60` / `min_capacity = 0.5`

### Portal verification checklist

1. Open **Azure Portal → SQL databases →** `<sql_database_name>`.
2. Open **Compute + storage** (or **Configure**).
3. Confirm the **Free offer / Free limit** is enabled.
4. Confirm exhaustion behavior is **Auto-pause** (not continue with paid usage).
5. Confirm SKU is serverless **GP_S Gen5** (2 vCores max) and max size ≤ 32 GB.
6. Review **Cost Management + Billing** for the resource group: only SQL + RG noise; no unexpected paid services.

### `terraform plan` safety checklist

`terraform plan` should only propose:

- `azurerm_resource_group`
- `azurerm_mssql_server`
- `azurerm_mssql_database`
- `azurerm_mssql_firewall_rule`
- `random_string` / `random_password`

If the plan shows VMs, AKS, Redis, networking gateways, etc., **do not apply**.

## Prerequisites

- Terraform `>= 1.5`
- Azure CLI logged in (`az login`) with rights to create RG + SQL in the target subscription
- azurerm provider `>= 4.30` (Free-offer attributes)
- Home/dev public IPv4 for the SQL firewall (when enabling public network access)
- For app connectivity: [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) + Python `pyodbc`

## Network access

Terraform sets `public_network_access_enabled = false` (secure-by-default / Sonar).
Optional `allowed_client_ipv4` still creates a firewall allow rule for later use.

For a home/dev backend without Private Link:

1. Apply Terraform.
2. In Azure Portal → SQL server → Networking, temporarily enable public access.
3. Confirm your client IP is allowed (or set `allowed_client_ipv4` before apply).
4. Run the smoke script, then disable public access again when done.

## Apply

```bash
cd infra/azure-rankings-archive
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: set allowed_client_ipv4 (and optional password)

terraform init
terraform plan
terraform apply
```

Sensitive outputs:

```bash
terraform output -raw sql_admin_password
terraform output rankings_archive_database_url_example
```

Build the app env var (do **not** commit):

```bash
export RANKINGS_ARCHIVE_DATABASE_URL='mssql+pyodbc://USER:URL_ENCODED_PASSWORD@SERVER.database.windows.net:1433/rankings_archive?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no'
```

`DATABASE_URL` (Postgres) stays unchanged.

## Connectivity smoke test

From the home/dev machine that matches `allowed_client_ipv4`:

```bash
# Optional disposable PoC table (also covered by scripts/azure_rankings_archive_smoke.py)
python3 scripts/azure_rankings_archive_smoke.py
```

The script:

1. Connects with `RANKINGS_ARCHIVE_DATABASE_URL`
2. Creates a disposable `_poc_smoke` table
3. Inserts / reads / deletes one non-personal row
4. Drops the table
5. Upserts a disposable EOD row into `ranking_snapshots_archive`, loads it back,
   reconstructs rank order, then deletes the smoke archive rows
6. Exits non-zero on failure without touching Postgres

## Destroy / recreate

```bash
terraform destroy
# then terraform apply again for a clean recreate
```

Local state lives under this directory and is gitignored (`*.tfstate`, `.terraform/`).

## Cost-safety verification after apply

- [ ] Portal Free offer enabled + Auto-pause on exhaustion
- [ ] Only intended resources exist in the resource group
- [ ] Firewall allows only the expected client IP
- [ ] No portfolio/user tables were created in Azure (app creates only archive tables)
- [ ] Unsetting `RANKINGS_ARCHIVE_DATABASE_URL` leaves the FastAPI app fully usable

## Related app settings

See root `backend/.env.example` and `docs/azure-rankings-archive.md`.

## Backend container

The app image installs ODBC Driver 18 + `pyodbc`. Wire the URL into the cluster
with `k8s/backend-rankings-archive-secret.yaml.example` (see
`docs/azure-rankings-archive.md` → Backend container / k3s hookup). Ensure the
Azure SQL firewall allows the cluster egress IP (or your laptop IP when using
Lima/`host.docker.internal` testing).
