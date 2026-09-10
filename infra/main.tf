terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

locals {
  name = "${var.project}-${var.environment}"
  tags = {
    Project = var.project
    Environment = var.environment
    ManagedBy = "terraform"
  }
  application_environment = [
    { name = "AWS_REGION", value = var.aws_region },
    { name = "CALL_DATA_BUCKET", value = aws_s3_bucket.data.id },
    { name = "CALL_DATA_KMS_KEY_ID", value = aws_kms_key.data.arn },
    { name = "CALLS_TABLE", value = aws_dynamodb_table.calls.name },
    { name = "AUDIT_TABLE", value = aws_dynamodb_table.audit.name },
    { name = "ORGANIZATIONS_TABLE", value = aws_dynamodb_table.organizations.name },
    { name = "PROCESSING_QUEUE_URL", value = aws_sqs_queue.processing.url },
    { name = "PUBLIC_BASE_URL", value = var.public_base_url },
    { name = "TWILIO_ACCOUNT_SID", value = var.twilio_account_sid },
    { name = "TENANT_ROUTING_JSON", value = var.tenant_policy_json },
    { name = "REPORT_SENDER", value = var.report_sender },
    { name = "REPORT_RECIPIENTS", value = var.report_recipients },
    { name = "SENTRY_DSN", value = var.sentry_dsn },
    { name = "SENTRY_ENVIRONMENT", value = var.sentry_environment },
    { name = "SENTRY_TRACES_SAMPLE_RATE", value = var.sentry_traces_sample_rate },
  ]
  application_secrets = [
    { name = "OPENAI_API_KEY", valueFrom = var.openai_api_key_arn },
    { name = "TWILIO_AUTH_TOKEN", valueFrom = var.twilio_secret_arn },
    { name = "DASHBOARD_JWT_SECRET", valueFrom = var.dashboard_jwt_secret_arn },
    { name = "CALL_CENTER_WEBHOOK_SECRET", valueFrom = var.call_center_webhook_secret_arn },
  ]
}

resource "aws_kms_key" "data" {
  description = "LeadLens data encryption key"
  enable_key_rotation = true
  deletion_window_in_days = 30
  tags = local.tags
}

resource "aws_s3_bucket" "data" {
  bucket_prefix = "${local.name}-data-"
  tags = local.tags
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id
  block_public_acls = true
  block_public_policy = true
  ignore_public_acls = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id = "retention"
    status = "Enabled"
    filter {}
    expiration {
      days = 365
    }
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

resource "aws_dynamodb_table" "calls" {
  name = "${local.name}-calls"
  billing_mode = "PAY_PER_REQUEST"
  hash_key = "call_sid"
  attribute {
    name = "call_sid"
    type = "S"
  }
  attribute {
    name = "tenant_id"
    type = "S"
  }
  attribute {
    name = "created_at"
    type = "S"
  }
  global_secondary_index {
    name = "tenant-created-at-index"
    hash_key = "tenant_id"
    range_key = "created_at"
    projection_type = "ALL"
  }
  ttl {
    attribute_name = "expires_at"
    enabled = true
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled = true
    kms_key_arn = aws_kms_key.data.arn
  }
  tags = local.tags
}

resource "aws_dynamodb_table" "audit" {
  name = "${local.name}-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key = "event_id"
  attribute {
    name = "event_id"
    type = "S"
  }
  ttl {
    attribute_name = "expires_at"
    enabled = true
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled = true
    kms_key_arn = aws_kms_key.data.arn
  }
  tags = local.tags
}

resource "aws_dynamodb_table" "organizations" {
  name = "${local.name}-organizations"
  billing_mode = "PAY_PER_REQUEST"
  hash_key = "tenant_id"
  attribute {
    name = "tenant_id"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled = true
    kms_key_arn = aws_kms_key.data.arn
  }
  tags = local.tags
}

resource "aws_sqs_queue" "dlq" {
  name = "${local.name}-processing-dlq.fifo"
  fifo_queue = true
  kms_master_key_id = aws_kms_key.data.id
  tags = local.tags
}

resource "aws_sqs_queue" "processing" {
  name = "${local.name}-processing.fifo"
  fifo_queue = true
  content_based_deduplication = true
  visibility_timeout_seconds = 900
  kms_master_key_id = aws_kms_key.data.id
  redrive_policy = jsonencode({ deadLetterTargetArn = aws_sqs_queue.dlq.arn, maxReceiveCount = 5 })
  tags = local.tags
}

data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
  tags = local.tags
}

resource "aws_iam_role" "execution" {
  name = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name = "read-task-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = [var.dashboard_jwt_secret_arn, var.openai_api_key_arn, var.twilio_secret_arn] }] })
}

