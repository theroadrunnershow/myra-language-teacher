terraform {
  required_version = ">= 1.10"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  backend "gcs" {
    bucket = "myra-tfstate"
    prefix = "myra/terraform.tfstate"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
