terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "ai-integration-gateway"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

module "network" {
  source = "./modules/network"

  environment = var.environment
  vpc_cidr    = var.vpc_cidr
}

module "ecs" {
  source = "./modules/ecs"

  environment         = var.environment
  vpc_id              = module.network.vpc_id
  private_subnet_ids  = module.network.private_subnet_ids
  public_subnet_ids   = module.network.public_subnet_ids
  container_image     = var.container_image
  desired_count       = var.desired_count
  cpu                 = var.cpu
  memory              = var.memory
  database_secret_arn = var.database_secret_arn
  redis_url           = var.redis_url
  kafka_bootstrap     = var.kafka_bootstrap
}

output "alb_dns_name" {
  description = "Public DNS name of the application load balancer."
  value       = module.ecs.alb_dns_name
}
