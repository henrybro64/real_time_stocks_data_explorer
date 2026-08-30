variable "project_name" {
  description = "Short name prefix for Azure resources (letters/numbers only preferred)."
  type        = string
  default     = "starchive"
}

variable "location" {
  description = "Azure region for the archive resource group and SQL resources."
  type        = string
  default     = "eastus"
}

variable "resource_group_name" {
  description = "Optional fixed resource group name. Empty creates one from project_name."
  type        = string
  default     = ""
}

variable "sql_server_name" {
  description = "Optional Azure SQL logical server name (globally unique). Empty derives one."
  type        = string
  default     = ""
}

variable "sql_database_name" {
  description = <<-EOT
    Existing Azure SQL Free Offer database name (external to Terraform).
    Default is rankings_archive_free. Do not use the deleted paid name
    rankings_archive (#186).
  EOT
  type        = string
  default     = "rankings_archive_free"

  validation {
    condition     = var.sql_database_name != "rankings_archive"
    error_message = "Do not recreate the deleted paid rankings_archive database; use rankings_archive_free (or another verified Free Offer DB)."
  }
}

variable "sql_admin_login" {
  description = "SQL server administrator login (not an Entra-only admin)."
  type        = string
  default     = "starchive_admin"
}

variable "sql_admin_password" {
  description = "SQL server administrator password. Leave empty to generate a random password."
  type        = string
  default     = ""
  sensitive   = true
}

variable "allowed_client_ipv4" {
  description = <<-EOT
    Optional home/dev IPv4 for an SQL firewall rule. Public network access is
    disabled in Terraform (secure default); enable it in Azure Portal for PoC
    connectivity, then this rule allows the client IP.
  EOT
  type        = string
  default     = ""

  validation {
    condition = (
      var.allowed_client_ipv4 == ""
      || can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.allowed_client_ipv4))
    )
    error_message = "allowed_client_ipv4 must be empty or a dotted IPv4 address."
  }
}

variable "tags" {
  description = "Tags applied to all managed resources."
  type        = map(string)
  default = {
    purpose    = "rankings-archive-poc"
    cost-tier  = "azure-sql-free-offer"
    personal   = "false"
    managed-by = "terraform"
  }
}
