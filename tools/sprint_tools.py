"""
tools/sprint_tools.py — Sprint context retrieval
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from config import SPRINT_GOALS, S3_BUCKET
from memory.ltm_manager import LTMManager

logger = logging.getLogger(__name__)
_ltm = LTMManager()


def get_sprint_context(sprint_id: str) -> dict:
    """
    Return the current sprint's goal, team assignments, and any
    summaries already stored in S3.
    """
    sprint_id = sprint_id.upper()

    # Pull stored summary (may be None for a brand-new sprint)
    stored = _ltm.get_sprint_summary(sprint_id) or {}

    goal = SPRINT_GOALS.get(sprint_id, "Sprint goal not configured — please update config.py.")

    # Build the context package returned to the agent
    context = {
        "sprint_id":    sprint_id,
        "sprint_goal":  goal,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "team_tasks":   stored.get("team_tasks", {}),
        "blockers":     stored.get("blockers", []),
        "notes":        stored.get("notes", ""),
    }
    return context


def update_sprint_summary(sprint_id: str, user: str, task: str, blocker: Optional[str] = None) -> dict:
    """
    Add or update a task / blocker for a user inside the sprint summary.
    Called internally by the summary tool.
    """
    sprint_id = sprint_id.upper()
    summary   = _ltm.get_sprint_summary(sprint_id) or {"team_tasks": {}, "blockers": []}

    # Update team tasks
    user_tasks = summary["team_tasks"].get(user, [])
    if task not in user_tasks:
        user_tasks.append(task)
    summary["team_tasks"][user] = user_tasks

    # Update blockers
    if blocker and blocker not in summary["blockers"]:
        summary["blockers"].append({"user": user, "blocker": blocker})

    _ltm.save_sprint_summary(sprint_id, summary)
    return {"status": "updated", "sprint_id": sprint_id, "user": user}