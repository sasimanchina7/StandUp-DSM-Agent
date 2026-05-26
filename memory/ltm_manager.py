"""
memory/ltm_manager.py — Long-Term Memory Manager

Reads and writes historical standup data to / from S3.
Also integrates with AWS Bedrock Memory when a MEMORY_ID is configured.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from config import S3_BUCKET, MEMORY_ID, AWS_REGION, LTM_DAYS

logger = logging.getLogger(__name__)


class LTMManager:
    """
    Long-Term Memory: persists standup updates across sessions via S3.

    S3 key layout:
        users/<user>/session-<YYYYMMDD>-<n>.json   — per-session file
        users/<user>/history.json                   — rolling 7-day index
        sprints/<sprint_id>-summary.json            — sprint-level summary
    """

    def __init__(self):
        self._s3 = boto3.client("s3", region_name=AWS_REGION)

    # ── Low-level S3 helpers ───────────────────────────────────────────────────

    def _put(self, key: str, data: dict) -> None:
        try:
            self._s3.put_object(
                Bucket=S3_BUCKET,
                Key=key,
                Body=json.dumps(data, indent=2, default=str),
                ContentType="application/json",
            )
            logger.info("S3 PUT s3://%s/%s", S3_BUCKET, key)
        except ClientError as exc:
            logger.error("S3 PUT failed for %s: %s", key, exc)
            raise

    def _get(self, key: str) -> Optional[dict]:
        try:
            obj = self._s3.get_object(Bucket=S3_BUCKET, Key=key)
            return json.loads(obj["Body"].read())
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            logger.error("S3 GET failed for %s: %s", key, exc)
            raise

    def _list_keys(self, prefix: str) -> list[str]:
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            keys = []
            for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
                keys.extend(obj["Key"] for obj in page.get("Contents", []))
            return keys
        except ClientError as exc:
            logger.error("S3 LIST failed for prefix %s: %s", prefix, exc)
            return []

    # ── Public API ─────────────────────────────────────────────────────────────

    def save_session(self, session_data: dict) -> str:
        """
        Persist a session record to S3 and update the user's rolling history.
        Returns the S3 key of the saved session.
        """
        user       = session_data["user"].lower()
        session_id = session_data["session_id"]
        date_str   = datetime.now(timezone.utc).strftime("%Y%m%d")
        key        = f"users/{user}/{session_id}.json"

        self._put(key, session_data)
        self._refresh_history_index(user)
        return key

    def _refresh_history_index(self, user: str) -> None:
        """Rebuild the rolling 7-day history index for a user."""
        cutoff  = datetime.now(timezone.utc) - timedelta(days=LTM_DAYS)
        prefix  = f"users/{user}/"
        keys    = [k for k in self._list_keys(prefix) if k.endswith(".json") and "history" not in k]

        recent_sessions = []
        for key in keys:
            record = self._get(key)
            if record:
                ts = record.get("timestamp", "")
                try:
                    if datetime.fromisoformat(ts.replace("Z", "+00:00")) >= cutoff:
                        recent_sessions.append(record)
                except ValueError:
                    recent_sessions.append(record)

        # Sort newest first
        recent_sessions.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
        history_key = f"users/{user}/history.json"
        self._put(history_key, {"user": user, "sessions": recent_sessions})

    def get_user_history(self, user: str) -> list[dict]:
        """Return the last LTM_DAYS sessions for a user."""
        history_key = f"users/{user.lower()}/history.json"
        data = self._get(history_key)
        if data:
            return data.get("sessions", [])

        # Fallback: scan individual files
        self._refresh_history_index(user.lower())
        data = self._get(history_key)
        return data.get("sessions", []) if data else []

    def save_sprint_summary(self, sprint_id: str, summary: dict) -> None:
        """Persist or update a sprint-level summary."""
        key = f"sprints/{sprint_id.lower()}-summary.json"
        existing = self._get(key) or {}
        existing.update(summary)
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._put(key, existing)

    def get_sprint_summary(self, sprint_id: str) -> Optional[dict]:
        """Retrieve a sprint summary."""
        return self._get(f"sprints/{sprint_id.lower()}-summary.json")