#!/usr/bin/env bash
# scripts/test_agent.sh
# ─────────────────────────────────────────────────────────────────────────────
# Smoke-test the deployed Bedrock AgentCore agent via AWS CLI
# Loads values from .env written by deploy.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

if [ ! -f "${ENV_FILE}" ]; then
    echo "❌ .env not found — run scripts/deploy.sh first."
    exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

SESSION_ID="test-session-$(date +%Y%m%d%H%M%S)"

run_turn() {
    local prompt="$1"
    echo "──────────────────────────────────────────"
    echo "▶ You: ${prompt}"
    RESPONSE=$(aws bedrock-agent-runtime invoke-agent \
        --region "${AWS_REGION}" \
        --agent-id "${BEDROCK_AGENT_ID}" \
        --agent-alias-id "${BEDROCK_AGENT_ALIAS_ID}" \
        --session-id "${SESSION_ID}" \
        --input-text "${prompt}" \
        --query "completion" \
        --output text 2>/dev/null)
    echo "🤖 Bot: ${RESPONSE}"
    echo ""
}

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║     Personal Standup DSM Bot — Smoke Test                   ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

run_turn "Hi, I'm Ravi. Yesterday I worked on API testing and Kafka retries."
sleep 2
run_turn "I also fixed a blocker related to the authentication middleware."
sleep 2
run_turn "What did I work on yesterday?"
sleep 2
run_turn "What is my sprint goal?"
sleep 2
run_turn "Give me my full standup summary."

echo "✅ Smoke test complete."
