variable "region" {
  description = "Region of the demo host: Mumbai, the nearest to the team and the audience."
  type        = string
  default     = "ap-south-1"
}

variable "aws_profile" {
  description = "Named profile in ~/.aws that applies this configuration."
  type        = string
  default     = "skylink-demo"
}

variable "instance_type" {
  description = "One host runs the whole stack: PostgreSQL, the five processes, the mock gateway and the proxy."
  type        = string
  default     = "t3.medium"
}

variable "db_instance_class" {
  description = "RDS for PostgreSQL. The demo's database is small: 40,000 customers and the guide index."
  type        = string
  default     = "db.t4g.micro"
}

variable "disk_gb" {
  description = "Root volume: the OS, Docker images, the database and the data pack."
  type        = number
  default     = 30
}

variable "gitlab_project_path" {
  description = "The GitLab.com project whose pipeline may deploy."
  type        = string
  default     = "prathap.aneesh/capstone_telecom"
}

variable "deploy_branch" {
  description = "The only branch whose pipelines may deploy. It is protected, so only maintainers push to it."
  type        = string
  default     = "dev"
}
