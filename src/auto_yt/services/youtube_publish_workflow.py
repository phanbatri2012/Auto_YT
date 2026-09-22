"""Durable, checkpointed YouTube publishing workflow."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from auto_yt.services import database as db
from auto_yt.services import secret_store, youtube_comments, youtube_publisher
from auto_yt.services.proxy_utils import parse_proxy_url


CAPTION_NAME = "Tiếng Việt"
ACTIVE_WORKFLOW_STATUSES = {
    "reserved",
    "running",
    "paused",
    "retry_wait",
    "reconcile_required",
}


class PublishConfigurationRequired(RuntimeError):
    def __init__(self, missing_configuration: list[str], message: str):
        super().__init__(message)
        self.missing_configuration = list(dict.fromkeys(missing_configuration))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _thumbnail_section(script: str, variant: str) -> str:
    label = "THUMBNAIL CÓ CHỮ" if variant == "with_text" else "THUMBNAIL KHÔNG CHỮ"
    match = re.search(
        rf"###\s*\[{re.escape(label)}\]\s*(.*?)(?=\n###\s*\[|\Z)",
        script or "",
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _resolve_thumbnail_path(script: str, variant: str, thumbnails_dir: Path) -> Path | None:
    section = _thumbnail_section(script, variant)
    candidates = [
        value.strip()
        for value in re.findall(r"\[IMAGE_URL:([^\]]+)\]", section)
    ]
    candidates.extend(
        match.group(0)
        for match in re.finditer(
            r"(?:https?://(?:127\.0\.0\.1|localhost)(?::\d+)?)?"
            r"/api/thumbnails/[^\s\]\)\"']+",
            section,
            flags=re.IGNORECASE,
        )
    )
    resolved_root = thumbnails_dir.resolve()
    for candidate in candidates:
        parsed = urlsplit(candidate)
        path_text = unquote(parsed.path or candidate)
        marker = "/api/thumbnails/"
        if marker not in path_text:
            continue
        filename = path_text.split(marker, 1)[1]
        if not filename or Path(filename).name != filename:
            continue
        path = (resolved_root / filename).resolve()
        try:
            path.relative_to(resolved_root)
        except ValueError:
            continue
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _artifact_snapshot(artifact: dict) -> dict:
    return {
        "id": int(artifact["id"]),
        "path": str(artifact.get("path") or ""),
        "content_hash": str(artifact.get("content_hash") or ""),
        "size_bytes": int(artifact.get("size_bytes") or 0),
    }


def _validate_artifact(
    artifact: dict | None,
    *,
    video_id: int,
    artifact_type: str,
    missing_key: str,
) -> tuple[dict, Path]:
    path = Path(str((artifact or {}).get("path") or ""))
    if (
        not artifact
        or int(artifact.get("video_id") or 0) != int(video_id)
        or artifact.get("artifact_type") != artifact_type
        or artifact.get("status") != "ready"
        or not str(artifact.get("content_hash") or "")
        or not path.is_file()
        or path.stat().st_size <= 0
    ):
        raise PublishConfigurationRequired(
            [missing_key],
            f"Artifact {artifact_type} chưa sẵn sàng hoặc không còn đúng bản đã lưu.",
        )
    return artifact, path


def _persist_refreshed_token(
    channel_id: int,
    encrypted_access_token: str,
    token_expiry: str,
    oauth_client_id: str,
) -> None:
    db.update_youtube_channel(
        channel_id,
        access_token_encrypted=encrypted_access_token,
        token_expiry=token_expiry,
        oauth_client_id=oauth_client_id,
    )


def _resolve_channel(
    *,
    workflow: dict | None,
    snapshot: dict,
    prompt_version: str,
    resolve_default_channel_id,
) -> dict:
    if workflow:
        channel = db.get_youtube_channel(
            int(workflow["youtube_channel_id"]), include_tokens=True
        )
        if channel:
            return channel
        raise PublishConfigurationRequired(
            ["default_youtube_channel_id"],
            "Kênh YouTube của workflow không còn tồn tại.",
        )

    stable_channel_id = str(snapshot.get("default_youtube_channel_id") or "").strip()
    current_channel_id = str(resolve_default_channel_id(prompt_version) or "").strip()
    channel_id = stable_channel_id or current_channel_id
    if not channel_id:
        raise PublishConfigurationRequired(
            ["default_youtube_channel_id"],
            "Bộ prompt chưa chọn kênh YouTube mặc định.",
        )
    channel = db.get_youtube_channel_by_channel_id(channel_id, include_tokens=True)
    if not channel:
        raise PublishConfigurationRequired(
            ["default_youtube_channel_id"],
            "Kênh YouTube mặc định không tồn tại hoặc đã bị ngắt kết nối.",
        )
    return channel


def _validate_dynamic_channel(channel: dict, *, schedule_enabled: bool) -> str:
    missing = []
    if channel.get("status") != "connected":
        missing.append("youtube_oauth")
    if not (
        channel.get("access_token_encrypted")
        or channel.get("refresh_token_encrypted")
    ):
        missing.append("youtube_oauth")
    if not str(channel.get("gpm_profile_id") or "").strip():
        missing.append("gpm_profile_id")
    raw_proxy = str(channel.get("gpm_proxy_info") or "").strip()
    if not parse_proxy_url(raw_proxy):
        missing.append("gpm_proxy_info")
    if schedule_enabled:
        if bool(channel.get("publication_paused")):
            missing.append("publication_paused")
        if not str(channel.get("publication_timezone") or "").strip():
            missing.append("publication_timezone")
        if not channel.get("publication_slots"):
            missing.append("publication_slots")
        if not bool(channel.get("public_upload_verified")):
            missing.append("public_upload_verified")
    if missing:
        raise PublishConfigurationRequired(
            missing,
            "Cấu hình kênh YouTube chưa đủ để tiếp tục tự động đăng.",
        )
    return raw_proxy


def _build_preflight_context(
    job: dict,
    *,
    resolve_default_channel_id,
    thumbnails_dir: Path,
) -> dict:
    payload = job.get("payload") or {}
    workflow = None
    workflow_id = str(payload.get("workflow_id") or "").strip()
    if workflow_id:
        workflow = db.get_youtube_publish_workflow(workflow_id)
    if workflow is None:
        workflow = db.get_youtube_publish_workflow_by_job(job["id"])

    snapshot = dict((workflow or {}).get("snapshot") or payload.get("snapshot") or {})
    prompt_version = str(
        snapshot.get("prompt_version")
        or job.get("prompt_version")
        or payload.get("prompt_version")
        or ""
    )
    channel = _resolve_channel(
        workflow=workflow,
        snapshot=snapshot,
        prompt_version=prompt_version,
        resolve_default_channel_id=resolve_default_channel_id,
    )
    pipeline = snapshot.get("pipeline") if isinstance(snapshot.get("pipeline"), dict) else {}
    schedule_enabled = bool(pipeline.get("youtube_schedule"))
    proxy = _validate_dynamic_channel(channel, schedule_enabled=schedule_enabled)

    video_id = int(
        (workflow or {}).get("video_id")
        or job.get("video_id")
        or payload.get("video_id")
        or 0
    )
    video = db.get_video(video_id)
    if not video:
        raise PublishConfigurationRequired(["video"], "Không tìm thấy video nội bộ.")

    artifact_id = int(
        (workflow or {}).get("artifact_id") or payload.get("artifact_id") or 0
    )
    final_artifact, video_path = _validate_artifact(
        db.get_video_artifact(artifact_id),
        video_id=video_id,
        artifact_type="final_mp4",
        missing_key="final_mp4",
    )
    caption_artifact, caption_path = _validate_artifact(
        db.get_latest_video_artifact(video_id, "captions", status="ready"),
        video_id=video_id,
        artifact_type="captions",
        missing_key="captions_srt",
    )

    publishing_settings = snapshot.get("publishing_settings")
    if not isinstance(publishing_settings, dict):
        publishing_settings = {}
    if not isinstance(publishing_settings.get("made_for_kids"), bool):
        raise PublishConfigurationRequired(
            ["made_for_kids"],
            "Bộ prompt chưa chọn video có dành cho trẻ em hay không.",
        )
    metadata = snapshot.get("youtube_metadata")
    if not isinstance(metadata, dict) or not metadata.get("snippet"):
        try:
            metadata = youtube_publisher.build_upload_metadata(
                video, publishing_settings
            )
        except youtube_publisher.YouTubePublishError as exc:
            raise PublishConfigurationRequired(["metadata"], str(exc)) from exc
        snapshot["youtube_metadata"] = metadata

    image_settings = snapshot.get("image_generation_settings")
    if not isinstance(image_settings, dict):
        image_settings = {}
    thumbnail_variant = str(
        image_settings.get("thumbnail_variant") or "without_text"
    )
    thumbnail_path = _resolve_thumbnail_path(
        str(video.get("generated_script") or ""),
        thumbnail_variant,
        thumbnails_dir,
    )
    if thumbnail_path is None:
        raise PublishConfigurationRequired(
            ["thumbnail"],
            "Không tìm thấy ảnh local hợp lệ trong đúng mục thumbnail đã chọn.",
        )

    artifact_snapshot = snapshot.get("artifacts")
    if isinstance(artifact_snapshot, dict) and artifact_snapshot:
        expected_video = artifact_snapshot.get("final_mp4") or {}
        expected_caption = artifact_snapshot.get("captions") or {}
        expected_thumbnail = artifact_snapshot.get("thumbnail") or {}
        changed = []
        if expected_video != _artifact_snapshot(final_artifact):
            changed.append("final_mp4")
        if expected_caption != _artifact_snapshot(caption_artifact):
            changed.append("captions_srt")
        if (
            str(expected_thumbnail.get("path") or "") != str(thumbnail_path)
            or str(expected_thumbnail.get("sha256") or "")
            != _sha256_file(thumbnail_path)
        ):
            changed.append("thumbnail")
        if changed:
            raise PublishConfigurationRequired(
                changed,
                "Artifact publish đã thay đổi so với snapshot; cần khôi phục đúng bản đã reserve.",
            )
    else:
        snapshot["artifacts"] = {
            "final_mp4": _artifact_snapshot(final_artifact),
            "captions": _artifact_snapshot(caption_artifact),
            "thumbnail": {
                "path": str(thumbnail_path),
                "sha256": _sha256_file(thumbnail_path),
            },
        }
    snapshot["prompt_version"] = prompt_version
    snapshot["default_youtube_channel_id"] = str(channel.get("channel_id") or "")

    if workflow is None:
        workflow, _ = db.reserve_youtube_publish_workflow(
            video_id=video_id,
            youtube_channel_id=int(channel["id"]),
            artifact_id=int(final_artifact["id"]),
            snapshot=snapshot,
            system_job_id=job["id"],
        )
    else:
        db.update_youtube_publish_workflow(
            workflow["id"],
            system_job_id=job["id"],
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        )
        workflow = db.get_youtube_publish_workflow(workflow["id"])

    return {
        "workflow": workflow,
        "snapshot": snapshot,
        "video": video,
        "video_path": video_path,
        "caption_path": caption_path,
        "thumbnail_path": thumbnail_path,
        "publishing_settings": publishing_settings,
        "metadata": metadata,
        "channel": channel,
        "proxy": proxy,
        "schedule_enabled": schedule_enabled,
    }


def execute_publish_job(
    job: dict,
    *,
    progress,
    cancel_check,
    resolve_default_channel_id,
    thumbnails_dir: Path,
) -> dict:
    context = _build_preflight_context(
        job,
        resolve_default_channel_id=resolve_default_channel_id,
        thumbnails_dir=thumbnails_dir,
    )
    workflow = context["workflow"]
    workflow_id = str(workflow["id"])
    video_id = int(workflow["video_id"])
    if workflow.get("status") == "completed":
        return {
            "workflow_id": workflow_id,
            "youtube_video_id": workflow.get("youtube_video_id") or "",
            "scheduled_at": workflow.get("scheduled_at") or "",
            "stage": "completed",
        }

    db.update_youtube_publish_workflow(
        workflow_id, status="running", error=""
    )
    db.update_video_production_state(
        video_id,
        publish_status="running",
        current_stage="preflight",
        production_progress="Đã kiểm tra cấu hình đăng YouTube",
        blocking_reason="",
    )
    progress(
        "Đã kiểm tra cấu hình đăng YouTube",
        str(workflow.get("stage") or "preflight"),
        None,
    )
    cancel_check()

    if context["schedule_enabled"] and not str(workflow.get("scheduled_at") or ""):
        scheduled_at = db.reserve_youtube_publication_slot(workflow_id)
        workflow = db.get_youtube_publish_workflow(workflow_id)
        progress("Đã giữ khung giờ đăng YouTube", "slot_reserved", 0)
    else:
        scheduled_at = str(workflow.get("scheduled_at") or "")

    def token_provider() -> str:
        latest_channel = db.get_youtube_channel(
            int(workflow["youtube_channel_id"]), include_tokens=True
        )
        if latest_channel is None:
            raise PublishConfigurationRequired(
                ["default_youtube_channel_id"], "Kênh YouTube không còn tồn tại."
            )
        latest_proxy = _validate_dynamic_channel(
            latest_channel,
            schedule_enabled=context["schedule_enabled"],
        )
        if parse_proxy_url(latest_proxy) != parse_proxy_url(context["proxy"]):
            raise PublishConfigurationRequired(
                ["gpm_proxy_info"],
                "Proxy kênh đã thay đổi; hãy resume để chạy preflight lại.",
            )
        return youtube_comments.access_token_for_channel(
            latest_channel,
            _persist_refreshed_token,
            proxy=context["proxy"],
        )

    workflow = db.get_youtube_publish_workflow(workflow_id)
    youtube_video_id = str(workflow.get("youtube_video_id") or "")
    if not youtube_video_id:
        session_url = secret_store.decrypt_secret(
            str(workflow.get("upload_session_encrypted") or "")
        )
        resume_session = bool(session_url)
        if not session_url:
            cancel_check()
            session_url = youtube_publisher.start_resumable_upload(
                token=token_provider(),
                video_path=context["video_path"],
                metadata=context["metadata"],
                notify_subscribers=bool(
                    context["publishing_settings"].get("notify_subscribers", True)
                ),
                proxy=context["proxy"],
            )
            db.update_youtube_publish_workflow(
                workflow_id,
                upload_session_encrypted=secret_store.encrypt_secret(session_url),
                stage="session_created",
                status="running",
            )
            progress("Đã tạo phiên upload riêng tư", "session_created", 0)

        total_size = max(1, context["video_path"].stat().st_size)

        def persist_upload_offset(offset: int) -> None:
            percent = min(100, max(0, int((int(offset) / total_size) * 100)))
            db.update_youtube_publish_workflow(
                workflow_id,
                upload_offset=int(offset),
                stage="uploading",
                status="running",
            )
            progress(f"Đang upload YouTube: {percent}%", "uploading", percent)

        def persist_remote_video(payload: dict) -> None:
            remote_id = str(payload.get("id") or "").strip()
            if not remote_id:
                return
            publication = db.save_video_publication(
                video_id=video_id,
                youtube_channel_id=int(workflow["youtube_channel_id"]),
                youtube_video_id=remote_id,
                published_url=f"https://www.youtube.com/watch?v={remote_id}",
                published_title=str(
                    context["metadata"].get("snippet", {}).get("title") or ""
                ),
                published_at=scheduled_at,
            )
            db.update_youtube_publish_workflow(
                workflow_id,
                youtube_video_id=remote_id,
                publication_id=int(publication["id"]),
                upload_offset=total_size,
                stage="uploaded",
                status="running",
            )

        uploaded = youtube_publisher.upload_video_resumable(
            session_url=session_url,
            video_path=context["video_path"],
            token_provider=token_provider,
            start_offset=int(workflow.get("upload_offset") or 0),
            persist_progress=persist_upload_offset,
            persist_remote_video=persist_remote_video,
            cancel_check=cancel_check,
            resume_session=resume_session,
            proxy=context["proxy"],
        )
        youtube_video_id = str(uploaded.get("id") or "").strip()
        if not youtube_video_id:
            raise youtube_publisher.YouTubeUploadReconciliationRequired(
                "Upload đã kết thúc nhưng chưa xác định được YouTube Video ID."
            )
        persist_remote_video(uploaded)
        progress("Đã upload video ở chế độ Private", "uploaded", 100)

    cancel_check()
    workflow = db.get_youtube_publish_workflow(workflow_id)
    completed_stages = {
        "thumbnail_done",
        "caption_done",
        "processing",
        "scheduled",
        "completed",
    }
    if str(workflow.get("stage") or "") not in completed_stages:
        youtube_publisher.upload_thumbnail(
            token_provider(),
            youtube_video_id,
            context["thumbnail_path"],
            proxy=context["proxy"],
        )
        db.update_youtube_publish_workflow(workflow_id, stage="thumbnail_done")
        progress("Đã gắn thumbnail YouTube", "thumbnail_done", 100)

    cancel_check()
    workflow = db.get_youtube_publish_workflow(workflow_id)
    if str(workflow.get("stage") or "") not in {
        "caption_done",
        "processing",
        "scheduled",
        "completed",
    }:
        language = str(context["publishing_settings"].get("language") or "vi")
        caption_id = youtube_publisher.find_caption_track(
            token_provider(),
            youtube_video_id,
            language,
            CAPTION_NAME,
            proxy=context["proxy"],
        )
        if not caption_id:
            caption_result = youtube_publisher.upload_caption(
                token_provider(),
                youtube_video_id,
                context["caption_path"],
                language=language,
                name=CAPTION_NAME,
                proxy=context["proxy"],
            )
            caption_id = str(caption_result.get("id") or "")
        db.update_youtube_publish_workflow(
            workflow_id,
            caption_id=caption_id,
            stage="caption_done",
        )
        progress("Đã gắn phụ đề tiếng Việt", "caption_done", 100)

    if context["schedule_enabled"]:
        cancel_check()
        db.update_youtube_publish_workflow(workflow_id, stage="processing")
        progress("Đang chờ YouTube xử lý video", "processing", 100)
        youtube_publisher.require_processing_succeeded(
            token_provider(), youtube_video_id, proxy=context["proxy"]
        )
        youtube_publisher.schedule_video(
            token_provider(),
            youtube_video_id,
            scheduled_at,
            proxy=context["proxy"],
        )
        db.update_youtube_publish_workflow(workflow_id, stage="scheduled")
        progress("Đã đặt lịch đăng YouTube", "scheduled", 100)

    db.update_youtube_publish_workflow(
        workflow_id,
        status="completed",
        stage="completed",
        upload_session_encrypted="",
        error="",
    )
    final_status = "scheduled" if context["schedule_enabled"] else "uploaded_private"
    db.update_video_production_state(
        video_id,
        publish_status=final_status,
        current_stage="completed",
        production_progress=(
            "Đã đặt lịch đăng YouTube"
            if context["schedule_enabled"]
            else "Đã upload Private cùng thumbnail và phụ đề"
        ),
        blocking_reason="",
    )
    return {
        "workflow_id": workflow_id,
        "youtube_video_id": youtube_video_id,
        "scheduled_at": scheduled_at,
        "stage": "completed",
        "upload_percent": 100,
    }
