terraform {
  # `removed` with lifecycle.destroy=false needs Terraform >= 1.7 (#186).
  required_version = ">= 1.7.0"

  required_providers {
    azurerm = {
      # Released AzureRM in this pin range does NOT support Free Offer arguments
      # on azurerm_mssql_database. Do not add use_free_limit /
      # free_limit_exhaustion_behavior here — they are invalid for the schema
      # and would not enroll Free Offer. Upstream support (provider PR #32277 /
      # free_limit_enabled) is not assumed available. The archive DB is an
      # external Free Offer resource; see main.tf + README (#186).
      source  = "hashicorp/azurerm"
      version = ">= 4.30.0, < 5.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Local state only for this PoC. Do not commit *.tfstate.
  # For shared work later, migrate to a remote backend outside git.
}

provider "azurerm" {
  features {}
}
