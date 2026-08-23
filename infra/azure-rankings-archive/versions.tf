terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      # Free-offer attributes (`use_free_limit`, `free_limit_exhaustion_behavior`)
      # need a recent azurerm (API 2025-01-01 / provider PR #32277 era).
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
