###############################################################################
# terraform/variables.tf
###############################################################################

variable "aws_region" {
  description = "AWS region to deploy resources into."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment tag (dev / staging / prod)."
  type        = string
  default     = "dev"
}

variable "team_members" {
  description = "Pre-configured team member names — used to seed S3 folders."
  type        = list(string)
  default     = ["Mahsa", "Sasi", "Sameer", "Ravi", "Sumanth", "Vidhi"]
}

variable "sprint_id" {
  description = "Active sprint identifier."
  type        = string
  default     = "SPRINT-14"
}

variable "bedrock_model_id" {
  description = "Bedrock foundation model ID for the agent."
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
}

variable "bedrock_memory_id" {
  description = "Bedrock Memory runtime ID (obtained after memory is created)."
  type        = string
  default     = ""
}

variable "deploy_lambda" {
  description = "Set to true to also deploy a Lambda function for local tool testing."
  type        = bool
  default     = false
}
