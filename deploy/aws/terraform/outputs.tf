output "site_url" {
  description = "The demo site."
  value       = "https://${local.site}"
}

output "site_address" {
  value = local.site
}

output "instance_id" {
  value = aws_instance.host.id
}

output "region" {
  value = var.region
}

output "ecr_repository" {
  value = aws_ecr_repository.app.repository_url
}

output "release_bucket" {
  value = aws_s3_bucket.releases.bucket
}

output "deploy_role_arn" {
  value = aws_iam_role.deployer.arn
}

output "deploy_document" {
  value = aws_ssm_document.deploy.name
}

output "env_secret" {
  value = aws_secretsmanager_secret.env.name
}

output "sign_in_secret" {
  value = aws_secretsmanager_secret.sign_in.name
}
