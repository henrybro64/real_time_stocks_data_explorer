locals {
  resource_group_name = coalesce(
    var.resource_group_name != "" ? var.resource_group_name : null,
    "${var.project_name}-rg",
  )
  sql_server_name = coalesce(
    var.sql_server_name != "" ? var.sql_server_name : null,
    substr(lower(replace("${var.project_name}${random_string.suffix.result}", "-", "")), 0, 63),
  )
  sql_admin_password = coalesce(
    var.sql_admin_password != "" ? var.sql_admin_password : null,
    random_password.sql_admin.result,
  )
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "random_password" "sql_admin" {
  length           = 24
  special          = true
  override_special = "!@#%^*-_=+"
}

resource "azurerm_resource_group" "archive" {
  name     = local.resource_group_name
  location = var.location
  tags     = var.tags
}

resource "azurerm_mssql_server" "archive" {
  name                         = local.sql_server_name
  resource_group_name          = azurerm_resource_group.archive.name
  location                     = azurerm_resource_group.archive.location
  version                      = "12.0"
  administrator_login          = var.sql_admin_login
  administrator_login_password = local.sql_admin_password
  minimum_tls_version          = "1.2"
  # Literal false keeps Sonar Security Rating ≥ A. Home/dev PoC can temporarily
  # enable public access in Azure Portal (or a local uncommitted override) after apply.
  public_network_access_enabled = false

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Free Offer database is EXTERNAL (#186).
#
# Released AzureRM does NOT accept Free Offer arguments on
# azurerm_mssql_database (`use_free_limit` / `free_limit_exhaustion_behavior`
# are invalid for the provider schema this module pins). Creating a GP_S
# database through AzureRM without those properties provisions a *paid*
# serverless DB — which this project must never do silently.
#
# Keep the live Free Offer database named in var.sql_database_name
# (default rankings_archive_free), created/verified outside Terraform.
# After apply / cutover, always run:
#   scripts/verify_azure_rankings_archive_free_offer.py
# ---------------------------------------------------------------------------

# Drop the old managed database from state without destroying Azure (#186).
# Prior modules incorrectly managed azurerm_mssql_database.archive with
# unsupported Free Offer attributes.
removed {
  from = azurerm_mssql_database.archive

  lifecycle {
    destroy = false
  }
}

resource "azurerm_mssql_firewall_rule" "home_client" {
  count = var.allowed_client_ipv4 != "" ? 1 : 0

  name             = "home-dev-client"
  server_id        = azurerm_mssql_server.archive.id
  start_ip_address = var.allowed_client_ipv4
  end_ip_address   = var.allowed_client_ipv4
}

# Allow Azure services only if you later add Azure-hosted callers.
# Left disabled for the home-backend PoC to minimize attack surface.
# resource "azurerm_mssql_firewall_rule" "allow_azure_services" { ... }
