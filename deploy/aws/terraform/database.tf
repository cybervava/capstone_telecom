# PostgreSQL on Amazon RDS, in place of the container on the host.
#
# Two users. RDS's master user keeps its password in Secrets Manager, where RDS rotates it; it is
# never seen here or in the state. The platform logs in as its own role, `care`, with the password
# of the host's settings, and owns its tables and nothing more. Before every start the host's
# deploy script makes sure, as the master user, that the role, its password and the vector
# extension are there (deploy/aws/host/db_bootstrap.py), so a rotation of the master password
# never touches the platform.

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "SkyLink care demo database: PostgreSQL from the demo host only"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_host" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from the demo host"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.host.id
}

resource "aws_db_instance" "main" {
  identifier        = local.name
  engine            = "postgres"
  engine_version    = "17.11"
  instance_class    = var.db_instance_class
  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name                     = "care"
  username                    = "care_admin"
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  # No zone is named: RDS places it where there is room. On 22 Nov the host's own zone,
  # ap-south-1a, had no db.t4g.micro to give, and a hop to the next zone costs a millisecond.
  publicly_accessible = false
  multi_az            = false

  backup_retention_period = 1
  # No restart by a minor upgrade in the week of the presentation.
  auto_minor_version_upgrade = false
  apply_immediately          = true
  # The demo is destroyed after the presentation, and every release starts from a clean demo state.
  deletion_protection = false
  skip_final_snapshot = true

  tags = {
    Name = local.name
  }
}

# Where the host finds the database: its address, and which secret holds the master password.
# Nothing in it is a secret.
resource "aws_ssm_parameter" "database" {
  name = "/${local.name}/database"
  type = "String"
  value = jsonencode({
    host          = aws_db_instance.main.address
    port          = aws_db_instance.main.port
    master_secret = aws_db_instance.main.master_user_secret[0].secret_arn
  })
}
