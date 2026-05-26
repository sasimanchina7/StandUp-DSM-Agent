# Personal Standup DSM Bot
### AWS Bedrock AgentCore — End-to-End PoC

A memory-aware conversational standup assistant that collects daily updates, remembers history across sessions (LTM) and within a session (STM), and generates structured standup summaries — all hosted on AWS Bedrock AgentCore.

---

## Architecture

```
User (Mahsa, Ravi, …)
        │
        ▼
Bedrock AgentCore Hosted Agent   ← agent.py (handler)
        │
   ┌────┴──────────────────────┐
   │                           │
   ▼                           ▼
STM (in-process dict)     LTM (S3 via boto3)
stm_manager.py            ltm_manager.py
                               │
                               ▼
                         S3 Bucket
                  standup-dsm-memory-poc-{env}/
                  ├── users/<name>/<session>.json
                  ├── sprints/<sprint>-summary.json
                  └── summaries/{daily,weekly}/
```

**Tool layer** (`tools/`) — four tools exposed to the LLM via Bedrock Converse:

| Tool | Purpose |
|---|---|
| `store_update` | Persist a standup update to S3 + LTM |
| `fetch_user_history` | Retrieve last 7 days for a user |
| `generate_standup_summary` | Build yesterday / today / blockers / sprint-goal |
| `get_sprint_context` | Retrieve sprint goal and team tasks |

---

## Project Structure

```
personal-standup-bot/
├── agent.py                  ← AgentCore entry point (handler)
├── config.py                 ← All environment-driven config
├── requirements.txt
│
├── memory/
│   ├── stm_manager.py        ← Short-Term Memory (in-process)
│   └── ltm_manager.py        ← Long-Term Memory (S3)
│
├── tools/
│   ├── memory_tools.py       ← store_update, fetch_user_history
│   ├── sprint_tools.py       ← get_sprint_context, update_sprint_summary
│   └── summary_tools.py      ← generate_standup_summary
│
├── terraform/
│   ├── main.tf               ← S3, IAM, CloudWatch, Lambda (optional)
│   ├── variables.tf
│   ├── outputs.tf
│   └── terraform.tfvars.example
│
└── scripts/
    ├── deploy.sh             ← Full end-to-end deploy (infra + agent)
    ├── test_agent.sh         ← CLI smoke test against live agent
    └── local_test.py         ← Python integration test (local creds)
```

---

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.12+ |
| pip | any recent |
| AWS CLI | v2 |
| Terraform | ≥ 1.6 |
| jq | any |

AWS credentials must have permissions for: Bedrock, S3, IAM, CloudWatch Logs.

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo>
cd personal-standup-bot

cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# Edit terraform.tfvars — at minimum set aws_region
```

### 2. Full deploy (infra + agent)

```bash
chmod +x scripts/deploy.sh scripts/test_agent.sh
./scripts/deploy.sh dev        # deploys to us-east-1 by default
```

This single script:
1. Installs Python deps into `package/`
2. Packages `personal-standup-bot.zip`
3. Runs `terraform apply` → creates S3, IAM, CloudWatch
4. Creates a Bedrock Memory runtime
5. Creates / updates the Bedrock AgentCore Hosted Agent
6. Creates an agent alias
7. Writes `.env` with all resource IDs

### 3. Smoke test

```bash
./scripts/test_agent.sh
```

### 4. Run locally

```bash
# Install deps normally (not into package/)
pip install -r requirements.txt

# Export env vars (or load from .env)
export $(grep -v '^#' .env | xargs)

python agent.py          # interactive REPL
# — or —
python scripts/local_test.py   # scripted conversation
```

---

## Configuration

All settings are in `config.py` and read from environment variables:

| Variable | Default | Description |
|---|---|---|
| `AWS_REGION` | `us-east-1` | AWS region |
| `BEDROCK_MODEL_ID` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | Foundation model |
| `BEDROCK_MEMORY_ID` | `your-memory-id` | Bedrock Memory runtime ID (set by deploy script) |
| `S3_BUCKET` | `standup-dsm-memory-poc` | S3 bucket name |
| `SPRINT_ID` | `SPRINT-14` | Active sprint |
| `LTM_DAYS` | `7` | Days of history to surface |

---

## S3 Data Layout

```
standup-dsm-memory-poc-dev/
├── users/
│   ├── mahsa/
│   │   ├── mahsa-session-20260514103000.json
│   │   └── history.json          ← rolling 7-day index
│   └── ravi/
│       └── ...
├── sprints/
│   └── sprint-14-summary.json
└── summaries/
    ├── daily/
    └── weekly/
```

### Session record schema

```json
{
  "session_id": "ravi-session-20260514103000",
  "user":       "Ravi",
  "timestamp":  "2026-05-14T10:30:00+00:00",
  "sprint_id":  "SPRINT-14",
  "sprint_day": "Day 4",
  "date":       "2026-05-14",
  "created_at": "2026-05-14T10:30:00+00:00",
  "updated_at": "2026-05-14T10:30:00+00:00",
  "updates":    ["Completed API testing", "Fixed Kafka retry logic"]
}
```

---

## Sample Conversation

```
You: Hi, I'm Ravi.
Bot: Hi Ravi! 👋 Ready to capture your standup update. What did you work on today?

You: Yesterday I worked on API testing and Kafka retries.
Bot: Got it! I've saved your update for SPRINT-14:
     • API testing
     • Kafka retries
     What are you planning today?

You: What did I work on yesterday?
Bot: Yesterday (2026-05-14) you worked on:
     • API testing
     • Kafka retries

You: What is my sprint goal?
Bot: 🎯 Sprint-14 Goal:
     Complete backend API migration and Kafka consumer stabilisation.

You: Give me my standup summary.
Bot:
📋 Standup Summary — Ravi (SPRINT-14)

✅ Yesterday:
  • API testing
  • Kafka retries

📌 Today:
  • Not yet specified for today.

🚧 Blockers:
  • None reported.

🎯 Sprint Goal: Complete backend API migration and Kafka consumer stabilisation.
```

---

## Manual AWS Console Steps (after deploy.sh)

If you prefer the Console to CLI:

1. **S3** — Verify bucket `standup-dsm-memory-poc-dev` was created with the correct folder structure.

2. **IAM** — Confirm role `standup-dsm-agent-role-dev` exists with S3 + Bedrock permissions.

3. **Bedrock → Memory** — Note the Memory ID printed by `deploy.sh` (also in `.env`).

4. **Bedrock → AgentCore → Agents** — Open `personal-standup-dsm-bot-dev`:
   - Attach the IAM role
   - Set foundation model to Claude 3.5 Sonnet v2
   - Attach the Memory runtime

5. **Test in Console** — Use the built-in test console with prompts from *Sample Conversation* above.

---

## Updating the Sprint

To start a new sprint, update `config.py`:

```python
SPRINT_ID = "SPRINT-15"
SPRINT_GOALS = {
    ...
    "SPRINT-15": "Launch v2 API and complete load-testing.",
}
```

Then re-zip and re-deploy:

```bash
./scripts/deploy.sh dev
```

---

## Future Enhancements

| Feature | Description |
|---|---|
| Vector DB (OpenSearch / Pinecone) | Semantic search over standup history |
| DynamoDB | Faster session metadata retrieval |
| Slack Integration | Native standup workflow in Slack |
| Daily Scheduler (EventBridge) | Auto-prompt team at 9 AM |
| Analytics Dashboard (QuickSight) | Sprint productivity metrics |
| Multi-agent team summaries | Aggregate across all team members |
