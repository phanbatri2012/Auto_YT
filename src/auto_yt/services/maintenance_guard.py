"""Restart safety checks for background jobs that can launch GPM profiles."""

from __future__ import annotations

import json

from auto_yt.services import database as db


def list_gpm_restart_blockers() -> list[dict]:
    now = db.utc_now()
    blockers = []
    for job in db.list_system_jobs(limit=500, job_type="comment_publish"):
        status = str(job.get("status") or "").strip().lower()
        if status not in {"queued", "running", "processing", "retry_wait"}:
            continue
        next_retry_at = str(job.get("next_retry_at") or "").strip()
        if status == "retry_wait" and next_retry_at and next_retry_at > now:
            continue
        channel_id = int((job.get("payload") or {}).get("channel_id") or 0)
        channel = db.get_youtube_channel(channel_id) if channel_id else None
        if not channel:
            continue
        if str(channel.get("interaction_mode") or "").strip() != "gpm_browser":
            continue
        if not str(channel.get("gpm_profile_id") or "").strip():
            continue
        blockers.append(
            {
                "id": str(job.get("id") or ""),
                "status": status,
                "title": str(job.get("title") or ""),
                "channel_title": str(channel.get("title") or ""),
                "gpm_profile_name": str(channel.get("gpm_profile_name") or ""),
            }
        )
    return blockers


def get_maintenance_status() -> dict:
    blockers = list_gpm_restart_blockers()
    return {
        "safe_to_restart": not blockers,
        "gpm_blocking_job_count": len(blockers),
        "gpm_blocking_jobs": blockers,
    }


if __name__ == "__main__":
    print(json.dumps(get_maintenance_status(), ensure_ascii=False))
