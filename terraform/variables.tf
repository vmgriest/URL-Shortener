variable "aws_region" {
  description = "AWS region"
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefix applied to every resource name"
  default     = "url-shortener"
}

variable "db_password" {
  description = "RDS PostgreSQL master password"
  sensitive   = true
}

variable "app_image_tag" {
  description = "Docker image tag to deploy from ECR"
  default     = "latest"
}
