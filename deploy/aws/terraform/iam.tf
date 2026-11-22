# Two identities. The host may pull its image and bundle and read its own settings. The pipeline
# may push images, upload bundles and run one SSM document (host.tf) on the host, and nothing else.

# --- The host --------------------------------------------------------------------------------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name               = "${local.name}-host"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# SSM Run Command and Session Manager, instead of SSH.
resource "aws_iam_role_policy_attachment" "host_ssm" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "host" {
  statement {
    sid       = "SignInToEcr"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "PullTheImage"
    actions   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.app.arn]
  }
  statement {
    sid       = "FetchReleaseBundles"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.releases.arn}/releases/*"]
  }
  statement {
    sid       = "ReadItsSettings"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.env.arn]
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "pull-releases-and-settings"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

resource "aws_iam_instance_profile" "host" {
  name = "${local.name}-host"
  role = aws_iam_role.host.name
}

# --- The GitLab pipeline ---------------------------------------------------------------------

resource "aws_iam_openid_connect_provider" "gitlab" {
  url            = "https://gitlab.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "gitlab_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.gitlab.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "gitlab.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Pipelines of the deploy branch of this one project, and no other ref or project.
    condition {
      test     = "StringEquals"
      variable = "gitlab.com:sub"
      values   = ["project_path:${var.gitlab_project_path}:ref_type:branch:ref:${var.deploy_branch}"]
    }
  }
}

resource "aws_iam_role" "deployer" {
  name                 = "${local.name}-gitlab-deploy"
  description          = "Assumed by the GitLab pipeline of ${var.gitlab_project_path} on ${var.deploy_branch}"
  assume_role_policy   = data.aws_iam_policy_document.gitlab_assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deployer" {
  statement {
    sid       = "SignInToEcr"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "PushTheImage"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.app.arn]
  }
  statement {
    sid       = "UploadReleaseBundles"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.releases.arn}/releases/*"]
  }
  # The deploy document only, with a parameter that must be a commit: the pipeline cannot run
  # any other command on the host.
  statement {
    sid       = "RunTheDeployDocument"
    actions   = ["ssm:SendCommand"]
    resources = [aws_instance.host.arn, aws_ssm_document.deploy.arn]
  }
  statement {
    sid       = "ReadItsResult"
    actions   = ["ssm:GetCommandInvocation"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deployer" {
  name   = "publish-and-deploy"
  role   = aws_iam_role.deployer.id
  policy = data.aws_iam_policy_document.deployer.json
}
