"""
Personal Standup DSM Bot - Main Agent Entry Point
AWS Bedrock AgentCore Hosted Agent
"""

import json
import logging
from datetime import datetime, timezone

from config import MEMORY_ID, S3_BUCKET, MODEL_ID, SPRINT_ID, KNOWN_USERS
from memory.stm_manager import STMManager
from memory.ltm_manager import LTMManager
from tools.memory_tools import store_update, fetch_user_history
from tools.sprint_tools import get_sprint_context
from tools.summary_tools import generate_standup_summary

import boto3

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ─── Bedrock Runtime client ───────────────────────────────────────────────────
bedrock_rt = boto3.client("bedrock-runtime", region_name="us-east-1")

# ─── In-process memory managers (persist for the lifetime of the container) ──
stm = STMManager()
ltm = LTMManager()


# ─────────────────────────────────────────────────────────────────────────────
# Tool registry exposed to the LLM
# ─────────────────────────────────────────────────────────────────────────────
TOOLS = [
    {
        "toolSpec": {
            "name": "store_update",
            "description": "Save a standup update for a user into memory and S3.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "user":      {"type": "string", "description": "User name"},
                        "sprint_id": {"type": "string", "description": "Sprint identifier, e.g. SPRINT-14"},
                        "update":    {"type": "string", "description": "The standup update text"},
                    },
                    "required": ["user", "sprint_id", "update"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "fetch_user_history",
            "description": "Fetch the last 7 days of standup updates for a user.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "user": {"type": "string", "description": "User name"},
                    },
                    "required": ["user"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "generate_standup_summary",
            "description": "Generate a structured standup summary for a user.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "user":      {"type": "string", "description": "User name"},
                        "sprint_id": {"type": "string", "description": "Sprint identifier"},
                    },
                    "required": ["user", "sprint_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_sprint_context",
            "description": "Retrieve sprint-related tasks and goals.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "sprint_id": {"type": "string", "description": "Sprint identifier"},
                    },
                    "required": ["sprint_id"],
                }
            },
        }
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Tool dispatcher
# ─────────────────────────────────────────────────────────────────────────────
def dispatch_tool(tool_name: str, tool_input: dict) -> str:
    """Execute the requested tool and return a JSON string result."""
    logger.info("Dispatching tool: %s  input: %s", tool_name, tool_input)
    try:
        if tool_name == "store_update":
            result = store_update(
                user=tool_input["user"],
                sprint_id=tool_input["sprint_id"],
                update=tool_input["update"],
            )
        elif tool_name == "fetch_user_history":
            result = fetch_user_history(user=tool_input["user"])
        elif tool_name == "generate_standup_summary":
            result = generate_standup_summary(
                user=tool_input["user"],
                sprint_id=tool_input["sprint_id"],
            )
        elif tool_name == "get_sprint_context":
            result = get_sprint_context(sprint_id=tool_input["sprint_id"])
        else:
            result = {"error": f"Unknown tool: {tool_name}"}
    except Exception as exc:
        logger.exception("Tool %s failed", tool_name)
        result = {"error": str(exc)}

    return json.dumps(result)


# ─────────────────────────────────────────────────────────────────────────────
# Core agent invocation (agentic loop)
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a Personal Standup DSM Bot — an intelligent, memory-aware
daily standup assistant for an engineering team.

Your responsibilities:
1. Collect and store daily standup updates from team members.
2. Remember what each person worked on across sessions (LTM).
3. Maintain context within the current conversation (STM).
4. Answer questions like "What did I work on yesterday?", "What is my sprint goal?",
   "What are my blockers?".
5. Generate structured standup summaries.

Known team members: Mahsa, Sasi, Sameer, Ravi, Sumanth, Vidhi.
Current sprint: SPRINT-14.

Rules:
- Always greet the user by name once you identify them.
- When a user shares work updates, call store_update immediately.
- When asked about history, call fetch_user_history.
- When asked for a standup summary, call generate_standup_summary.
- Be concise, professional, and supportive.
- If you are unsure of the user's identity, ask them their name.
"""


def invoke_agent(user_message: str, session_id: str, conversation_history: list) -> dict:
    """
    Run one turn of the agentic loop.

    Returns:
        {
            "response": "<final text response>",
            "history":  [<updated conversation history>],
            "session_id": "<session_id>"
        }
    """
    # Append new user message
    conversation_history.append({"role": "user", "content": user_message})

    messages = list(conversation_history)

    while True:
        response = bedrock_rt.converse(
            modelId=MODEL_ID,
            system=[{"text": SYSTEM_PROMPT}],
            messages=messages,
            toolConfig={"tools": TOOLS},
        )

        stop_reason = response["stopReason"]
        output_message = response["output"]["message"]
        messages.append(output_message)

        if stop_reason == "end_turn":
            # Extract text from response
            text_parts = [
                block["text"]
                for block in output_message.get("content", [])
                if "text" in block
            ]
            final_text = "\n".join(text_parts)
            return {
                "response": final_text,
                "history":  messages,
                "session_id": session_id,
            }

        elif stop_reason == "tool_use":
            # Process every tool call in this response
            tool_results = []
            for block in output_message.get("content", []):
                if block.get("type") == "toolUse" or "toolUse" in block:
                    tool_block = block.get("toolUse", block)
                    tool_id    = tool_block["toolUseId"]
                    tool_name  = tool_block["name"]
                    tool_input = tool_block["input"]

                    result_str = dispatch_tool(tool_name, tool_input)
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_id,
                            "content": [{"text": result_str}],
                        }
                    })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            # Loop back — let model continue
        else:
            # Unexpected stop reason — return whatever we have
            text_parts = [
                block.get("text", "")
                for block in output_message.get("content", [])
            ]
            return {
                "response": "\n".join(text_parts),
                "history":  messages,
                "session_id": session_id,
            }


# ─────────────────────────────────────────────────────────────────────────────
# AgentCore handler (entry point called by Bedrock AgentCore runtime)
# ─────────────────────────────────────────────────────────────────────────────
# AgentCore calls handler(event, context) where event contains:
#   event["inputText"]    – the user's message
#   event["sessionId"]    – unique session identifier
#   event["memoryId"]     – (optional) Bedrock Memory ID
#   event["sessionAttributes"] – dict of session-scoped state (we store history here)

def handler(event: dict, context) -> dict:
    """AWS Lambda / Bedrock AgentCore entry point."""
    logger.info("Received event: %s", json.dumps(event, default=str))

    user_message = event.get("inputText", "")
    session_id   = event.get("sessionId", f"session-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")

    # Restore conversation history from session attributes (serialised JSON)
    session_attrs    = event.get("sessionAttributes", {})
    raw_history      = session_attrs.get("conversation_history", "[]")
    conversation_history = json.loads(raw_history) if isinstance(raw_history, str) else raw_history

    result = invoke_agent(user_message, session_id, conversation_history)

    # Serialize updated history back into session attributes
    updated_attrs = dict(session_attrs)
    updated_attrs["conversation_history"] = json.dumps(result["history"])

    return {
        "response":          result["response"],
        "sessionId":         session_id,
        "sessionAttributes": updated_attrs,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Local interactive mode
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Personal Standup DSM Bot (local mode) ===")
    print("Type 'quit' to exit.\n")

    session_id   = f"local-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    history: list = []

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit"):
            break
        if not user_input:
            continue

        result  = invoke_agent(user_input, session_id, history)
        history = result["history"]
        print(f"\nBot: {result['response']}\n")