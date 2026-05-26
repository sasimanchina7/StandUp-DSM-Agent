"""
mcp/server.py
══════════════════════════════════════════════════════════════════════════════
MCP (Model Context Protocol) Server for Personal Standup DSM Bot

Exposes all standup tools as MCP-compliant endpoints over WebSocket.
Any MCP-aware client (Claude Desktop, VS Code extension, custom agent) can
connect and invoke tools without knowing the underlying implementation.

Protocol: JSON-RPC 2.0 over WebSocket
Port    : MCP_PORT (default 8765)

Messages:
  →  {"jsonrpc":"2.0","id":1,"method":"tools/list"}
  ←  {"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}

  →  {"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"store_update","arguments":{...}}}
  ←  {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"..."}]}}
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import asyncio
import json
import logging
import websockets
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    import websockets
    import websockets.server as ws_server
    HAS_WS = True
except ImportError:
    HAS_WS = False

from config import MCP_HOST, MCP_PORT
from harness.context_harness import harness
from tools.memory_tools import store_update, fetch_user_history
from tools.sprint_tools import get_sprint_context
from tools.summary_tools import generate_standup_summary
from tools.code_interpreter import run_code_snippet

logger = logging.getLogger(__name__)

# ─── Tool manifest (MCP schema) ───────────────────────────────────────────────
MCP_TOOLS = [
    {
        "name": "store_update",
        "description": "Save a standup update for a user into memory and S3.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user":      {"type": "string"},
                "sprint_id": {"type": "string"},
                "update":    {"type": "string"},
                "session_id":{"type": "string", "description": "Harness session ID"},
            },
            "required": ["user", "sprint_id", "update"],
        },
    },
    {
        "name": "fetch_user_history",
        "description": "Retrieve the last 7 days of standup updates for a user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user":       {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["user"],
        },
    },
    {
        "name": "generate_standup_summary",
        "description": "Generate a structured standup summary (yesterday/today/blockers/sprint goal).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user":       {"type": "string"},
                "sprint_id":  {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["user", "sprint_id"],
        },
    },
    {
        "name": "get_sprint_context",
        "description": "Retrieve sprint goal, team tasks, and blockers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sprint_id":  {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["sprint_id"],
        },
    },
    {
        "name": "run_code_snippet",
        "description": "Execute a Python snippet via the Code Interpreter Lambda and return stdout/result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code":       {"type": "string", "description": "Python code to execute"},
                "session_id": {"type": "string"},
            },
            "required": ["code"],
        },
    },
]

# ─── Dispatcher ───────────────────────────────────────────────────────────────
def _dispatch(name: str, args: dict) -> Any:
    session_id = args.pop("session_id", f"mcp-{uuid.uuid4().hex[:8]}")
    # Ensure session exists in harness
    harness.open_session(session_id)
    return harness.dispatch(name, args, session_id=session_id)


# ─── JSON-RPC handler ─────────────────────────────────────────────────────────
async def _handle(websocket) -> None:
    client_addr = websocket.remote_address
    logger.info("MCP: client connected %s", client_addr)
    try:
        async for raw in websocket:
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }))
                continue

            rpc_id  = req.get("id")
            method  = req.get("method", "")
            params  = req.get("params", {})

            # ── initialize ────────────────────────────────────────────────────
            if method == "initialize":
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": rpc_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "standup-dsm-mcp", "version": "1.0.0"},
                        "capabilities": {"tools": {}},
                    },
                }))

            # ── tools/list ────────────────────────────────────────────────────
            elif method == "tools/list":
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": rpc_id,
                    "result": {"tools": MCP_TOOLS},
                }))

            # ── tools/call ────────────────────────────────────────────────────
            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = dict(params.get("arguments", {}))
                try:
                    result = _dispatch(tool_name, tool_args)
                    text   = json.dumps(result, default=str)
                    await websocket.send(json.dumps({
                        "jsonrpc": "2.0", "id": rpc_id,
                        "result": {"content": [{"type": "text", "text": text}]},
                    }))
                except Exception as exc:
                    await websocket.send(json.dumps({
                        "jsonrpc": "2.0", "id": rpc_id,
                        "error": {"code": -32603, "message": str(exc)},
                    }))

            # ── ping ──────────────────────────────────────────────────────────
            elif method == "ping":
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": rpc_id, "result": {"pong": True},
                }))

            else:
                await websocket.send(json.dumps({
                    "jsonrpc": "2.0", "id": rpc_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }))

    except Exception as exc:
        logger.warning("MCP: connection error %s: %s", client_addr, exc)
    finally:
        logger.info("MCP: client disconnected %s", client_addr)


# ─── Entry point ──────────────────────────────────────────────────────────────
async def run_server():
    if not HAS_WS:
        logger.error("websockets not installed — pip install websockets")
        return
    logger.info("MCP server starting on ws://%s:%d", MCP_HOST, MCP_PORT)
    async with ws_server.serve(_handle, MCP_HOST, MCP_PORT):
        await asyncio.Future()   # run forever


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_server())