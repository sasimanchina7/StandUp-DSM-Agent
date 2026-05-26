###############################################################################
# terraform/main.tf
# Provisions all AWS resources required by the Personal Standup DSM Bot.
###############################################################################

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ─── Local helpers ────────────────────────────────────────────────────────────
locals {
  name_prefix = "standup-dsm"
  tags = {
    Project     = "personal-standup-bot"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

###############################################################################
# S3 — standup storage
###############################################################################
resource "aws_s3_bucket" "standup" {
  bucket        = "${local.name_prefix}-memory-poc-${var.environment}"
  force_destroy = true
  tags          = local.tags
}

resource "aws_s3_bucket_versioning" "standup" {
  bucket = aws_s3_bucket.standup.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "standup" {
  bucket = aws_s3_bucket.standup.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "standup" {
  bucket                  = aws_s3_bucket.standup.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Seed the folder structure
resource "aws_s3_object" "users_prefix" {
  for_each = toset(var.team_members)
  bucket   = aws_s3_bucket.standup.id
  key      = "users/${lower(each.value)}/.keep"
  content  = ""
}

resource "aws_s3_object" "sprints_prefix" {
  bucket  = aws_s3_bucket.standup.id
  key     = "sprints/.keep"
  content = ""
}

resource "aws_s3_object" "summaries_daily" {
  bucket  = aws_s3_bucket.standup.id
  key     = "summaries/daily/.keep"
  content = ""
}

resource "aws_s3_object" "summaries_weekly" {
  bucket  = aws_s3_bucket.standup.id
  key     = "summaries/weekly/.keep"
  content = ""
}

###############################################################################
# IAM — Role assumed by Bedrock AgentCore / Lambda
###############################################################################
data "aws_iam_policy_document" "assume_bedrock" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type = "Service"
      identifiers = [
        "bedrock.amazonaws.com",
        "lambda.amazonaws.com",
      ]
    }
  }
}

resource "aws_iam_role" "agent" {
  name               = "${local.name_prefix}-agent-role-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.assume_bedrock.json
  tags               = local.tags
}

data "aws_iam_policy_document" "agent_permissions" {
  # S3 access
  statement {
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.standup.arn,
      "${aws_s3_bucket.standup.arn}/*",
    ]
  }

  # Bedrock model invocation
  statement {
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = ["arn:aws:bedrock:${var.aws_region}::foundation-model/*"]
  }

  # Bedrock Memory (AgentCore)
  statement {
    effect  = "Allow"
    actions = [
      "bedrock:CreateMemory",
      "bedrock:GetMemory",
      "bedrock:UpdateMemory",
      "bedrock:DeleteMemory",
      "bedrock:ListMemories",
      "bedrock:RetrieveAndGenerateWithMemory",
    ]
    resources = ["*"]
  }

  # CloudWatch Logs
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.aws_region}:*:log-group:/aws/lambda/${local.name_prefix}*"]
  }
}

resource "aws_iam_policy" "agent" {
  name   = "${local.name_prefix}-agent-policy-${var.environment}"
  policy = data.aws_iam_policy_document.agent_permissions.json
  tags   = local.tags
}

resource "aws_iam_role_policy_attachment" "agent" {
  role       = aws_iam_role.agent.name
  policy_arn = aws_iam_policy.agent.arn
}

###############################################################################
# CloudWatch Log Group
###############################################################################
resource "aws_cloudwatch_log_group" "agent" {
  name              = "/aws/bedrock-agentcore/${local.name_prefix}-${var.environment}"
  retention_in_days = 14
  tags              = local.tags
}

###############################################################################
# Lambda (optional — for local tool testing before AgentCore upload)
###############################################################################
resource "aws_lambda_function" "agent" {
  count = var.deploy_lambda ? 1 : 0

  function_name = "${local.name_prefix}-agent-${var.environment}"
  role          = aws_iam_role.agent.arn
  handler       = "agent.handler"
  runtime       = "python3.12"
  timeout       = 120
  memory_size   = 512

  filename         = "${path.module}/../personal-standup-bot.zip"
  source_code_hash = filebase64sha256("${path.module}/../personal-standup-bot.zip")

  environment {
    variables = {
      S3_BUCKET         = aws_s3_bucket.standup.bucket
      AWS_REGION        = var.aws_region
      SPRINT_ID         = var.sprint_id
      BEDROCK_MODEL_ID  = var.bedrock_model_id
      BEDROCK_MEMORY_ID = var.bedrock_memory_id
    }
  }

  tags = local.tags
}

resource "aws_cloudwatch_log_group" "lambda" {
  count             = var.deploy_lambda ? 1 : 0
  name              = "/aws/lambda/${local.name_prefix}-agent-${var.environment}"
  retention_in_days = 14
  tags              = local.tags
}
