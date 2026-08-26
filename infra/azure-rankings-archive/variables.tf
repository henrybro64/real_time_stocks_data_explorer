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
  description = "Azure SQL database name for the ranking archive PoC."
  type        = string
  default     = "rankings_archive"
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

variable "enable_public_network_access" {
  description = <<-EOT
    When true, the SQL server accepts public-endpoint connections (still gated by
    firewall rules). Default false satisfies secure-by-default / Sonar expectations.
    Home/dev PoC callers must set this true and set allowed_client_ipv4.
  EOT
  type        = bool
  default     = false
}

variable "allowed_client_ipv4" {
  description = "Home/dev IPv4 allowed through the SQL firewall when public access is enabled."
  type        = string
  default     = ""

  validation {
    condition = (
      var.allowed_client_ipv4 == ""
      || can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.allowed_client_ipv4))
    )
    error_message = "allowed_client_ipv4 must be empty or a dotted IPv4 address."
  }

  validation {
    condition = (
      !var.enable_public_network_access
      || can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.allowed_client_ipv4))
    )
    error_message = "allowed_client_ipv4 is required when enable_public_network_access is true."
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
