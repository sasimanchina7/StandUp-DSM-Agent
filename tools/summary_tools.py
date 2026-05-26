"""
tools/summary_tools.py — Generate structured standup summaries
"""

import logging
from datetime import datetime, timezone, timedelta

from config import SPRINT_GOALS, SPRINT_ID as DEFAULT_SPRINT
from tools.memory_tools import fetch_user_history
from tools.sprint_tools import get_sprint_context

logger = logging.getLogger(__name__)


def generate_standup_summary(user: str, sprint_id: str) -> dict:
    """
    Build a structured standup summary for a user.

    Returns:
        {
            "user": ...,
            "sprint_id": ...,
            "yesterday": [...],
            "today": [...],         # inferred from most recent session
            "blockers": [...],
            "sprint_goal": "...",
            "generated_at": "...",
        }
    """
    history_data = fetch_user_history(user)
    updates      = history_data.get("updates", [])
    sprint_ctx   = get_sprint_context(sprint_id)

    today_str     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    yesterday_updates: list[str] = []
    today_updates:     list[str] = []
    blockers:          list[str] = []

    for entry in updates:
        date   = entry.get("date", "")
        text   = entry.get("update", "")
        lower  = text.lower()

        if "blocker" in lower or "blocked" in lower:
            blockers.append(text)
        elif date == today_str:
            today_updates.append(text)
        elif date == yesterday_str:
            yesterday_updates.append(text)
        else:
            # Older — still surfaces under yesterday if nothing else
            if not yesterday_updates:
                yesterday_updates.append(text)

    summary = {
        "user":         user,
        "sprint_id":    sprint_id,
        "yesterday":    yesterday_updates or ["No updates recorded for yesterday."],
        "today":        today_updates     or ["Not yet specified for today."],
        "blockers":     blockers          or ["None reported."],
        "sprint_goal":  sprint_ctx.get("sprint_goal", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Human-readable formatted version (also returned for convenience)
    summary["formatted"] = _format(summary)
    return summary


def _format(s: dict) -> str:
    lines = [
        f"📋 Standup Summary — {s['user']} ({s['sprint_id']})",
        "",
        "✅ Yesterday:",
        *[f"  • {item}" for item in s["yesterday"]],
        "",
        "📌 Today:",
        *[f"  • {item}" for item in s["today"]],
        "",
        "🚧 Blockers:",
        *[f"  • {item}" for item in s["blockers"]],
        "",
        f"🎯 Sprint Goal:  {s['sprint_goal']}",
        "",
        f"Generated at: {s['generated_at']}",
    ]
    return "\n".join(lines)
