"""
harness/context_harness.py
══════════════════════════════════════════════════════════════════════════════
Central Context Management Harness

Every tool call, MCP request, Lambda invocation, and AG-UI message passes
through this harness.  It owns:

  • SessionStore   — per-user, per-sprint session registry (in-process + S3)
  • ContextFrame   — the rich context blob injected into every LLM call
  • HarnessMetrics — lightweight telemetry (tool calls, latency, errors)
  • HarnessMiddleware — pre/post hooks wrapping all tool dispatches

The harness is the single source of truth for "who is talking, in which
sprint, with which history" at any point in time.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import boto3
from botocore.exceptions import ClientError

from config import (
    AWS_REGION, S3_BUCKET, MEMORY_ID, SPRINT_ID, KNOWN_USERS,
    MODEL_ID, LTM_DAYS,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ToolCall:
    """Record of a single tool invocation."""
    tool_name:   str
    tool_input:  dict
    tool_output: Any       = None
    error:       str       = ""
    latency_ms:  float     = 0.0
    timestamp:   str       = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tool_use_id: str       = field(default_factory=lambda: f"tc-{uuid.uuid4().hex[:8]}")


@dataclass
class ContextFrame:
    """
    Rich context snapshot injected into every LLM call and tool dispatch.
    All fields are JSON-serialisable.
    """
    session_id:       str
    user:             Optional[str]  = None
    sprint_id:        str            = SPRINT_ID
    memory_id:        str            = MEMORY_ID
    s3_bucket:        str            = S3_BUCKET

    # STM
    recent_messages:  list[dict]     = field(default_factory=list)   # last N turns
    active_topic:     Optional[str]  = None
    active_task:      Optional[str]  = None

    # LTM summary (injected from S3)
    ltm_summary:      Optional[str]  = None

    # Tool trace for this session
    tool_calls:       list[ToolCall] = field(default_factory=list)

    # AG-UI streaming token buffer
    stream_tokens:    list[str]      = field(default_factory=list)

    # Metadata
    created_at:       str            = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at:       str            = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    turn_count:       int            = 0

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.turn_count += 1

    def to_dict(self) -> dict:
        d = asdict(self)
        # ToolCall objects serialise cleanly via asdict
        return d

    def system_context_block(self) -> str:
        """
        Returns a compact text block injected into the system prompt so the
        LLM always knows the current session context without repeating it
        in every user message.
        """
        lines = [
            f"[HARNESS CONTEXT]",
            f"session_id : {self.session_id}",
            f"user       : {self.user or 'unknown'}",
            f"sprint     : {self.sprint_id}",
            f"memory_id  : {self.memory_id or 'none'}",
            f"turn       : {self.turn_count}",
        ]
        if self.active_topic:
            lines.append(f"topic      : {self.active_topic}")
        if self.ltm_summary:
            lines.append(f"ltm_hint   : {self.ltm_summary[:200]}…")
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Metrics
# ══════════════════════════════════════════════════════════════════════════════

class HarnessMetrics:
    """Lightweight in-process telemetry; flushed to S3 on session close."""

    def __init__(self):
        self._counts:   dict[str, int]   = defaultdict(int)
        self._latencies: dict[str, list] = defaultdict(list)
        self._errors:   dict[str, int]   = defaultdict(int)

    def record(self, tool: str, latency_ms: float, error: bool = False) -> None:
        self._counts[tool]    += 1
        self._latencies[tool].append(latency_ms)
        if error:
            self._errors[tool] += 1

    def summary(self) -> dict:
        out = {}
        for tool, count in self._counts.items():
            lats = self._latencies[tool]
            out[tool] = {
                "calls":      count,
                "errors":     self._errors.get(tool, 0),
                "avg_ms":     round(sum(lats) / len(lats), 1) if lats else 0,
                "max_ms":     round(max(lats), 1) if lats else 0,
            }
        return out


# ══════════════════════════════════════════════════════════════════════════════
# Session Store
# ══════════════════════════════════════════════════════════════════════════════

class SessionStore:
    """
    Manages ContextFrame objects in memory and persists them to S3.

    Key layout in S3:
        sessions/<session_id>/context.json   — full ContextFrame
        sessions/<session_id>/metrics.json   — tool metrics
    """

    STM_WINDOW = 20   # last N messages kept in ContextFrame.recent_messages

    def __init__(self):
        self._frames:  dict[str, ContextFrame] = {}
        self._metrics: dict[str, HarnessMetrics] = {}
        self._s3 = boto3.client("s3", region_name=AWS_REGION)

    # ── Frame lifecycle ────────────────────────────────────────────────────────

    def get_or_create(
        self,
        session_id: str,
        user: Optional[str] = None,
        sprint_id: str = SPRINT_ID,
    ) -> ContextFrame:
        if session_id not in self._frames:
            # Try restoring from S3
            frame = self._load_from_s3(session_id)
            if frame is None:
                frame = ContextFrame(
                    session_id=session_id,
                    user=user,
                    sprint_id=sprint_id,
                )
            self._frames[session_id] = frame
            self._metrics[session_id] = HarnessMetrics()
        else:
            frame = self._frames[session_id]
            if user and not frame.user:
                frame.user = user
        return frame

    def update_user(self, session_id: str, user: str) -> None:
        frame = self._frames.get(session_id)
        if frame:
            frame.user = user
            frame.touch()

    def add_message(self, session_id: str, role: str, content: str) -> None:
        frame = self.get_or_create(session_id)
        frame.recent_messages.append({
            "role": role,
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        # Keep sliding window
        if len(frame.recent_messages) > self.STM_WINDOW:
            frame.recent_messages = frame.recent_messages[-self.STM_WINDOW:]
        frame.touch()

    def inject_ltm(self, session_id: str, ltm_text: str) -> None:
        frame = self._frames.get(session_id)
        if frame:
            frame.ltm_summary = ltm_text
            frame.touch()

    def record_tool_call(self, session_id: str, tc: ToolCall) -> None:
        frame = self._frames.get(session_id)
        if frame:
            frame.tool_calls.append(tc)
            frame.touch()
        metrics = self._metrics.get(session_id)
        if metrics:
            metrics.record(tc.tool_name, tc.latency_ms, bool(tc.error))

    def get_frame(self, session_id: str) -> Optional[ContextFrame]:
        return self._frames.get(session_id)

    def close_session(self, session_id: str) -> None:
        """Flush the frame and metrics to S3 then evict from memory."""
        frame   = self._frames.get(session_id)
        metrics = self._metrics.get(session_id)
        if frame:
            self._save_to_s3(frame, metrics)
        self._frames.pop(session_id, None)
        self._metrics.pop(session_id, None)

    # ── S3 persistence ────────────────────────────────────────────────────────

    def _save_to_s3(
        self,
        frame: ContextFrame,
        metrics: Optional[HarnessMetrics] = None,
    ) -> None:
        try:
            ctx_key = f"sessions/{frame.session_id}/context.json"
            self._s3.put_object(
                Bucket=S3_BUCKET,
                Key=ctx_key,
                Body=json.dumps(frame.to_dict(), indent=2, default=str),
                ContentType="application/json",
            )
            if metrics:
                met_key = f"sessions/{frame.session_id}/metrics.json"
                self._s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=met_key,
                    Body=json.dumps(metrics.summary(), indent=2),
                    ContentType="application/json",
                )
            logger.info("Harness: flushed session %s to S3", frame.session_id)
        except ClientError as exc:
            logger.warning("Harness: S3 flush failed for %s: %s", frame.session_id, exc)

    def _load_from_s3(self, session_id: str) -> Optional[ContextFrame]:
        try:
            obj = self._s3.get_object(
                Bucket=S3_BUCKET,
                Key=f"sessions/{session_id}/context.json",
            )
            data = json.loads(obj["Body"].read())
            frame = ContextFrame(**{k: v for k, v in data.items()
                                    if k in ContextFrame.__dataclass_fields__})
            logger.info("Harness: restored session %s from S3", session_id)
            return frame
        except ClientError:
            return None


# ══════════════════════════════════════════════════════════════════════════════
# Middleware
# ══════════════════════════════════════════════════════════════════════════════

class HarnessMiddleware:
    """
    Pre/post hooks executed around every tool dispatch.

    Register hooks with:
        harness.middleware.before("store_update", my_fn)
        harness.middleware.after("*", my_fn)   # wildcard

    Hook signature:  fn(session_id, tool_name, tool_input, result?) -> None
    """

    def __init__(self):
        self._before: dict[str, list[Callable]] = defaultdict(list)
        self._after:  dict[str, list[Callable]] = defaultdict(list)

    def before(self, tool: str, fn: Callable) -> None:
        self._before[tool].append(fn)

    def after(self, tool: str, fn: Callable) -> None:
        self._after[tool].append(fn)

    def run_before(self, session_id: str, tool: str, inp: dict) -> None:
        for fn in self._before.get(tool, []) + self._before.get("*", []):
            try:
                fn(session_id, tool, inp)
            except Exception as exc:
                logger.warning("Before-hook error (%s): %s", tool, exc)

    def run_after(self, session_id: str, tool: str, inp: dict, result: Any) -> None:
        for fn in self._after.get(tool, []) + self._after.get("*", []):
            try:
                fn(session_id, tool, inp, result)
            except Exception as exc:
                logger.warning("After-hook error (%s): %s", tool, exc)


# ══════════════════════════════════════════════════════════════════════════════
# Main Harness
# ══════════════════════════════════════════════════════════════════════════════

class ContextHarness:
    """
    The single entry point for all agent interactions.

    Usage:
        harness = ContextHarness()
        frame   = harness.open_session("mahsa-session-001", user="Mahsa")
        result  = harness.dispatch("store_update", {...}, session_id="mahsa-session-001")
        harness.close_session("mahsa-session-001")
    """

    def __init__(self):
        self.sessions   = SessionStore()
        self.middleware = HarnessMiddleware()
        self._registry: dict[str, Callable] = {}

        # Register default logging middleware
        self.middleware.before("*", self._log_before)
        self.middleware.after("*",  self._log_after)

    # ── Tool registry ─────────────────────────────────────────────────────────

    def register(self, name: str, fn: Callable) -> None:
        """Register a callable as a named tool."""
        self._registry[name] = fn
        logger.debug("Harness: registered tool '%s'", name)

    # ── Session management ────────────────────────────────────────────────────

    def open_session(
        self,
        session_id: str,
        user: Optional[str] = None,
        sprint_id: str = SPRINT_ID,
    ) -> ContextFrame:
        return self.sessions.get_or_create(session_id, user=user, sprint_id=sprint_id)

    def close_session(self, session_id: str) -> None:
        self.sessions.close_session(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> None:
        self.sessions.add_message(session_id, role, content)

    def inject_ltm(self, session_id: str, ltm_text: str) -> None:
        self.sessions.inject_ltm(session_id, ltm_text)

    def get_frame(self, session_id: str) -> Optional[ContextFrame]:
        return self.sessions.get_frame(session_id)

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def dispatch(
        self,
        tool_name: str,
        tool_input: dict,
        session_id: str,
        tool_use_id: Optional[str] = None,
    ) -> Any:
        """
        Execute a tool through the full middleware pipeline.
        Records timing, errors, and ToolCall trace automatically.
        """
        if tool_name not in self._registry:
            return {"error": f"Tool '{tool_name}' not registered in harness"}

        self.middleware.run_before(session_id, tool_name, tool_input)

        tc = ToolCall(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=tool_use_id or f"tc-{uuid.uuid4().hex[:8]}",
        )
        t0 = time.perf_counter()
        try:
            result = self._registry[tool_name](**tool_input)
            tc.tool_output = result
        except Exception as exc:
            logger.exception("Harness: tool '%s' raised", tool_name)
            tc.error = str(exc)
            result   = {"error": str(exc)}
        finally:
            tc.latency_ms = round((time.perf_counter() - t0) * 1000, 2)

        self.sessions.record_tool_call(session_id, tc)
        self.middleware.run_after(session_id, tool_name, tool_input, result)
        return result

    # ── Bedrock Memory integration ────────────────────────────────────────────

    def save_to_bedrock_memory(
        self,
        session_id: str,
        messages: list[dict],
    ) -> bool:
        """
        Persist the conversation to Bedrock Memory Runtime (if MEMORY_ID set).
        Called at end-of-session or periodically.
        """
        if not MEMORY_ID:
            return False
        try:
            ba = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
            ba.invoke_memory(
                memoryId=MEMORY_ID,
                sessionId=session_id,
                memoryContents=[
                    {"conversationHistory": {"messages": messages}}
                ],
            )
            logger.info("Harness: saved session %s to Bedrock Memory %s", session_id, MEMORY_ID)
            return True
        except Exception as exc:
            logger.warning("Harness: Bedrock Memory save failed: %s", exc)
            return False

    def fetch_from_bedrock_memory(self, session_id: str, query: str) -> str:
        """Retrieve a memory summary from Bedrock Memory Runtime."""
        if not MEMORY_ID:
            return ""
        try:
            ba = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
            resp = ba.retrieve_and_generate(
                input={"text": query},
                retrieveAndGenerateConfiguration={
                    "type": "KNOWLEDGE_BASE",
                    "knowledgeBaseConfiguration": {
                        "knowledgeBaseId": MEMORY_ID,
                        "modelArn": f"arn:aws:bedrock:{AWS_REGION}::foundation-model/{MODEL_ID}",
                    },
                },
            )
            return resp.get("output", {}).get("text", "")
        except Exception as exc:
            logger.warning("Harness: Bedrock Memory fetch failed: %s", exc)
            return ""

    # ── Default middleware callbacks ───────────────────────────────────────────

    @staticmethod
    def _log_before(session_id: str, tool: str, inp: dict) -> None:
        logger.info("→ TOOL [%s] session=%s input_keys=%s",
                    tool, session_id, list(inp.keys()))

    @staticmethod
    def _log_after(session_id: str, tool: str, inp: dict, result: Any) -> None:
        status = "error" if isinstance(result, dict) and "error" in result else "ok"
        logger.info("← TOOL [%s] session=%s status=%s", tool, session_id, status)


# ── Module-level singleton ────────────────────────────────────────────────────
harness = ContextHarness()