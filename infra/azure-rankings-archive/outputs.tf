output "resource_group_name" {
  description = "Resource group that owns the archive SQL resources."
  value       = azurerm_resource_group.archive.name
}

output "sql_server_fqdn" {
  description = "Fully qualified domain name of the Azure SQL logical server."
  value       = azurerm_mssql_server.archive.fully_qualified_domain_name
}

output "sql_server_name" {
  description = "Azure SQL logical server name."
  value       = azurerm_mssql_server.archive.name
}

output "sql_database_name" {
  description = "Azure SQL database name."
  value       = azurerm_mssql_database.archive.name
}

output "sql_admin_login" {
  description = "SQL administrator login."
  value       = var.sql_admin_login
}

output "sql_admin_password" {
  description = "SQL administrator password (sensitive)."
  value       = local.sql_admin_password
  sensitive   = true
}

output "rankings_archive_database_url_example" {
  description = "Example SQLAlchemy URL template. Replace PASSWORD; do not commit secrets."
  value       = "mssql+pyodbc://${var.sql_admin_login}:PASSWORD@${azurerm_mssql_server.archive.fully_qualified_domain_name}:1433/${azurerm_mssql_database.archive.name}?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no"
}

output "free_offer_verification" {
  description = "Settings that prove the Free offer + no-overage guardrail."
  value = {
    sku_name                       = azurerm_mssql_database.archive.sku_name
    use_free_limit                 = true
    free_limit_exhaustion_behavior = "AutoPause"
    max_size_gb                    = 32
    portal_hint                    = "Azure Portal → SQL database → Compute + storage → Free offer / Free limit should show enabled with Auto-pause on exhaustion."
  }
}

output "intended_resources" {
  description = "Expected terraform-managed resource types for cost-safety review."
  value = [
    "azurerm_resource_group",
    "azurerm_mssql_server",
    "azurerm_mssql_database",
    "azurerm_mssql_firewall_rule",
    "random_string",
    "random_password",
  ]
}
