"""
tools/memory_tools.py — Store & Fetch standup updates
"""

import json
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from config import S3_BUCKET, SPRINT_ID, AWS_REGION
from memory.ltm_manager import LTMManager

logger = logging.getLogger(__name__)
_ltm = LTMManager()


def store_update(user: str, sprint_id: str, update: str) -> dict:
    """
    Persist a standup update to S3 via LTMManager.

    Returns a confirmation dict with the session record.
    """
    now        = datetime.now(timezone.utc)
    session_id = f"{user.lower()}-session-{now.strftime('%Y%m%d%H%M%S')}"

    session_data = {
        "session_id": session_id,
        "user":       user,
        "timestamp":  now.isoformat(),
        "sprint_id":  sprint_id,
        "sprint_day": _sprint_day(now),
        "date":       now.strftime("%Y-%m-%d"),
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "updates":    [update],
    }

    try:
        s3_key = _ltm.save_session(session_data)
        logger.info("Stored update for %s at %s", user, s3_key)
        return {
            "status":     "stored",
            "session_id": session_id,
            "user":       user,
            "sprint_id":  sprint_id,
            "timestamp":  now.isoformat(),
            "s3_key":     s3_key,
        }
    except Exception as exc:
        logger.exception("Failed to store update for %s", user)
        return {"status": "error", "error": str(exc)}


def fetch_user_history(user: str) -> dict:
    """
    Retrieve the last 7 days of standup updates for a user from S3.

    Returns a dict with a list of sessions and a flat updates list.
    """
    try:
        sessions = _ltm.get_user_history(user)
        all_updates: list[dict] = []
        for session in sessions:
            date = session.get("date", session.get("timestamp", "")[:10])
            for update_text in session.get("updates", []):
                all_updates.append({
                    "date":       date,
                    "sprint_id":  session.get("sprint_id", SPRINT_ID),
                    "update":     update_text,
                    "session_id": session.get("session_id", ""),
                })

        return {
            "user":           user,
            "total_sessions": len(sessions),
            "updates":        all_updates,
        }
    except Exception as exc:
        logger.exception("Failed to fetch history for %s", user)
        return {"user": user, "error": str(exc), "updates": []}


# ── Helper ────────────────────────────────────────────────────────────────────

def _sprint_day(dt: datetime) -> str:
    """Return a friendly sprint-day label (Day 1 … Day 14)."""
    # Sprint starts on Monday of the current 2-week window
    days_since_monday = dt.weekday()
    week_of_sprint    = (dt.isocalendar()[1] % 2)          # 0 or 1
    sprint_day        = week_of_sprint * 5 + days_since_monday + 1
    return f"Day {min(sprint_day, 14)}"