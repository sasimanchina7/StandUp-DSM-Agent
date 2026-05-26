###############################################################################
# terraform/outputs.tf
###############################################################################

output "s3_bucket_name" {
  description = "Name of the standup storage S3 bucket."
  value       = aws_s3_bucket.standup.bucket
}

output "s3_bucket_arn" {
  description = "ARN of the standup storage S3 bucket."
  value       = aws_s3_bucket.standup.arn
}

output "agent_iam_role_arn" {
  description = "IAM role ARN to attach to the Bedrock AgentCore hosted agent."
  value       = aws_iam_role.agent.arn
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group for the agent."
  value       = aws_cloudwatch_log_group.agent.name
}

output "lambda_function_name" {
  description = "Lambda function name (empty if deploy_lambda = false)."
  value       = var.deploy_lambda ? aws_lambda_function.agent[0].function_name : ""
}
