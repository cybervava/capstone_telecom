# The demo deployment of SkyLink care on AWS: one host running the containers of
# docker-compose.yml, with Caddy in front for HTTPS (deploy/aws/compose.aws.yml).
#
# The GitLab pipeline builds and scans the image, copies it to ECR, uploads the release bundle to
# S3 and has SSM Run Command start it on the host (.gitlab-ci.yml). It signs in to AWS with the
# pipeline's OIDC token as a role that trusts one branch of one project (iam.tf), so no AWS key is
# stored in GitLab. The host has no SSH port and no key pair; it is reached through SSM.
#
# No secret value is managed here, so none is in the state: the two secrets are created empty and
# `deploy/aws/operate.sh env-init` fills them.

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "skylink-care"
      Purpose   = "capstone-demo"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "host" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = "${var.region}a"
  default_for_az    = true
}

locals {
  name = "skylink-care-demo"
  # A name that resolves to the host's address without a domain of our own, so the certificate
  # authority can check it and issue a certificate.
  site = "${replace(aws_eip.host.public_ip, ".", "-")}.sslip.io"
}

resource "aws_ecr_repository" "app" {
  name = "skylink-care"
  # A tag is a commit and always names the same image, so a rollback gets what was tested.
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 20 newest images, enough to roll back"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

# Release bundles: the compose files, the setup script, the proxy configuration and the host's
# deploy script of one commit, unpacked on the host by the deploy document (host.tf).
resource "aws_s3_bucket" "releases" {
  bucket        = "${local.name}-releases-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "releases" {
  bucket                  = aws_s3_bucket.releases.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "releases" {
  bucket = aws_s3_bucket.releases.id

  rule {
    id     = "expire-old-releases"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

resource "aws_secretsmanager_secret" "env" {
  name                    = "${local.name}/env"
  description             = "The host's .env: generated secrets, the model keys and the demo settings."
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "sign_in" {
  name                    = "${local.name}/sign-in"
  description             = "The shared sign-in to the demo site, for people. The host holds only its hash."
  recovery_window_in_days = 0
}
