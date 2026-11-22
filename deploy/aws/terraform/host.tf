# The host, its address and firewall, and the one SSM document the pipeline may run on it.

# Canonical's current Ubuntu 24.04 image. The instance ignores later releases of it (below), so a
# new image never replaces the host and its database.
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

resource "aws_security_group" "host" {
  name        = local.name
  description = "SkyLink care demo host: HTTPS and the HTTP redirect in, nothing else"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each = {
    http  = { port = 80, why = "HTTP: the certificate challenge and the redirect to HTTPS" }
    https = { port = 443, why = "HTTPS" }
  }
  security_group_id = aws_security_group.host.id
  description       = each.value.why
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.host.id
  description       = "Out: images, packages, AWS APIs, OpenAI, the certificate authority"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_instance" "host" {
  ami                    = data.aws_ssm_parameter.ubuntu.insecure_value
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.host.id
  vpc_security_group_ids = [aws_security_group.host.id]
  iam_instance_profile   = aws_iam_instance_profile.host.name

  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    region     = var.region
    env_secret = aws_secretsmanager_secret.env.arn
    repository = aws_ecr_repository.app.repository_url
  })

  # IMDSv2 only, and out of reach of the containers (one hop: the host itself).
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.disk_gb
    encrypted   = true
  }

  tags = {
    Name = local.name
  }

  lifecycle {
    ignore_changes = [ami, user_data]
  }
}

resource "aws_eip" "host" {
  domain   = "vpc"
  instance = aws_instance.host.id

  tags = {
    Name = local.name
  }
}

resource "aws_ssm_document" "deploy" {
  name            = "${local.name}-deploy"
  document_type   = "Command"
  document_format = "JSON"
  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Start one SkyLink care release on the demo host and check it; if the checks fail, the release before it is started again."
    parameters = {
      Release = {
        type           = "String"
        description    = "The commit's short SHA: the image tag in ECR and the bundle's name in S3"
        allowedPattern = "^[0-9a-f]{8,40}$"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "deploy"
      inputs = {
        timeoutSeconds = "900"
        runCommand = [
          "set -eu",
          "export PATH=\"$PATH:/snap/bin\"",
          "dir=/opt/skylink/releases/{{ Release }}",
          "rm -rf \"$dir\" && mkdir -p \"$dir\"",
          "aws s3 cp \"s3://${aws_s3_bucket.releases.bucket}/releases/{{ Release }}.tgz\" \"$dir.tgz\" --only-show-errors --region ${var.region}",
          "tar -xzf \"$dir.tgz\" -C \"$dir\" && rm -f \"$dir.tgz\"",
          "exec bash \"$dir/deploy/aws/host/deploy.sh\" deploy {{ Release }}",
        ]
      }
    }]
  })
}
