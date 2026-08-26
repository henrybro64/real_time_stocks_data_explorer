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
  minimum_tls_version           = "1.2"
  # Secure default: private-only. PoC home backends opt in via enable_public_network_access.
  public_network_access_enabled = var.enable_public_network_access
  tags                          = var.tags
}

# Serverless GP_S Gen5 + free-offer flags: exhaustion must AutoPause (no paid overage).
resource "azurerm_mssql_database" "archive" {
  name        = var.sql_database_name
  server_id   = azurerm_mssql_server.archive.id
  collation   = "SQL_Latin1_General_CP1_CI_AS"
  max_size_gb = 32
  sku_name    = "GP_S_Gen5_2"

  # Serverless idle pause (minutes). Free offer still gates monthly vCore-seconds.
  auto_pause_delay_in_minutes = 60
  min_capacity                = 0.5

  # Azure SQL Database Free offer (one eligible DB / subscription quota rules apply).
  use_free_limit                 = true
  free_limit_exhaustion_behavior = "AutoPause"

  tags = var.tags
}

resource "azurerm_mssql_firewall_rule" "home_client" {
  count = var.enable_public_network_access ? 1 : 0

  name             = "home-dev-client"
  server_id        = azurerm_mssql_server.archive.id
  start_ip_address = var.allowed_client_ipv4
  end_ip_address   = var.allowed_client_ipv4
}

# Allow Azure services only if you later add Azure-hosted callers.
# Left disabled for the home-backend PoC to minimize attack surface.
# resource "azurerm_mssql_firewall_rule" "allow_azure_services" { ... }
