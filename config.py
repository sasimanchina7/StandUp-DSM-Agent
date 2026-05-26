"""
config.py — Central configuration for Personal Standup DSM Bot
All values are environment-driven; safe defaults are provided for local dev.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Auto-load .env when running locally
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

# ─── AWS / Bedrock ────────────────────────────────────────────────────────────
AWS_REGION   = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID     = os.environ.get("BEDROCK_MODEL_ID",
               "anthropic.claude-3-5-sonnet-20241022-v2:0")

# Bedrock Memory Runtime ID (wired in by deploy.sh → written to .env)
MEMORY_ID    = os.environ.get("BEDROCK_MEMORY_ID", "")

# AgentCore IDs
AGENT_ID           = os.environ.get("BEDROCK_AGENT_ID", "")
AGENT_ALIAS_ID     = os.environ.get("BEDROCK_AGENT_ALIAS_ID", "")

# ─── S3 ───────────────────────────────────────────────────────────────────────
S3_BUCKET    = os.environ.get("S3_BUCKET", "standup-dsm-memory-poc-dev")

# ─── Sprint ───────────────────────────────────────────────────────────────────
SPRINT_ID    = os.environ.get("SPRINT_ID", "SPRINT-14")
SPRINT_GOALS = {
    "SPRINT-14": "Complete backend API migration and Kafka consumer stabilisation.",
    "SPRINT-15": "Launch v2 API and complete load-testing.",
}

# ─── Team ─────────────────────────────────────────────────────────────────────
KNOWN_USERS  = ["Mahsa", "Sasi", "Sameer", "Ravi", "Sumanth", "Vidhi"]

# ─── LTM window ───────────────────────────────────────────────────────────────
LTM_DAYS     = int(os.environ.get("LTM_DAYS", "7"))

# ─── MCP Server ───────────────────────────────────────────────────────────────
MCP_HOST     = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT     = int(os.environ.get("MCP_PORT", "8765"))

# ─── AG-UI ────────────────────────────────────────────────────────────────────
AGUI_HOST    = os.environ.get("AGUI_HOST", "0.0.0.0")
AGUI_PORT    = int(os.environ.get("AGUI_PORT", "8000"))

# ─── Code Interpreter Lambda name ─────────────────────────────────────────────
CODE_INTERPRETER_LAMBDA = os.environ.get(
    "CODE_INTERPRETER_LAMBDA", "standup-code-interpreter-dev"
)