resource "aws_iam_role_policy" "task" {
  name = "application"
  role = aws_iam_role.task.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:Query", "dynamodb:Scan"], Resource = [aws_dynamodb_table.calls.arn, "${aws_dynamodb_table.calls.arn}/index/*", aws_dynamodb_table.audit.arn, aws_dynamodb_table.organizations.arn] },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.data.arn}/*"] },
    { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:SendMessage", "sqs:GetQueueAttributes"], Resource = aws_sqs_queue.processing.arn },
    { Effect = "Allow", Action = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"], Resource = aws_kms_key.data.arn },
    { Effect = "Allow", Action = ["ses:SendEmail", "bedrock:Retrieve", "bedrock:StartIngestionJob"], Resource = "*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue", "secretsmanager:CreateSecret", "secretsmanager:PutSecretValue", "secretsmanager:DescribeSecret"], Resource = "*" }
  ] })
}

resource "aws_cloudwatch_log_group" "app" {
  name = "/ecs/${local.name}"
  retention_in_days = 90
  kms_key_id = aws_kms_key.data.arn
  tags = local.tags
}

resource "aws_cloudwatch_log_metric_filter" "api_exceptions" {
  name           = "${local.name}-api-exceptions"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "{ $.event = \"request.exception\" }"
  metric_transformation {
    name      = "ApiExceptions"
    namespace = "${local.name}/Application"
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "worker_exceptions" {
  name           = "${local.name}-worker-exceptions"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "{ $.event = \"worker.process.exception\" || $.event = \"worker.queue.exception\" }"
  metric_transformation {
    name      = "WorkerExceptions"
    namespace = "${local.name}/Application"
    value     = "1"
  }
}

resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name = "containerInsights"
    value = "enhanced"
  }
  tags = local.tags
}

resource "aws_security_group" "alb" {
  name = "${local.name}-alb"
  vpc_id = var.vpc_id
  ingress {
    from_port = 443
    to_port = 443
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port = 0
    to_port = 0
    protocol = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "service" {
  name = "${local.name}-service"
  vpc_id = var.vpc_id
  ingress {
    from_port = 8080
    to_port = 8080
    protocol = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port = 443
    to_port = 443
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_lb" "main" {
  name = local.name
  internal = false
  load_balancer_type = "application"
  security_groups = [aws_security_group.alb.id]
  subnets = var.public_subnet_ids
  tags = local.tags
}

resource "aws_lb_target_group" "api" {
  name = "${local.name}-api"
  port = 8080
  protocol = "HTTP"
  vpc_id = var.vpc_id
  target_type = "ip"
  health_check {
    path = "/healthz"
    matcher = "200"
  }
  tags = local.tags
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port = 443
  protocol = "HTTPS"
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn = var.certificate_arn
  default_action {
    type = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_ecs_task_definition" "api" {
  family = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode = "awsvpc"
  cpu = 512
  memory = 1024
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn = aws_iam_role.task.arn
  container_definitions = jsonencode([{ name = "api", image = var.image, essential = true, command = ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"], portMappings = [{ containerPort = 8080 }], environment = local.application_environment, secrets = local.application_secrets, logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.app.name, awslogs-region = var.aws_region, awslogs-stream-prefix = "api" } } }])
}

resource "aws_ecs_task_definition" "worker" {
  family = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode = "awsvpc"
  cpu = 512
  memory = 1024
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn = aws_iam_role.task.arn
  container_definitions = jsonencode([{ name = "worker", image = var.image, essential = true, command = ["python", "worker.py"], environment = local.application_environment, secrets = local.application_secrets, logConfiguration = { logDriver = "awslogs", options = { awslogs-group = aws_cloudwatch_log_group.app.name, awslogs-region = var.aws_region, awslogs-stream-prefix = "worker" } } }])
}

resource "aws_ecs_service" "api" {
  name = "api"
  cluster = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count = 2
  launch_type = "FARGATE"
  network_configuration {
    subnets = var.private_subnet_ids
    security_groups = [aws_security_group.service.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name = "api"
    container_port = 8080
  }
  deployment_circuit_breaker {
    enable = true
    rollback = true
  }
  tags = local.tags
}

resource "aws_ecs_service" "worker" {
  name = "worker"
  cluster = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count = 1
  launch_type = "FARGATE"
  network_configuration {
    subnets = var.private_subnet_ids
    security_groups = [aws_security_group.service.id]
    assign_public_ip = false
  }
  tags = local.tags
}

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
  kms_master_key_id = aws_kms_key.data.id
  tags = local.tags
}

resource "aws_sns_topic_subscription" "alerts" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol = "email"
  endpoint = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "dlq" {
  alarm_name = "${local.name}-dlq-not-empty"
  namespace = "AWS/SQS"
  metric_name = "ApproximateNumberOfMessagesVisible"
  dimensions = { QueueName = aws_sqs_queue.dlq.name }
  statistic = "Maximum"
  period = 300
  evaluation_periods = 1
  threshold = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data = "notBreaching"
  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_exceptions" {
  alarm_name          = "${local.name}-api-exceptions"
  namespace           = "${local.name}/Application"
  metric_name         = "ApiExceptions"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "worker_exceptions" {
  alarm_name          = "${local.name}-worker-exceptions"
  namespace           = "${local.name}/Application"
  metric_name         = "WorkerExceptions"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
