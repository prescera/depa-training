terraform {
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }

  # Remote state (recommended). Create the state bucket out of band, then
  # uncomment and run `terraform init -migrate-state`.
  # backend "s3" {
  #   bucket       = "<tdp>-terraform-state"
  #   key          = "depa-training/tdp-aws/terraform.tfstate"
  #   region       = "ap-south-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = var.tags
  }
}
