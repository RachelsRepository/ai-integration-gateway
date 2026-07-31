variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "container_image" {
  description = "Container image URI for the gateway API."
  type        = string
}

variable "desired_count" {
  description = "Desired number of ECS tasks."
  type        = number
  default     = 2
}

variable "cpu" {
  description = "Fargate task CPU units."
  type        = number
  default     = 1024
}

variable "memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 2048
}

variable "database_secret_arn" {
  description = "Secrets Manager ARN containing the database DSN."
  type        = string
}

variable "redis_url" {
  description = "Redis connection URL."
  type        = string
}

variable "kafka_bootstrap" {
  description = "Kafka bootstrap servers."
  type        = string
}
