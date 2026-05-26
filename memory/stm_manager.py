"""
memory/stm_manager.py — Short-Term Memory Manager

Maintains the active conversation context for the current session.
Data lives in-process (dict) and is intentionally ephemeral.
"""

from datetime import datetime, timezone
from collections import deque
from typing import Optional


class STMManager:
    """
    Manages Short-Term Memory for one or more concurrent sessions.

    Each session entry holds:
        current_topic   – what the conversation is currently about
        active_task     – the specific task being discussed
        sprint_context  – active sprint ID
        recent_messages – sliding window of the last N messages
        user            – identified user for this session
    """

    MAX_MESSAGES = 10

    def __init__(self):
        # session_id → context dict
        self._sessions: dict[str, dict] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_or_create(self, session_id: str, user: Optional[str] = None) -> dict:
        """Return the STM context for a session, creating it if absent."""
        if session_id not in self._sessions:
            self._sessions[session_id] = {
                "session_id":      session_id,
                "user":            user,
                "current_topic":   None,
                "active_task":     None,
                "sprint_context":  None,
                "recent_messages": deque(maxlen=self.MAX_MESSAGES),
                "created_at":      datetime.now(timezone.utc).isoformat(),
                "updated_at":      datetime.now(timezone.utc).isoformat(),
            }
        elif user and not self._sessions[session_id].get("user"):
            self._sessions[session_id]["user"] = user
        return self._sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Append a message to the session's recent-message window."""
        ctx = self.get_or_create(session_id)
        ctx["recent_messages"].append({
            "role":      role,
            "content":   content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        ctx["updated_at"] = datetime.now(timezone.utc).isoformat()

    def update_context(
        self,
        session_id:     str,
        current_topic:  Optional[str] = None,
        active_task:    Optional[str] = None,
        sprint_context: Optional[str] = None,
        user:           Optional[str] = None,
    ) -> None:
        """Patch one or more fields in the STM context."""
        ctx = self.get_or_create(session_id)
        if current_topic  is not None: ctx["current_topic"]  = current_topic
        if active_task    is not None: ctx["active_task"]    = active_task
        if sprint_context is not None: ctx["sprint_context"] = sprint_context
        if user           is not None: ctx["user"]           = user
        ctx["updated_at"] = datetime.now(timezone.utc).isoformat()

    def get_context(self, session_id: str) -> dict:
        """Return the full STM context dict (creates an empty one if missing)."""
        ctx = self.get_or_create(session_id)
        # Serialise the deque for callers that need JSON-safe output
        return {
            **ctx,
            "recent_messages": list(ctx["recent_messages"]),
        }

    def clear(self, session_id: str) -> None:
        """Remove a session from STM (e.g. after the conversation ends)."""
        self._sessions.pop(session_id, None)
