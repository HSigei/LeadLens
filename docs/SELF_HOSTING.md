# Self-Hosting with Terraform

The `infra` module provisions a production-oriented AWS foundation for LeadLens. It is optional for the hosted SaaS model and useful for organizations that require ownership of their infrastructure and data plane.

## Resources Created

- KMS key with rotation for data encryption.
- Private S3 bucket with public access blocked, versioning, encryption, and lifecycle retention.
- DynamoDB call, audit, and organization tables with point-in-time recovery.
- Encrypted FIFO processing queue and dead-letter queue.
- ECS cluster, API service, worker service, CloudWatch logs, and deployment rollback.
- HTTPS Application Load Balancer using your ACM certificate.
- SNS topic, email subscription, and alarm for messages in the processing DLQ.

## Prerequisites

- Terraform 1.7 or later and AWS CLI credentials for the target account.
- A VPC with two public subnets and two private subnets. Private ECS tasks need controlled outbound HTTPS connectivity.
- An ACM certificate in the target region, a built LeadLens container image, configured AWS Secrets Manager values, verified SES identities, and a reviewed tenant policy.

## Deploy

```powershell
Copy-Item infra\terraform.tfvars.example infra\terraform.tfvars
terraform -chdir=infra init
terraform -chdir=infra validate
terraform -chdir=infra plan
terraform -chdir=infra apply
```

Set real values in `terraform.tfvars` and never commit it. Copy the reviewed policy JSON into `tenant_policy_json`; this is injected into ECS as a sensitive Terraform value. Terraform outputs the load balancer hostname; point your HTTPS DNS name to it and set the matching `PUBLIC_BASE_URL`.

## Important Gaps to Complete

The module consumes secret ARNs but does not create secrets or an ECR image. Create those in the target account before applying. Configure Bedrock Knowledge Base resources separately if knowledge upload/retrieval is enabled. Confirm the SNS email subscription before depending on alerts.

## Master Report Schedule

The module deploys the API and long-running worker. Schedule `python aggregator.py` once daily using an ECS scheduled task, EventBridge Scheduler, Kubernetes CronJob, or another approved scheduler in the target environment. The aggregator needs the same runtime environment and secrets as the worker plus SES sender/recipient configuration.