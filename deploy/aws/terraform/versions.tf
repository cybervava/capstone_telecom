terraform {
  required_version = ">= 1.9"

  # The state lives in S3, in a bucket made once by state-bucket.sh, outside this configuration.
  # It is versioned and encrypted. It has no lock: one operator applies, and Terraform 1.9 can
  # lock only with a DynamoDB table (1.10 and later can lock in S3 with use_lockfile = true).
  backend "s3" {
    bucket  = "skylink-care-demo-tfstate-f106d3ae"
    key     = "demo/terraform.tfstate"
    region  = "ap-south-1"
    profile = "skylink-demo"
    encrypt = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }
}
