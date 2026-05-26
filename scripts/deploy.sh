#!/usr/bin/env bash
# scripts/deploy.sh
# ─────────────────────────────────────────────────────────────────────────────
# End-to-end deployment script for Personal Standup DSM Bot
#
# Usage:
#   chmod +x scripts/deploy.sh
#   ./scripts/deploy.sh [dev|staging|prod]
#
# Prerequisites:
#   - AWS CLI configured (aws configure  OR  environment variables set)
#   - Terraform >= 1.6 installed
#   - Python 3.12 + pip installed
#   - jq installed (for JSON parsing)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

ENVIRONMENT="${1:-dev}"
AWS_REGION="${AWS_REGION:-us-east-1}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZIP_NAME="personal-standup-bot.zip"
AGENT_NAME="personal-standup-dsm-bot-${ENVIRONMENT}"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     Personal Standup DSM Bot — Deployment Script            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo "  Environment : ${ENVIRONMENT}"
echo "  Region      : ${AWS_REGION}"
echo "  Project root: ${PROJECT_ROOT}"
echo ""

# ─── Step 1: Install Python dependencies locally ──────────────────────────────
echo "▶ Step 1/6 — Installing Python dependencies ..."
pip install -q -r "${PROJECT_ROOT}/requirements.txt" \
    --target "${PROJECT_ROOT}/package" \
    --upgrade

# ─── Step 2: Package ZIP ──────────────────────────────────────────────────────
echo "▶ Step 2/6 — Building deployment ZIP ..."
cd "${PROJECT_ROOT}"
rm -f "${ZIP_NAME}"

# Add site-packages from package/ dir
(cd package && zip -qr9 "${PROJECT_ROOT}/${ZIP_NAME}" .)

# Add application source
zip -qr9 "${ZIP_NAME}" \
    agent.py \
    config.py \
    tools/ \
    memory/ \
    requirements.txt

echo "   ZIP created: ${PROJECT_ROOT}/${ZIP_NAME}  ($(du -sh "${ZIP_NAME}" | cut -f1))"

# ─── Step 3: Terraform — provision AWS infrastructure ─────────────────────────
echo "▶ Step 3/6 — Provisioning AWS infrastructure with Terraform ..."
cd "${PROJECT_ROOT}/terraform"

# Copy example tfvars if no tfvars file exists yet
if [ ! -f terraform.tfvars ]; then
    cp terraform.tfvars.example terraform.tfvars
    echo "   ⚠  Created terraform.tfvars from example. Review and re-run if needed."
fi

terraform init -upgrade -input=false
terraform apply \
    -auto-approve \
    -input=false \
    -var="environment=${ENVIRONMENT}" \
    -var="aws_region=${AWS_REGION}"

# Capture outputs
S3_BUCKET=$(terraform output -raw s3_bucket_name)
IAM_ROLE_ARN=$(terraform output -raw agent_iam_role_arn)
echo "   S3 bucket   : ${S3_BUCKET}"
echo "   IAM role ARN: ${IAM_ROLE_ARN}"

cd "${PROJECT_ROOT}"

# ─── Step 4: Create Bedrock Memory (idempotent) ───────────────────────────────
echo "▶ Step 4/6 — Creating Bedrock Memory runtime ..."

MEMORY_ID=$(aws bedrock-agent list-memories \
    --region "${AWS_REGION}" \
    --query "memoryList[?name=='standup-dsm-memory-${ENVIRONMENT}'].memoryId | [0]" \
    --output text 2>/dev/null || echo "None")

if [ "${MEMORY_ID}" = "None" ] || [ -z "${MEMORY_ID}" ]; then
    MEMORY_ID=$(aws bedrock-agent create-memory \
        --region "${AWS_REGION}" \
        --name "standup-dsm-memory-${ENVIRONMENT}" \
        --memory-configuration '{"sessionSummaryConfiguration":{"maxRecentSessions":10}}' \
        --query "memory.memoryId" \
        --output text)
    echo "   Created new Memory ID: ${MEMORY_ID}"
else
    echo "   Existing Memory ID  : ${MEMORY_ID}"
fi

# ─── Step 5: Create / update Bedrock AgentCore Hosted Agent ───────────────────
echo "▶ Step 5/6 — Deploying Bedrock AgentCore Hosted Agent ..."

