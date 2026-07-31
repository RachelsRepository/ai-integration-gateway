# Deployment guide

## Local

```bash
cp .env.example .env
docker compose up --build
```

The API listens on `http://localhost:8000`. Migrations run as a one-shot Compose service
before the API starts.

## Container image

```bash
docker build -t ai-integration-gateway:1.0.0 .
docker run --rm -p 8000:8000 \
  -e AIGW_ENVIRONMENT=local \
  -e AIGW_AUTH_JWT_ENABLED=false \
  -e AIGW_PROVIDER_ENABLED='["echo"]' \
  -e AIGW_AUTH_API_KEY_PEPPER_REF=literal://change-me \
  ai-integration-gateway:1.0.0
```

Entrypoint roles: `api`, `worker`, `migrate`.

## Terraform

Infrastructure under `deploy/terraform` provisions:

- VPC with public/private subnets and NAT
- Application Load Balancer
- ECS Fargate service with CloudWatch logs
- Security groups locking ALB → service traffic

```bash
cd deploy/terraform
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

Supply environment values from `environments/staging/terraform.tfvars.example`.

## Configuration

All settings use the `AIGW_` prefix. Nested settings use `__`, for example
`AIGW_DB_DSN`, `AIGW_REDIS_URL`, `AIGW_AUTH_JWT_ENABLED`. Secret fields accept
references such as `env://NAME`, `file:///path` or `literal://value` (local only).

## Migrations

```bash
alembic upgrade head
```
