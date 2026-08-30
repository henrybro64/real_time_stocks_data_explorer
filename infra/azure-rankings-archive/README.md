# Azure SQL Free ranking archive (PoC)

Test-only infrastructure for issues **#165–#167** / cost-safety **#186**.
Provisions the **surrounding** Azure resources for **public historical market
ranking** archives. The Free Offer database itself is **external**.

This module does **not** change the Stock Ticker PostgreSQL path. Personal data
(users, portfolios, watchlists, alerts, API keys) must never be written here.

## What Terraform manages

| Resource | Purpose |
| --- | --- |
| Resource group | Container for the PoC |
| Azure SQL logical server | Host for the archive database |
| Firewall rule | Optional single home/dev client IPv4 |

The Free Offer database name defaults to `rankings_archive_free` (variable only —
not a Terraform-managed database resource).

Explicitly **not** provisioned by this module:

- `azurerm_mssql_database` (would create a **paid** GP serverless DB with the
  released AzureRM provider, which cannot express Free Offer)
- VMs, AKS, Managed Redis, VPN Gateway, Functions, Container Apps, etc.

## Free Offer cost guardrails (#186)

### Why Free Offer is not in `azurerm_mssql_database`

Released AzureRM (the pin in `versions.tf`) does **not** support Free Offer
arguments on `azurerm_mssql_database`. Attributes such as `use_free_limit` /
`free_limit_exhaustion_behavior` are **invalid** for that schema. Adding a
normal `GP_S_Gen5_2` database resource would silently create a **billable**
serverless database.

The live intended database is:

```text
rankings_archive_free
```

Create / enroll Free Offer **outside** this module (Azure Portal, or a future
supported AzAPI/AzureRM path). Then point `sql_database_name` at that database
(default `rankings_archive_free`). Do **not** recreate the deleted paid
`rankings_archive` database (variable validation blocks that name).

### Mandatory live verification (management plane only)

Terraform apply / tags / SKU labels are **not** proof of Free Offer.

```bash
# Needs az login + reader rights. Does NOT open SQL / ODBC.
python3 scripts/verify_azure_rankings_archive_free_offer.py \
  --resource-group <rg> \
  --server <sql-server-name> \
  --database rankings_archive_free
```

Expected live metadata:

```json
{
  "useFreeLimit": true,
  "freeLimitExhaustionBehavior": "AutoPause"
}
```

Exit `1` means the database may be billable. Tags like
`cost-tier=azure-sql-free-offer` and “General Purpose - Serverless” alone do
**not** mean free. `useFreeLimit: null` means Free Offer is **not** active.

### Portal checklist

1. Azure Portal → SQL databases → `rankings_archive_free`
2. Compute + storage: Free offer / Free limit **enabled**
3. Exhaustion: **Auto-pause**
4. Serverless GP_S Gen5, max size ≤ 32 GB
5. Cost Management: no unexpected paid services

### `terraform plan` safety checklist

Plan should only touch:

- `azurerm_resource_group`
- `azurerm_mssql_server`
- `azurerm_mssql_firewall_rule` (optional)
- `random_string` / `random_password`

If the plan proposes creating `azurerm_mssql_database` or any VM/AKS/Redis/etc.,
**do not apply**.

State migration: this module uses a Terraform `removed` block
(`destroy = false`) so a previously managed `azurerm_mssql_database.archive` is
dropped from state **without** destroying the Azure database.

## Prerequisites

- Terraform `>= 1.7` (`removed` / `destroy = false`)
- Azure CLI logged in (`az login`)
- azurerm provider `>= 4.30, < 5` (server / firewall only — **not** Free Offer DB create)
- An existing Free Offer database on the server (default name `rankings_archive_free`)
- ODBC Driver 18 + Python `pyodbc` for app connectivity

## Network access

Terraform sets `public_network_access_enabled = false` (secure-by-default / Sonar).
Optional `allowed_client_ipv4` still creates a firewall allow rule for later use.

For a home/dev backend without Private Link:

1. Apply Terraform (server / RG / firewall).
2. Ensure Free Offer DB exists; run the verify script.
3. In Azure Portal → SQL server → Networking, temporarily enable public access.
4. Confirm your client IP is allowed.
5. Run smoke only when needed, then disable public access again.

## Apply

```bash
cd infra/azure-rankings-archive
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: set allowed_client_ipv4 (and optional password)
# sql_database_name defaults to rankings_archive_free

terraform init
terraform plan
terraform apply

# Mandatory — Terraform cannot prove Free Offer:
python3 ../../scripts/verify_azure_rankings_archive_free_offer.py \
  --resource-group <rg> \
  --server <sql-server-name> \
  --database rankings_archive_free
```

Sensitive outputs:

```bash
terraform output -raw sql_admin_password
terraform output rankings_archive_database_url_example
```

App env (do **not** commit):

```bash
export RANKINGS_ARCHIVE_DATABASE_URL='mssql+pyodbc://USER:URL_ENCODED_PASSWORD@SERVER.database.windows.net:1433/rankings_archive_free?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no'
```

`DATABASE_URL` (Postgres) stays unchanged.

## CI note

Normal GitHub Actions (push / PR) does **not** connect to Azure SQL and does
**not** run Free Offer verification. The live SQL smoke job is **manual
`workflow_dispatch` only** and may wake the serverless database — see
`docs/azure-rankings-archive.md`.