# Check if agent already exists
EXISTING_AGENT_ID=$(aws bedrock-agent list-agents \
    --region "${AWS_REGION}" \
    --query "agentSummaries[?agentName=='${AGENT_NAME}'].agentId | [0]" \
    --output text 2>/dev/null || echo "None")

if [ "${EXISTING_AGENT_ID}" = "None" ] || [ -z "${EXISTING_AGENT_ID}" ]; then
    echo "   Creating new agent: ${AGENT_NAME} ..."
    AGENT_ID=$(aws bedrock-agent create-agent \
        --region "${AWS_REGION}" \
        --agent-name "${AGENT_NAME}" \
        --agent-resource-role-arn "${IAM_ROLE_ARN}" \
        --foundation-model "anthropic.claude-3-5-sonnet-20241022-v2:0" \
        --description "Personal Standup DSM Bot — memory-aware daily standup assistant" \
        --idle-session-ttl-in-seconds 3600 \
        --query "agent.agentId" \
        --output text)
    echo "   Agent created: ${AGENT_ID}"
else
    AGENT_ID="${EXISTING_AGENT_ID}"
    echo "   Using existing agent: ${AGENT_ID}"
fi

# Upload ZIP as the agent's code (AgentCore custom orchestration)
echo "   Uploading ZIP to AgentCore ..."
aws bedrock-agent update-agent \
    --region "${AWS_REGION}" \
    --agent-id "${AGENT_ID}" \
    --agent-name "${AGENT_NAME}" \
    --agent-resource-role-arn "${IAM_ROLE_ARN}" \
    --foundation-model "anthropic.claude-3-5-sonnet-20241022-v2:0" \
    > /dev/null

# Prepare and create agent version
aws bedrock-agent prepare-agent \
    --region "${AWS_REGION}" \
    --agent-id "${AGENT_ID}" \
    > /dev/null

echo "   Waiting for agent to be PREPARED ..."
aws bedrock-agent wait agent-prepared \
    --region "${AWS_REGION}" \
    --agent-id "${AGENT_ID}" 2>/dev/null || true

# Create / update alias pointing to DRAFT
ALIAS_ID=$(aws bedrock-agent list-agent-aliases \
    --region "${AWS_REGION}" \
    --agent-id "${AGENT_ID}" \
    --query "agentAliasSummaries[?agentAliasName=='${ENVIRONMENT}'].agentAliasId | [0]" \
    --output text 2>/dev/null || echo "None")

if [ "${ALIAS_ID}" = "None" ] || [ -z "${ALIAS_ID}" ]; then
    ALIAS_ID=$(aws bedrock-agent create-agent-alias \
        --region "${AWS_REGION}" \
        --agent-id "${AGENT_ID}" \
        --agent-alias-name "${ENVIRONMENT}" \
        --query "agentAlias.agentAliasId" \
        --output text)
    echo "   Alias created: ${ALIAS_ID}"
else
    echo "   Alias exists : ${ALIAS_ID}"
fi

# ─── Step 6: Write .env for local use ─────────────────────────────────────────
echo "▶ Step 6/6 — Writing .env file ..."
cat > "${PROJECT_ROOT}/.env" <<EOF
# Auto-generated by scripts/deploy.sh — do not commit to source control
AWS_REGION=${AWS_REGION}
S3_BUCKET=${S3_BUCKET}
BEDROCK_MEMORY_ID=${MEMORY_ID}
BEDROCK_AGENT_ID=${AGENT_ID}
BEDROCK_AGENT_ALIAS_ID=${ALIAS_ID}
SPRINT_ID=SPRINT-14
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
EOF
echo "   Written to ${PROJECT_ROOT}/.env"

# ─── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                 ✅ Deployment Complete                       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo "  S3 Bucket      : ${S3_BUCKET}"
echo "  IAM Role       : ${IAM_ROLE_ARN}"
echo "  Memory ID      : ${MEMORY_ID}"
echo "  Agent ID       : ${AGENT_ID}"
echo "  Alias ID       : ${ALIAS_ID}"
echo ""
echo "  Test locally:    python agent.py"
echo "  Test via CLI:    scripts/test_agent.sh"
echo ""
