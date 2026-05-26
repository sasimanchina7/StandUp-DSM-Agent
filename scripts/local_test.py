#!/usr/bin/env python3
"""
scripts/local_test.py
─────────────────────
Quick local integration test — fires a scripted conversation through
agent.invoke_agent() using your real AWS credentials.

Usage:
    # Export AWS creds and env vars first:
    export AWS_REGION=us-east-1
    export S3_BUCKET=standup-dsm-memory-poc-dev
    export BEDROCK_MEMORY_ID=<your-memory-id>

    python scripts/local_test.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from agent import invoke_agent
from datetime import datetime, timezone

TURNS = [
    "Hi, I'm Sameer.",
    "Yesterday I worked on DSM integration and memory testing.",
    "I also reviewed the Kafka consumer retry logic.",
    "What did I work on yesterday?",
    "What is my sprint goal?",
    "Generate my standup summary.",
    "Are there any blockers for me?",
]

def main():
    session_id = f"local-test-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    history    = []

    print("\n=== Local Integration Test ===\n")

    for turn in TURNS:
        print(f"[YOU]  {turn}")
        result  = invoke_agent(turn, session_id, history)
        history = result["history"]
        print(f"[BOT]  {result['response']}\n")

if __name__ == "__main__":
    main()
