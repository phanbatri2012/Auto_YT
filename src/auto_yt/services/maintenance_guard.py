"""Comprehensive restart and maintenance safety checks for Auto_YT background jobs, renderers, uploads, and GPM profiles."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from auto_yt.services import database as db


def _get_db_connection() -> sqlite3.Connection | None:
    try:
        conn = sqlite3.connect(str(db.DB_PATH), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def list_system_job_blockers() -> list[dict[str, Any]]:
    """Check for any active system_jobs (video_render, youtube_publish, comment_publish, etc.)."""
    conn = _get_db_connection()
    if not conn:
        return []
    try:
        rows = conn.execute(
            """
            SELECT system_jobs.id, system_jobs.job_type, system_jobs.status,
                   system_jobs.video_id, system_jobs.progress, system_jobs.started_at,
                   videos.title AS video_title
            FROM system_jobs
            LEFT JOIN videos ON videos.id = system_jobs.video_id
            WHERE system_jobs.status IN ('running', 'processing', 'in_progress', 'queued')
            ORDER BY system_jobs.created_at ASC
            """
        ).fetchall()
        blockers = []
        for r in rows:
            blockers.append(
                {
                    "type": "system_job",
                    "id": str(r["id"]),
                    "job_type": str(r["job_type"]),
                    "status": str(r["status"]),
                    "video_id": r["video_id"],
                    "video_title": str(r["video_title"] or ""),
                    "progress": str(r["progress"] or ""),
                    "started_at": str(r["started_at"] or ""),
                }
            )
        return blockers
    except Exception:
        return []
    finally:
        conn.close()


def list_youtube_publish_blockers() -> list[dict[str, Any]]:
    """Check for active YouTube video upload workflows or publication jobs."""
    conn = _get_db_connection()
    if not conn:
        return []
    blockers = []
    try:
        # 1. Check video_publications in active processing states
        rows_p = conn.execute(
            """
            SELECT vp.id, vp.video_id, vp.published_title, vp.processing_status, vp.updated_at,
                   yc.title AS channel_title
            FROM video_publications vp
            LEFT JOIN youtube_channels yc ON yc.id = vp.youtube_channel_id
            WHERE vp.processing_status IN ('processing', 'uploading', 'session_created')
              AND (
                  EXISTS (
                      SELECT 1 FROM system_jobs sj
                      WHERE sj.video_id = vp.video_id
                        AND sj.job_type = 'youtube_publish'
                        AND sj.status IN ('running', 'processing', 'in_progress')
                  )
                  OR datetime(vp.updated_at) >= datetime('now', '-15 minutes')
              )
            """
        ).fetchall()
        for r in rows_p:
            blockers.append(
                {
                    "type": "youtube_upload",
                    "id": str(r["id"]),
                    "video_id": r["video_id"],
                    "title": str(r["published_title"] or f"Video #{r['video_id']}"),
                    "status": str(r["processing_status"]),
                    "channel_title": str(r["channel_title"] or ""),
                    "updated_at": str(r["updated_at"] or ""),
                }
            )

        # 2. Check youtube_publish_workflows in active states
        rows_w = conn.execute(
            """
            SELECT ypw.id, ypw.video_id, ypw.status, ypw.updated_at
            FROM youtube_publish_workflows ypw
            WHERE ypw.status IN ('running', 'processing', 'uploading', 'in_progress')
              AND datetime(ypw.updated_at) >= datetime('now', '-15 minutes')
            """
        ).fetchall()
        for r in rows_w:
            if not any(b.get("video_id") == r["video_id"] for b in blockers):
                blockers.append(
                    {
                        "type": "youtube_workflow",
                        "id": str(r["id"]),
                        "video_id": r["video_id"],
                        "title": f"Publish Workflow #{r['video_id']}",
                        "status": str(r["status"]),
                        "updated_at": str(r["updated_at"] or ""),
                    }
                )
    except Exception:
        pass
    finally:
        conn.close()
    return blockers


def list_video_artifact_blockers() -> list[dict[str, Any]]:
    """Check for active scene generation or video render artifacts in progress.
    Only considers artifacts active if their parent job is actively running or updated within the last 5 minutes."""
    conn = _get_db_connection()
    if not conn:
        return []
    try:
        rows = conn.execute(
            """
            SELECT va.id, va.video_id, va.artifact_type, va.status, va.created_at, va.updated_at,
                   videos.title AS video_title
            FROM video_artifacts va
            LEFT JOIN videos ON videos.id = va.video_id
            WHERE va.status IN ('processing', 'running', 'generating')
              AND (
                  EXISTS (
                      SELECT 1 FROM system_jobs sj
                      WHERE sj.video_id = va.video_id
                        AND sj.status IN ('running', 'processing', 'in_progress')
                  )
                  OR datetime(va.updated_at) >= datetime('now', '-5 minutes')
              )
            ORDER BY va.id DESC
            LIMIT 50
            """
        ).fetchall()
        blockers = []
        for r in rows:
            blockers.append(
                {
                    "type": "video_artifact",
                    "id": str(r["id"]),
                    "video_id": r["video_id"],
                    "artifact_type": str(r["artifact_type"]),
                    "status": str(r["status"]),
                    "video_title": str(r["video_title"] or ""),
                    "created_at": str(r["created_at"] or ""),
                    "updated_at": str(r["updated_at"] or ""),
                }
            )
        return blockers
    except Exception:
        return []
    finally:
        conn.close()


def list_fb_crossposter_blockers() -> list[dict[str, Any]]:
    """Check for active Facebook cross-post uploads."""
    conn = _get_db_connection()
    if not conn:
        return []
    try:
        rows = conn.execute(
            """
            SELECT id, video_id, status, target_page_id, updated_at
            FROM fb_crossposter_queue
            WHERE status IN ('processing', 'uploading', 'running')
            """
        ).fetchall()
        blockers = []
        for r in rows:
            blockers.append(
                {
                    "type": "fb_crosspost",
                    "id": str(r["id"]),
                    "video_id": r["video_id"],
                    "status": str(r["status"]),
                    "target_page_id": str(r["target_page_id"] or ""),
                    "updated_at": str(r["updated_at"] or ""),
                }
            )
        return blockers
    except Exception:
        return []
    finally:
        conn.close()


def list_gpm_restart_blockers() -> list[dict[str, Any]]:
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
                "type": "gpm_browser",
                "id": str(job.get("id") or ""),
                "status": status,
                "title": str(job.get("title") or ""),
                "channel_title": str(channel.get("title") or ""),
                "gpm_profile_name": str(channel.get("gpm_profile_name") or ""),
            }
        )
    return blockers


def get_maintenance_status() -> dict[str, Any]:
    system_job_blockers = list_system_job_blockers()
    youtube_blockers = list_youtube_publish_blockers()
    artifact_blockers = list_video_artifact_blockers()
    fb_blockers = list_fb_crossposter_blockers()
    gpm_blockers = list_gpm_restart_blockers()

    # De-duplicate blockers by (type, id)
    seen_keys = set()
    all_blockers = []
    for b in system_job_blockers + youtube_blockers + artifact_blockers + fb_blockers + gpm_blockers:
        key = (b.get("type"), b.get("id"))
        if key not in seen_keys:
            seen_keys.add(key)
            all_blockers.append(b)

    reasons = []
    if system_job_blockers:
        reasons.append(f"{len(system_job_blockers)} system job(s) in progress (render/generation/publish)")
    if youtube_blockers:
        reasons.append(f"{len(youtube_blockers)} YouTube video upload(s) in progress")
    if artifact_blockers:
        reasons.append(f"{len(artifact_blockers)} scene artifact(s) generating")
    if fb_blockers:
        reasons.append(f"{len(fb_blockers)} Facebook crosspost(s) uploading")
    if gpm_blockers:
        reasons.append(f"{len(gpm_blockers)} GPM browser task(s) active")

    return {
        "safe_to_restart": len(all_blockers) == 0,
        "total_blocking_jobs": len(all_blockers),
        "blocking_jobs": all_blockers,
        "summary": {
            "system_jobs": len(system_job_blockers),
            "youtube_uploads": len(youtube_blockers),
            "video_artifacts": len(artifact_blockers),
            "fb_crossposts": len(fb_blockers),
            "gpm_browsers": len(gpm_blockers),
        },
        "reasons": reasons,
    }


if __name__ == "__main__":
    print(json.dumps(get_maintenance_status(), ensure_ascii=False, indent=2))

