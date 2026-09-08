from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import List, Literal, Optional
import asyncio
import json
import math
import threading
import time
import uuid
from auto_yt.paths import ACCOUNT_PATH, AUDIO_DIR, DATA_DIR, CHROME_USER_DATA_DIR, gpt_profile_dir, PROMPTS_PATH, THUMBNAILS_DIR
from auto_yt.default_prompts import DEFAULT_PROMPTS_DATA
import shutil
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding='utf-8')

# Add src to sys.path so we can import auto_yt paths correctly if run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

from auto_yt.services.youtube_service import get_video_transcript, get_video_title
from auto_yt.services.chatgpt_service import process_prompt_via_chatgpt
from auto_yt.services.chatgpt_worker import sanitize_generated_script
import auto_yt.services.database as db
import auto_yt.services.audio_utils as audio_utils
import auto_yt.services.tts_service as tts
from auto_yt.services.audio_review import (
    audit_script_for_audio,
    get_audio_script,
    get_audio_script_hash,
)
from auto_yt.services import voice_config
from auto_yt.services import chatgpt_projects
from auto_yt.services.generation_checkpoint import (
    clear_checkpoint,
    load_checkpoint,
)
from auto_yt.services.youtube_downloader import (
    YouTubeDownloaderError,
    download_jobs,
    is_youtube_channel_url,
    list_youtube_videos,
    select_download_directory,
)
import re

# In-memory job store: job_id -> {status, progress, result, error}
_jobs: dict = {}
_jobs_lock = threading.Lock()
_video_queue_state_lock = threading.Lock()
_video_queue_worker_active = False
_video_queue_wakeup_timer: threading.Timer | None = None
_video_queue_wakeup_at = 0.0
_prompts_config_lock = threading.Lock()
_chatgpt_profile_lock = threading.Lock()
_chatgpt_state_lock = threading.Lock()
_chatgpt_operation = ""
_chatgpt_prompt_version = ""
CHATGPT_BUSY_ERROR = (
    "ChatGPT đang bận với tác vụ khác. Hãy đợi tác vụ hiện tại hoàn tất."
)
INITIAL_GENERATED_SCRIPT = (
    "### [INTRO]\n\n"
    "### [BODY]\n\n"
    "### [OUTRO]\n\n"
    "### [METADATA & QUIZ]\n\n"
    "### [CHAPTERS]\n\n"
    "### [THUMBNAIL CÓ CHỮ]\n\n"
    "### [THUMBNAIL KHÔNG CHỮ]\n"
)
MAX_THUMBNAIL_IMAGES_PER_RESPONSE = 2
VIDEO_RECOVERY_DELAYS_SECONDS = (30, 120, 300)


def _normalize_thumbnail_urls(value, fallback_url: str = "") -> list[str]:
    raw_urls = value if isinstance(value, list) else []
    if not raw_urls and fallback_url:
        raw_urls = [fallback_url]
    return list(dict.fromkeys(url for url in raw_urls if url))[
        :MAX_THUMBNAIL_IMAGES_PER_RESPONSE
    ]


def _try_start_chatgpt_operation(
    operation: str,
    prompt_version: str = "",
) -> bool:
    global _chatgpt_operation, _chatgpt_prompt_version
    if not _chatgpt_profile_lock.acquire(blocking=False):
        return False
    with _chatgpt_state_lock:
        _chatgpt_operation = operation
        _chatgpt_prompt_version = str(prompt_version or "").strip()
    return True


def _finish_chatgpt_operation() -> None:
    global _chatgpt_operation, _chatgpt_prompt_version
    with _chatgpt_state_lock:
        _chatgpt_operation = ""
        _chatgpt_prompt_version = ""
    _chatgpt_profile_lock.release()
    queue_kicker = globals().get("_kick_video_queue")
    if queue_kicker:
        queue_kicker()


def _get_chatgpt_operation() -> str:
    with _chatgpt_state_lock:
        return _chatgpt_operation


def _get_chatgpt_state() -> tuple[str, str]:
    with _chatgpt_state_lock:
        return _chatgpt_operation, _chatgpt_prompt_version

class AccountData(BaseModel):
    email: str = ""
    password: str = ""
    totp_secret: str = ""
    headless: bool = True

class PromptPipelineData(BaseModel):
    metadata: bool = True
    chapters: bool = True
    thumbnail_with_text: bool = True
    thumbnail_without_text: bool = True
    audio: bool = True


class PromptVersion(BaseModel):
    name: str
    prompts: dict
    project_url: str = chatgpt_projects.DEFAULT_CHATGPT_PROJECT_URL
    default_voice_id: str = ""
    pipeline: PromptPipelineData = Field(default_factory=PromptPipelineData)

class PromptsData(BaseModel):
    active_version: str
    versions: dict[str, PromptVersion]


class PromptVersionNameData(BaseModel):
    name: str


class PromptProjectData(BaseModel):
    project_url: str


class PromptDefaultVoiceData(BaseModel):
    voice_id: str = ""


class PromptFieldData(BaseModel):
    value: str


class VoiceOptionData(BaseModel):
    id: str
    name: str


class VoiceConfigData(BaseModel):
    active_voice_id: str
    voices: List[VoiceOptionData]

DEFAULT_GPT_ACCOUNT_KEY = "gpt_account1"
DEFAULT_GPT_PROFILE = "PROFILE_GPT_1"

def _extract_gpt_account(data: dict) -> dict:
    if DEFAULT_GPT_ACCOUNT_KEY in data and isinstance(data[DEFAULT_GPT_ACCOUNT_KEY], dict):
        return data[DEFAULT_GPT_ACCOUNT_KEY]
    if "email" in data:
        return data
    return {}

def _save_account_payload(account: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = {}
    if ACCOUNT_PATH.exists():
        try:
            existing = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    
    # Merge with existing gpt_account1 to preserve session_cookie etc.
    current_account = _extract_gpt_account(existing)
    current_account.update(account)
    
    existing[DEFAULT_GPT_ACCOUNT_KEY] = current_account
    ACCOUNT_PATH.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

app = FastAPI(title="Auto_YT API")

# Configure CORS for Vite React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/thumbnails/{filename}")
async def serve_thumbnail(filename: str):
    """Serve locally downloaded thumbnail images."""
    file_path = THUMBNAILS_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    
    response = FileResponse(str(file_path), media_type="image/png")
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.get("/api/audio/{filename}")
async def serve_audio(filename: str):
    """Serve a locally merged long-form audio file."""
    if Path(filename).name != filename or not filename.lower().endswith(".mp3"):
        raise HTTPException(status_code=400, detail="Invalid audio filename")
    file_path = AUDIO_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    response = FileResponse(str(file_path), media_type="audio/mpeg")
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

class VideoRequest(BaseModel):
    url: str
    prompt_version: Optional[str] = None
    voice_id: Optional[str] = None

class VideoResponse(BaseModel):
    success: bool
    full_transcript: str
    summary: str
    chat_url: str = ""
    error: str = None
    video_id: Optional[int] = None


class RetryAudioRequest(BaseModel):
    confirm_credit_charge: bool


class RegenerateAudioRequest(BaseModel):
    voice_id: str
    confirm_credit_charge: bool


class ApproveAudioReviewRequest(BaseModel):
    confirm_credit_charge: bool
    voice_id: Optional[str] = None


class AudioDurationRequest(BaseModel):
    duration_seconds: float


class YouTubeLinkRequest(BaseModel):
    url: str


class YouTubeDownloadVideoRequest(BaseModel):
    id: str
    title: str
    url: str
    position: Optional[int] = None


class YouTubeDownloadRequest(BaseModel):
    destination: str
    videos: List[YouTubeDownloadVideoRequest]
    number_folders: bool = False


@app.post("/api/youtube-download/list")
def list_youtube_downloads(request: YouTubeLinkRequest):
    try:
        videos = list_youtube_videos(request.url)
        return {
            "success": True,
            "videos": videos,
            "total": len(videos),
            "is_channel": is_youtube_channel_url(request.url),
        }
    except (ValueError, YouTubeDownloaderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/youtube-download/select-folder")
def choose_youtube_download_folder():
    try:
        selected_path = select_download_directory()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Không thể mở cửa sổ chọn thư mục: {exc}",
        ) from exc
    return {
        "success": True,
        "cancelled": not bool(selected_path),
        "path": selected_path,
    }


@app.post("/api/youtube-download/start")
def start_youtube_download(request: YouTubeDownloadRequest):
    try:
        job_id = download_jobs.start(
            request.destination,
            [video.model_dump() for video in request.videos],
            number_folders=request.number_folders,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "job_id": job_id}


@app.get("/api/youtube-download/jobs/{job_id}")
def get_youtube_download_job(job_id: str):
    job = download_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Download job not found")
    return {"success": True, "job": job}


def _control_youtube_download(job_id: str, action: str):
    try:
        job = getattr(download_jobs, action)(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "job": job}


@app.post("/api/youtube-download/jobs/{job_id}/pause")
def pause_youtube_download(job_id: str):
    return _control_youtube_download(job_id, "pause")


@app.post("/api/youtube-download/jobs/{job_id}/resume")
def resume_youtube_download(job_id: str):
    return _control_youtube_download(job_id, "resume")


@app.post("/api/youtube-download/jobs/{job_id}/stop")
def stop_youtube_download(job_id: str):
    return _control_youtube_download(job_id, "stop")


def apply_tts_filters(text: str) -> str:
    """
    Bộ lọc xử lý văn bản trước khi gửi cho TTS API để tránh đọc sai.
    Thay thế dựa trên regex (có phân biệt hoa/thường).
    """
    # Các từ cần sửa lỗi phát âm, dùng \b để chỉ match từ độc lập
    replacements = {
        r'\bAI\b': 'Ây ai',      # Tránh bị đọc thành "ai"
        r'\bADN\b': 'A đê en',
        r'\bCEO\b': 'Xi i ô',
        r'\bIT\b': 'Ai ti',
        r'\bVlog\b': 'Vê lốc',
        r'\bYouTube\b': 'Yêu túp'
    }
    
    filtered_text = text
    for pattern, replacement in replacements.items():
        filtered_text = re.sub(pattern, replacement, filtered_text)
        
    return filtered_text

def get_clean_script_for_tts(text: str) -> str:
    return get_audio_script(text)


AUDIO_VOICE_ID = voice_config.DEFAULT_VOICE_ID
AUDIO_POLL_INTERVAL_SECONDS = 30
AUDIO_INTERRUPTED_STATUS = "interrupted"
_audio_submit_lock = threading.Lock()
_audio_watchers: dict[int, threading.Thread] = {}
_audio_watchers_lock = threading.Lock()
_audio_sync_locks: dict[int, threading.Lock] = {}
_audio_sync_locks_guard = threading.Lock()


def _audio_review_response(review: dict) -> dict:
    report = review.get("report") or {}
    return {
        "video_id": review["video_id"],
        "script_hash": review.get("script_hash", ""),
        "status": review.get("status", "pending"),
        "reviewed_at": review.get("reviewed_at", ""),
        "updated_at": review.get("updated_at", ""),
        "can_approve": bool(report.get("can_approve")),
        "errors": report.get("errors") or [],
        "warnings": report.get("warnings") or [],
        "metrics": report.get("metrics") or {},
    }


def _prepare_audio_review(video_id: int) -> dict:
    video = db.get_video(video_id)
    if not video:
        raise RuntimeError("Video không tồn tại.")

    report = audit_script_for_audio(video.get("generated_script", ""))
    previous_review = db.get_audio_review(video_id)
    same_approved_script = (
        previous_review
        and previous_review.get("status") == "approved"
        and previous_review.get("script_hash") == report["script_hash"]
        and report["can_approve"]
    )
    status = (
        "approved"
        if same_approved_script
        else "pending" if report["can_approve"] else "blocked"
    )
    reviewed_at = (
        previous_review.get("reviewed_at", "") if same_approved_script else ""
    )
    return db.upsert_audio_review(
        video_id=video_id,
        script_hash=report["script_hash"],
        status=status,
        report=report,
        reviewed_at=reviewed_at,
    )


def _automatically_approve_audio_review(video_id: int) -> dict:
    """Approve the current narrative hash only when deterministic checks pass."""
    review = _prepare_audio_review(video_id)
    report = review.get("report") or {}
    if not report.get("can_approve"):
        return review
    if review.get("status") == "approved":
        return review
    return db.upsert_audio_review(
        video_id=video_id,
        script_hash=review["script_hash"],
        status="approved",
        report=report,
        reviewed_at=db.utc_now(),
    )


def _auto_review_and_create_audio(
    video_id: int,
    requested_voice_id: str = "",
    requested_voice_name: str = "",
) -> tuple[dict, dict | None, str]:
    """Run the quality gate and idempotent TTS submission as one system step."""
    review = _automatically_approve_audio_review(video_id)
    if review.get("status") != "approved":
        return (
            review,
            None,
            "Kịch bản không đạt kiểm tra tự động; chưa gửi sang Genmax.",
        )
    try:
        task = _ensure_audio_task(
            video_id,
            requested_voice_id=requested_voice_id,
            requested_voice_name=requested_voice_name,
        )
        if task.get("status") == "failed":
            return (
                review,
                task,
                task.get("error")
                or "Task Genmax đã thất bại; hệ thống không tự retry để tránh trừ credit.",
            )
        return review, task, ""
    except Exception as exc:
        return review, db.get_audio_task(video_id), str(exc) or repr(exc)


def _require_audio_review_approval(video_id: int) -> dict:
    review = _prepare_audio_review(video_id)
    if review.get("status") != "approved":
        raise RuntimeError(
            "Kịch bản chưa đạt bước kiểm tra tự động cho nội dung hiện tại."
        )
    return review


def _audio_task_response(task: dict) -> dict:
    segments = _get_audio_segments(task)
    missing_segments = sum(not segment.get("task_id") for segment in segments)
    return {
        "video_id": task["video_id"],
        "task_id": task["task_id"],
        "status": task["status"],
        "audio_url": task.get("audio_url", ""),
        "error": task.get("error", ""),
        "voice_id": task.get("voice_id", ""),
        "voice_name": task.get("voice_name", ""),
        "updated_at": task["updated_at"],
        "segment_count": len(segments),
        "completed_segments": sum(
            segment.get("status") == "completed" for segment in segments
        ),
        "missing_segments": missing_segments,
    }


def _get_audio_segments(task: dict) -> list[dict]:
    serialized = task.get("segments_json", "")
    if not serialized:
        return []
    try:
        segments = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Stored audio segment data is invalid.") from exc
    if not isinstance(segments, list):
        raise RuntimeError("Stored audio segment data is invalid.")
    return segments


def _save_audio_url(
    video_id: int,
    audio_url: str,
    voice_id: str = "",
    voice_name: str = "",
) -> str:
    video = db.get_video(video_id)
    if not video:
        raise RuntimeError("Video không còn tồn tại.")

    script = video["generated_script"]
    audio_section = f"### [AUDIO]\n{audio_url}"
    if "### [AUDIO]" in script:
        updated_script = re.sub(
            r"### \[AUDIO\]\n.*?(?=\n### \[|\Z)",
            audio_section,
            script,
            flags=re.DOTALL,
        )
    else:
        updated_script = f"{script.rstrip()}\n\n{audio_section}"

    if updated_script != script:
        db.update_script(video_id, updated_script)
    if voice_id:
        db.update_video_voice(video_id, voice_id, voice_name)
    return updated_script


def _get_audio_sync_lock(video_id: int) -> threading.Lock:
    with _audio_sync_locks_guard:
        return _audio_sync_locks.setdefault(video_id, threading.Lock())


def _sync_audio_task(video_id: int) -> dict:
    # The background watcher and frontend status polling can run at the same
    # time. Only one of them may fetch/merge/write a video's audio at once.
    with _get_audio_sync_lock(video_id):
        return _sync_audio_task_unlocked(video_id)


def _sync_audio_task_unlocked(video_id: int) -> dict:
    stored_task = db.get_audio_task(video_id)
    if not stored_task:
        raise RuntimeError("Không tìm thấy audio task.")

    segments = _get_audio_segments(stored_task)
    if segments:
        return _sync_batch_audio_task(stored_task, segments)

    remote_task = tts.get_tts_task(stored_task["task_id"])
    status = remote_task.get("status", stored_task["status"])
    audio_url = (remote_task.get("result") or {}).get("audio_url", "")
    error = remote_task.get("error") or remote_task.get("detail_error") or ""

    duration_seconds = None
    if status == "completed":
        if not audio_url:
            status = "failed"
            error = "Task Genmax completed without an audio URL."
        else:
            try:
                duration_seconds = audio_utils.get_remote_mp3_duration(audio_url)
                audio_utils.validate_spoken_duration(
                    remote_task.get("text", ""),
                    duration_seconds,
                )
            except audio_utils.AudioContentError as exc:
                status = "failed"
                audio_url = ""
                error = str(exc)

    task = db.upsert_audio_task(
        video_id=video_id,
        request_hash=stored_task["request_hash"],
        task_id=stored_task["task_id"],
        status=status,
        audio_url=audio_url,
        error=str(error),
        segments_json="",
        voice_id=stored_task.get("voice_id", ""),
        voice_name=stored_task.get("voice_name", ""),
    )
    if status == "completed":
        _save_audio_url(
            video_id,
            audio_url,
            task.get("voice_id", ""),
            task.get("voice_name", ""),
        )
        db.update_audio_duration(video_id, duration_seconds)
    return task


def _sync_batch_audio_task(stored_task: dict, segments: list[dict]) -> dict:
    video_id = stored_task["video_id"]
    task_voice_id = stored_task.get("voice_id") or AUDIO_VOICE_ID
    task_voice_name = stored_task.get("voice_name", "")
    sync_errors = []
    for segment in segments:
        if segment.get("status") not in {"pending", "processing"}:
            continue
        task_id = segment.get("task_id", "")
        if not task_id:
            continue
        try:
            remote_task = tts.get_tts_task(task_id)
        except Exception as exc:
            # Preserve progress from every other segment. A transient failure
            # must not discard a whole polling cycle and leave the UI stale.
            sync_errors.append((segment.get("index", 0), str(exc)))
            segment["error"] = f"Tạm thời chưa đồng bộ được: {exc}"
            continue
        segment["status"] = remote_task.get("status", segment["status"])
        segment["audio_url"] = (
            (remote_task.get("result") or {}).get("audio_url", "")
        )
        if segment["status"] == "completed" and not segment["audio_url"]:
            segment["status"] = "processing"
        segment["error"] = str(
            remote_task.get("error") or remote_task.get("detail_error") or ""
        )

    failed_segments = [
        segment for segment in segments if segment.get("status") == "failed"
    ]
    missing_segments = [segment for segment in segments if not segment.get("task_id")]
    all_completed = all(
        segment.get("status") == "completed" and segment.get("audio_url")
        for segment in segments
    )
    status = "processing"
    error = (
        "Tạm thời chưa đồng bộ được đoạn: "
        + ", ".join(str(index + 1) for index, _ in sync_errors)
        if sync_errors
        else ""
    )
    audio_url = ""

    if failed_segments:
        status = "failed"
        failed_numbers = ", ".join(
            str(segment["index"] + 1) for segment in failed_segments
        )
        error = f"Genmax failed on audio segment(s): {failed_numbers}."
    elif missing_segments:
        status = AUDIO_INTERRUPTED_STATUS
        error = (
            "Quá trình gửi audio bị gián đoạn. "
            f"Còn {len(missing_segments)} phần chưa gửi; hãy tiếp tục để chỉ "
            "gửi các phần còn thiếu."
        )
    elif all_completed:
        filename = f"video_{video_id}_{stored_task['request_hash'][:16]}.mp3"
        output_path = AUDIO_DIR / filename
        try:
            video = db.get_video(video_id)
            script_for_tts = apply_tts_filters(
                get_clean_script_for_tts(video["generated_script"])
            )
            current_request_hash = tts.get_generation_request_hash(
                script_for_tts,
                task_voice_id,
            )
            if current_request_hash != stored_task["request_hash"]:
                raise audio_utils.AudioContentError(
                    "The script changed while audio was being generated."
                )
            chunks = tts.split_text_for_tts(script_for_tts)
            duration_seconds = audio_utils.merge_remote_mp3_files(
                [segment["audio_url"] for segment in segments],
                output_path,
                expected_texts=chunks,
            )
            audio_utils.validate_spoken_duration(script_for_tts, duration_seconds)
            audio_url = f"http://127.0.0.1:8080/api/audio/{filename}"
            status = "completed"
            db.update_audio_duration(video_id, duration_seconds)
        except audio_utils.AudioContentError as exc:
            output_path.unlink(missing_ok=True)
            status = "failed"
            error = str(exc)

    task = db.upsert_audio_task(
        video_id=video_id,
        request_hash=stored_task["request_hash"],
        task_id=stored_task["task_id"],
        status=status,
        audio_url=audio_url,
        error=error,
        segments_json=json.dumps(segments, ensure_ascii=False),
        voice_id=task_voice_id,
        voice_name=task_voice_name,
    )
    if status == "completed":
        _save_audio_url(
            video_id,
            audio_url,
            task_voice_id,
            task_voice_name,
        )
    return task


def _watch_audio_task(video_id: int) -> None:
    try:
        while True:
            try:
                task = _sync_audio_task(video_id)
                if task["status"] in {
                    "completed",
                    "failed",
                    AUDIO_INTERRUPTED_STATUS,
                }:
                    return
            except Exception as exc:
                print(
                    f"Audio task sync failed for video {video_id}: {exc}",
                    file=sys.stderr,
                )
            time.sleep(AUDIO_POLL_INTERVAL_SECONDS)
    finally:
        with _audio_watchers_lock:
            _audio_watchers.pop(video_id, None)


def _start_audio_watcher(video_id: int) -> None:
    with _audio_watchers_lock:
        watcher = _audio_watchers.get(video_id)
        if watcher and watcher.is_alive():
            return
        watcher = threading.Thread(
            target=_watch_audio_task,
            args=(video_id,),
            daemon=True,
        )
        _audio_watchers[video_id] = watcher
        watcher.start()


def _segment_from_remote(
    index: int,
    text: str,
    remote_task: dict,
    voice_id: str,
) -> dict:
    status = remote_task.get("status", "pending")
    audio_url = (remote_task.get("result") or {}).get("audio_url", "")
    if status == "completed" and not audio_url:
        status = "processing"
    return {
        "index": index,
        "text_hash": tts.get_request_hash(text, voice_id),
        "characters": len(text),
        "task_id": remote_task["id"],
        "status": status,
        "audio_url": audio_url,
        "error": str(
            remote_task.get("error") or remote_task.get("detail_error") or ""
        ),
    }


def _store_batch_audio_task(
    video_id: int,
    request_hash: str,
    segments: list[dict],
    voice_id: str,
    voice_name: str,
    status: str = "pending",
    error: str = "",
) -> dict:
    return db.upsert_audio_task(
        video_id=video_id,
        request_hash=request_hash,
        task_id=f"batch-{request_hash[:24]}",
        status=status,
        error=error,
        segments_json=json.dumps(segments, ensure_ascii=False),
        voice_id=voice_id,
        voice_name=voice_name,
    )


def _ensure_batch_audio_task(
    video_id: int,
    request_hash: str,
    chunks: list[str],
    stored_task: dict | None,
    voice_id: str = AUDIO_VOICE_ID,
    voice_name: str = "",
) -> dict:
    existing_segments = _get_audio_segments(stored_task) if stored_task else []
    existing_by_index = {
        segment.get("index"): segment
        for segment in existing_segments
        if segment.get("task_id")
    }

    if stored_task and stored_task["request_hash"] == request_hash:
        if stored_task["status"] == "completed":
            _save_audio_url(
                video_id,
                stored_task["audio_url"],
                voice_id,
                voice_name,
            )
            return stored_task
        if stored_task["status"] == "failed" and all(
            segment.get("task_id") for segment in existing_segments
        ):
            return stored_task
        if len(existing_by_index) == len(chunks):
            _start_audio_watcher(video_id)
            return stored_task

    # Fail closed: history must be checked before any new paid segment is sent.
    remote_matches = tts.find_matching_tasks(chunks, voice_id)
    segments = []
    for index, chunk in enumerate(chunks):
        existing_segment = existing_by_index.get(index)
        if existing_segment:
            segments.append(existing_segment)
            continue
        remote_task = remote_matches.get(chunk)
        if remote_task:
            segments.append(
                _segment_from_remote(index, chunk, remote_task, voice_id)
            )
        else:
            segments.append({
                "index": index,
                "text_hash": tts.get_request_hash(chunk, voice_id),
                "characters": len(chunk),
                "task_id": "",
                "status": "not_submitted",
                "audio_url": "",
                "error": "",
            })

    task = _store_batch_audio_task(
        video_id,
        request_hash,
        segments,
        voice_id,
        voice_name,
    )
    try:
        for segment in segments:
            if segment["task_id"]:
                continue
            submitted_task = tts.submit_tts_task(
                chunks[segment["index"]],
                voice_id,
            )
            segment.update(
                _segment_from_remote(
                    segment["index"],
                    chunks[segment["index"]],
                    submitted_task,
                    voice_id,
                )
            )
            task = _store_batch_audio_task(
                video_id,
                request_hash,
                segments,
                voice_id,
                voice_name,
            )
    except Exception as exc:
        missing_count = sum(not segment.get("task_id") for segment in segments)
        _store_batch_audio_task(
            video_id,
            request_hash,
            segments,
            voice_id,
            voice_name,
            status=AUDIO_INTERRUPTED_STATUS,
            error=(
                f"Không thể gửi tiếp audio: {exc}. "
                f"Còn {missing_count} phần chưa gửi."
            ),
        )
        raise

    if all(
        segment.get("status") == "completed" and segment.get("audio_url")
        for segment in segments
    ):
        return _sync_batch_audio_task(task, segments)
    if any(segment.get("status") == "failed" for segment in segments):
        return _sync_batch_audio_task(task, segments)

    _start_audio_watcher(video_id)
    return task


def _ensure_audio_task(
    video_id: int,
    requested_voice_id: str = "",
    requested_voice_name: str = "",
) -> dict:
    video = db.get_video(video_id)
    if not video:
        raise RuntimeError("Video không tồn tại.")
    _require_audio_review_approval(video_id)

    existing_task = db.get_audio_task(video_id)
    if requested_voice_id:
        voice_id = requested_voice_id
        voice_name = requested_voice_name
    elif (
        existing_task
        and existing_task.get("status") != "completed"
        and existing_task.get("voice_id")
    ):
        voice_id = existing_task["voice_id"]
        voice_name = existing_task.get("voice_name", "")
    elif video.get("voice_id"):
        voice_id = video["voice_id"]
        voice_name = video.get("voice_name", "")
    else:
        configured_voice = voice_config.get_voice()
        voice_id = configured_voice["id"]
        voice_name = configured_voice["name"]

    if not voice_name:
        try:
            voice_name = voice_config.get_voice(voice_id)["name"]
        except ValueError:
            voice_name = "Giọng đã lưu"

    script_for_tts = get_clean_script_for_tts(video["generated_script"])
    if not script_for_tts:
        raise RuntimeError(
            "Không tìm thấy kịch bản để đọc (thiếu INTRO/BODY/OUTRO)."
        )

    filtered_script = apply_tts_filters(script_for_tts)
    chunks = tts.split_text_for_tts(filtered_script)
    request_hash = tts.get_generation_request_hash(
        filtered_script,
        voice_id,
    )

    with _audio_submit_lock:
        stored_task = db.get_audio_task(video_id)
        if stored_task:
            if stored_task["request_hash"] != request_hash:
                if stored_task["status"] in {"pending", "processing"}:
                    raise RuntimeError(
                        "Video đang có một audio task cho phiên bản kịch bản trước."
                    )
            elif len(chunks) == 1:
                if stored_task["status"] == "completed":
                    _save_audio_url(
                        video_id,
                        stored_task["audio_url"],
                        voice_id,
                        voice_name,
                    )
                elif stored_task["status"] in {"pending", "processing"}:
                    _start_audio_watcher(video_id)
                return stored_task

        if len(chunks) > 1:
            return _ensure_batch_audio_task(
                video_id,
                request_hash,
                chunks,
                (
                    stored_task
                    if stored_task and stored_task["request_hash"] == request_hash
                    else None
                ),
                voice_id,
                voice_name,
            )

        shared_task = db.get_audio_task_by_request_hash(request_hash)
        if shared_task:
            task = db.upsert_audio_task(
                video_id=video_id,
                request_hash=request_hash,
                task_id=shared_task["task_id"],
                status=shared_task["status"],
                audio_url=shared_task.get("audio_url", ""),
                error=shared_task.get("error", ""),
                segments_json="",
                voice_id=voice_id,
                voice_name=voice_name,
            )
            if task["status"] == "completed":
                _save_audio_url(
                    video_id,
                    task["audio_url"],
                    voice_id,
                    voice_name,
                )
            elif task["status"] in {"pending", "processing"}:
                _start_audio_watcher(video_id)
            return task

        remote_task = tts.find_matching_task(filtered_script, voice_id)
        if remote_task:
            status = remote_task.get("status", "pending")
            audio_url = (remote_task.get("result") or {}).get("audio_url", "")
            error = remote_task.get("error") or remote_task.get("detail_error") or ""
            task = db.upsert_audio_task(
                video_id=video_id,
                request_hash=request_hash,
                task_id=remote_task["id"],
                status=status,
                audio_url=audio_url,
                error=str(error),
                segments_json="",
                voice_id=voice_id,
                voice_name=voice_name,
            )
            _start_audio_watcher(video_id)
            return task

        submitted_task = tts.submit_tts_task(filtered_script, voice_id)
        task = db.upsert_audio_task(
            video_id=video_id,
            request_hash=request_hash,
            task_id=submitted_task["id"],
            status=submitted_task.get("status", "pending"),
            segments_json="",
            voice_id=voice_id,
            voice_name=voice_name,
        )
        _start_audio_watcher(video_id)
        return task


@app.on_event("startup")
def resume_background_jobs() -> None:
    for task in db.get_active_audio_tasks():
        _start_audio_watcher(task["video_id"])
    db.recover_interrupted_system_jobs("video_generation")
    _kick_video_queue()


@app.on_event("shutdown")
def stop_video_queue_wakeup_timer() -> None:
    global _video_queue_wakeup_at, _video_queue_wakeup_timer
    with _video_queue_state_lock:
        if _video_queue_wakeup_timer is not None:
            _video_queue_wakeup_timer.cancel()
        _video_queue_wakeup_timer = None
        _video_queue_wakeup_at = 0.0


class VideoJobCanceled(RuntimeError):
    pass


def _sync_legacy_job(job: dict | None) -> None:
    if not job:
        return
    with _jobs_lock:
        _jobs[job["id"]] = {
            "status": job["status"],
            "progress": job.get("progress", ""),
            "result": job.get("result") or None,
            "error": job.get("error") or None,
            "queue_position": db.get_system_job_queue_position(job["id"]),
        }


def _update_video_job(job_id: str, **changes) -> dict | None:
    job = db.update_system_job(job_id, **changes)
    _sync_legacy_job(job)
    return job


def _raise_if_video_job_canceled(job_id: str) -> None:
    job = db.get_system_job(job_id)
    if job and job.get("cancel_requested"):
        raise VideoJobCanceled("Job đã được người dùng yêu cầu dừng.")


def _schedule_automatic_video_recovery(
    job_id: str,
    video_id: int | None,
    error: str,
    result: dict | None,
) -> bool:
    if video_id is None:
        return False
    checkpoint = load_checkpoint(video_id)
    resume_from_step = str(checkpoint.get("current_step") or "").strip()
    if not checkpoint or not resume_from_step or resume_from_step == "complete":
        return False

    job = db.get_system_job(job_id)
    recovery_count = int((job or {}).get("recovery_count") or 0)
    if recovery_count >= len(VIDEO_RECOVERY_DELAYS_SECONDS):
        return False

    delay_seconds = VIDEO_RECOVERY_DELAYS_SECONDS[recovery_count]
    recovered_job = db.schedule_system_job_recovery(
        job_id,
        resume_from_step=resume_from_step,
        delay_seconds=delay_seconds,
        error=error,
        result_json=result,
    )
    if not recovered_job:
        return False
    recovered_job = db.update_system_job(
        job_id,
        progress=(
            f"Đã lưu checkpoint; tự phục hồi bước {resume_from_step} sau "
            f"{delay_seconds} giây "
            f"(lần {recovered_job['recovery_count']}/"
            f"{len(VIDEO_RECOVERY_DELAYS_SECONDS)})"
        ),
    )
    _sync_legacy_job(recovered_job)
    _schedule_video_queue_wakeup(delay_seconds)
    return True


def _execute_video_job(job: dict) -> None:
    job_id = job["id"]
    payload = job.get("payload") or {}
    request_url = str(payload.get("url") or "").strip()
    prompt_version = str(job.get("prompt_version") or payload.get("prompt_version") or "")
    voice_id = str(job.get("voice_id") or payload.get("voice_id") or "")
    voice_name = str(payload.get("voice_name") or "")
    pipeline = chatgpt_projects.normalize_prompt_pipeline(
        payload.get("pipeline")
        if "pipeline" in payload
        else _get_prompt_pipeline(prompt_version)
    )
    video_id = job.get("video_id")
    full_transcript = ""
    title = ""
    completed_script_before_restart = ""

    def update(message: str) -> None:
        _update_video_job(job_id, progress=message)

    try:
        _raise_if_video_job_canceled(job_id)
        if video_id:
            existing_video = db.get_video(video_id)
            if not existing_video:
                raise RuntimeError(f"Không tìm thấy bản nháp video #{video_id} để tiếp tục.")
            full_transcript = existing_video["transcript"]
            title = existing_video["title"]
            existing_script = existing_video.get("generated_script", "")
            if (
                existing_script
                and existing_script != INITIAL_GENERATED_SCRIPT
                and not load_checkpoint(video_id)
                and get_clean_script_for_tts(existing_script)
            ):
                completed_script_before_restart = existing_script
            update("Đang tiếp tục bản nháp đã lưu")
        else:
            update("Đang tải phụ đề YouTube")
            full_transcript = get_video_transcript(request_url)
            _raise_if_video_job_canceled(job_id)
            title = get_video_title(request_url)
            update("Đang lưu bản nháp an toàn")
            video_id = db.save_video(
                request_url,
                title,
                full_transcript,
                INITIAL_GENERATED_SCRIPT,
                "",
                prompt_version,
                voice_id,
                voice_name,
            )
            _update_video_job(job_id, video_id=video_id, title=title)

        _raise_if_video_job_canceled(job_id)
        if completed_script_before_restart:
            update("Đã tìm thấy kịch bản hoàn tất; đang tiếp tục bước audio")
            worker_result = {
                "script": completed_script_before_restart,
                "chat_url": existing_video.get("chat_url", ""),
                "warning": "",
                "complete_for_audio": True,
            }
        else:
            combined_text = (
                f"TIÊU ĐỀ KỊCH BẢN: {title}\n\nNỘI DUNG:\n{full_transcript}"
            )
            update("ChatGPT đang viết kịch bản (5-15 phút)")
            worker_result = process_prompt_via_chatgpt(
                combined_text,
                prompt_version,
                video_id,
                pipeline=pipeline,
            )
        _raise_if_video_job_canceled(job_id)
        summary_text = (
            worker_result["script"]
            if isinstance(worker_result, dict)
            else worker_result
        )
        summary_text = sanitize_generated_script(summary_text)
        chat_url = (
            worker_result.get("chat_url", "")
            if isinstance(worker_result, dict)
            else ""
        )
        generation_warning = (
            worker_result.get("warning", "")
            if isinstance(worker_result, dict)
            else ""
        )
        failed_step = (
            worker_result.get("failed_step", "")
            if isinstance(worker_result, dict)
            else ""
        )
        complete_for_audio = (
            worker_result.get("complete_for_audio", True)
            if isinstance(worker_result, dict)
            else True
        )

        update("Đang lưu vào database")
        if not db.update_video_generation(video_id, summary_text, chat_url):
            raise RuntimeError("Không thể cập nhật bản nháp video.")
        if not generation_warning:
            clear_checkpoint(video_id)

        _raise_if_video_job_canceled(job_id)
        if pipeline["audio"]:
            update("Đang tự động kiểm tra kịch bản trước bước audio")
        else:
            update("Đã hoàn thành các bước tự động của pipeline")
        audio_task = None
        audio_error = ""
        audio_review = None
        if complete_for_audio and pipeline["audio"]:
            audio_review, audio_task, audio_error = _auto_review_and_create_audio(
                video_id,
                requested_voice_id=voice_id,
                requested_voice_name=voice_name,
            )
        elif not complete_for_audio:
            audio_review = _prepare_audio_review(video_id)
        review_response = (
            _audio_review_response(audio_review) if audio_review else None
        )

        result = {
            "success": True,
            "full_transcript": full_transcript,
            "summary": summary_text,
            "title": title,
            "prompt_version": prompt_version,
            "voice_id": voice_id,
            "voice_name": voice_name,
            "pipeline": pipeline,
            "chat_url": chat_url,
            "video_id": video_id,
            "audio_task": (
                _audio_task_response(audio_task) if audio_task else None
            ),
            "audio_error": audio_error or None,
            "audio_review": review_response,
            "generation_warning": generation_warning,
            "complete_for_audio": complete_for_audio,
            "failed_step": failed_step,
        }
        if generation_warning and _schedule_automatic_video_recovery(
            job_id,
            video_id,
            generation_warning,
            result,
        ):
            return
        _update_video_job(
            job_id,
            status="error" if generation_warning else "done",
            progress=(
                (
                    "Đã giữ phần hoàn tất nhưng tự phục hồi đã hết số lần thử"
                )
                if generation_warning
                else (
                    "Kịch bản đã hoàn thành; pipeline không tự tạo audio"
                    if not pipeline["audio"]
                    else (
                        "Kịch bản không đạt kiểm tra tự động; chưa tạo audio"
                        if audio_review and audio_review.get("status") == "blocked"
                        else (
                            "Kịch bản đã tự động duyệt; audio chưa thể khởi tạo"
                            if audio_error
                            else (
                                "Kịch bản và audio đã hoàn thành"
                                if audio_task and audio_task.get("status") == "completed"
                                else "Kịch bản đã tự động duyệt; Genmax đang tạo audio"
                            )
                        )
                    )
                )
            ),
            result_json=result,
            error=generation_warning,
            resume_from_step="",
            next_retry_at="",
            cancel_requested=0,
            finished_at=db.utc_now(),
        )
    except VideoJobCanceled as exc:
        _update_video_job(
            job_id,
            status="canceled",
            progress="Đã dừng tại điểm an toàn",
            error=str(exc),
            cancel_requested=0,
            finished_at=db.utc_now(),
        )
    except Exception as exc:
        error_message = str(exc) or repr(exc)
        if (
            "Could not retrieve a transcript" in error_message
            or "Subtitles are disabled" in error_message
        ):
            error_message = (
                "Video này không có phụ đề (Transcript). Vui lòng chọn video khác."
            )

        recovered_result = None
        if video_id is not None:
            checkpoint = load_checkpoint(video_id)
            if checkpoint:
                from auto_yt.services.chatgpt_worker import build_video_script

                partial_script = build_video_script(checkpoint)
                checkpoint_chat_url = checkpoint.get("chat_url", "")
                db.update_video_generation(
                    video_id,
                    partial_script,
                    checkpoint_chat_url,
                )
                recovered_result = {
                    "success": True,
                    "full_transcript": full_transcript,
                    "summary": partial_script,
                    "title": title,
                    "prompt_version": prompt_version,
                    "voice_id": voice_id,
                    "voice_name": voice_name,
                    "pipeline": pipeline,
                    "chat_url": checkpoint_chat_url,
                    "video_id": video_id,
                    "audio_task": None,
                    "audio_error": None,
                    "generation_warning": error_message,
                    "complete_for_audio": False,
                    "failed_step": checkpoint.get("current_step", "unknown"),
                }
            error_message = (
                f"{error_message} Bản nháp video #{video_id} đã được lưu ở Dashboard."
            )

        if recovered_result and _schedule_automatic_video_recovery(
            job_id,
            video_id,
            error_message,
            recovered_result,
        ):
            return

        _update_video_job(
            job_id,
            status="error",
            progress=(
                "Đã giữ phần hoàn tất nhưng tự phục hồi đã hết số lần thử"
                if recovered_result
                else "Tạo video thất bại"
            ),
            result_json=recovered_result or {},
            error=error_message,
            cancel_requested=0,
            finished_at=db.utc_now(),
        )
    finally:
        _finish_chatgpt_operation()


def _drain_video_queue() -> None:
    global _video_queue_worker_active
    blocked_by_chatgpt = False
    try:
        while True:
            job = db.claim_next_system_job("video_generation")
            if not job:
                return
            _sync_legacy_job(job)
            if not _try_start_chatgpt_operation(
                "video",
                job.get("prompt_version", ""),
            ):
                blocked_by_chatgpt = True
                _update_video_job(
                    job["id"],
                    status="queued",
                    progress="Đang chờ tác vụ ChatGPT hiện tại hoàn tất",
                )
                return
            _execute_video_job(job)
            current_job = db.get_system_job(job["id"])
            if current_job and current_job["status"] == "retry_wait":
                return
    finally:
        with _video_queue_state_lock:
            _video_queue_worker_active = False
        if (
            not blocked_by_chatgpt
            and not _chatgpt_profile_lock.locked()
        ):
            if db.has_claimable_system_jobs("video_generation"):
                _kick_video_queue()
            else:
                _schedule_video_queue_wakeup()


def _schedule_video_queue_wakeup(delay_seconds: float | None = None) -> None:
    global _video_queue_wakeup_at, _video_queue_wakeup_timer
    if delay_seconds is None:
        delay_seconds = db.get_next_system_job_retry_delay("video_generation")
    if delay_seconds is None:
        return

    delay_seconds = max(0.05, float(delay_seconds))
    wakeup_at = time.monotonic() + delay_seconds

    def wake_queue() -> None:
        global _video_queue_wakeup_at, _video_queue_wakeup_timer
        with _video_queue_state_lock:
            if _video_queue_wakeup_timer is not timer:
                return
            _video_queue_wakeup_timer = None
            _video_queue_wakeup_at = 0.0
        _kick_video_queue()

    with _video_queue_state_lock:
        if (
            _video_queue_wakeup_timer is not None
            and _video_queue_wakeup_timer.is_alive()
            and _video_queue_wakeup_at <= wakeup_at + 0.05
        ):
            return
        if _video_queue_wakeup_timer is not None:
            _video_queue_wakeup_timer.cancel()
        timer = threading.Timer(delay_seconds, wake_queue)
        timer.daemon = True
        _video_queue_wakeup_timer = timer
        _video_queue_wakeup_at = wakeup_at
    timer.start()


def _kick_video_queue() -> None:
    global _video_queue_wakeup_at, _video_queue_wakeup_timer
    global _video_queue_worker_active
    should_schedule_wakeup = False
    with _video_queue_state_lock:
        if _video_queue_worker_active:
            return
        if not db.has_claimable_system_jobs("video_generation"):
            should_schedule_wakeup = True
        else:
            if _video_queue_wakeup_timer is not None:
                _video_queue_wakeup_timer.cancel()
                _video_queue_wakeup_timer = None
                _video_queue_wakeup_at = 0.0
            _video_queue_worker_active = True
    if should_schedule_wakeup:
        _schedule_video_queue_wakeup()
        return
    thread = threading.Thread(target=_drain_video_queue, daemon=True)
    try:
        thread.start()
    except Exception:
        with _video_queue_state_lock:
            _video_queue_worker_active = False
        raise

@app.post("/api/process-video")
def process_video(request: VideoRequest):
    resolved_prompt_version = request.prompt_version or _get_active_prompt_version_id()
    pipeline = _get_prompt_pipeline(resolved_prompt_version)
    requested_voice_id = request.voice_id or _get_prompt_default_voice_id(
        resolved_prompt_version
    )
    try:
        selected_voice = voice_config.get_voice(requested_voice_id)
    except ValueError as exc:
        if not request.voice_id:
            # A voice may have been removed after it was assigned to this
            # prompt version. Fall back safely to the global default.
            selected_voice = voice_config.get_voice()
        else:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    normalized_url = request.url.strip()
    if not normalized_url:
        raise HTTPException(status_code=400, detail="YouTube URL is required")
    for existing_job in db.list_system_jobs(
        limit=500,
        job_type="video_generation",
    ):
        existing_payload = existing_job.get("payload") or {}
        if (
            existing_job["status"] in {"queued", "running", "retry_wait", "paused"}
            and str(existing_payload.get("url") or "").strip() == normalized_url
            and existing_job.get("prompt_version", "") == resolved_prompt_version
            and existing_job.get("voice_id", "") == selected_voice["id"]
            and chatgpt_projects.normalize_prompt_pipeline(
                existing_payload.get("pipeline")
            ) == pipeline
        ):
            _sync_legacy_job(existing_job)
            _kick_video_queue()
            return {
                "job_id": existing_job["id"],
                "status": existing_job["status"],
                "queue_position": db.get_system_job_queue_position(
                    existing_job["id"]
                ),
                "duplicate": True,
            }

    job_id = uuid.uuid4().hex[:12]
    job = db.create_system_job(
        job_id=job_id,
        job_type="video_generation",
        title=normalized_url,
        payload={
            "url": normalized_url,
            "prompt_version": resolved_prompt_version,
            "voice_id": selected_voice["id"],
            "voice_name": selected_voice["name"],
            "pipeline": pipeline,
        },
        prompt_version=resolved_prompt_version,
        voice_id=selected_voice["id"],
    )
    _sync_legacy_job(job)
    _kick_video_queue()
    refreshed_job = db.get_system_job(job_id)
    return {
        "job_id": job_id,
        "status": refreshed_job["status"],
        "queue_position": db.get_system_job_queue_position(job_id),
    }


@app.post("/api/videos/{video_id}/continue-generation")
def continue_video_generation(video_id: int):
    """Resume a power-interrupted ChatGPT workflow in its original chat."""
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    checkpoint = load_checkpoint(video_id)
    if not checkpoint or not checkpoint.get("chat_url"):
        raise HTTPException(
            status_code=409,
            detail=(
                "Video chưa có URL chat trong checkpoint để tiếp tục an toàn. "
                "Không tạo chat mới tự động."
            ),
        )

    prompt_version = video.get("prompt_version", "")
    pipeline = chatgpt_projects.normalize_prompt_pipeline(
        checkpoint.get("pipeline")
        if "pipeline" in checkpoint
        else _get_prompt_pipeline(prompt_version)
    )
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "progress": "⏳ Đang khôi phục checkpoint...",
            "result": None,
            "error": None,
        }

    if not _try_start_chatgpt_operation("continue-video", prompt_version):
        with _jobs_lock:
            _jobs[job_id].update({
                "status": "error",
                "progress": f"❌ Lỗi: {CHATGPT_BUSY_ERROR}",
                "error": CHATGPT_BUSY_ERROR,
            })
        return {"job_id": job_id}

    def _resume():
        profile_reserved = True

        def update(message: str) -> None:
            with _jobs_lock:
                _jobs[job_id]["progress"] = message

        try:
            combined_text = (
                f"TIÊU ĐỀ KỊCH BẢN: {video['title']}\n\n"
                f"NỘI DUNG:\n{video['transcript']}"
            )
            update("🤖 Đang tiếp tục đúng phiên ChatGPT cũ...")
            worker_result = process_prompt_via_chatgpt(
                combined_text,
                prompt_version,
                video_id,
                pipeline=pipeline,
            )
            _finish_chatgpt_operation()
            profile_reserved = False

            summary_text = sanitize_generated_script(worker_result["script"])
            chat_url = worker_result.get("chat_url", "")
            generation_warning = worker_result.get("warning", "")
            complete_for_audio = worker_result.get("complete_for_audio", True)
            if not db.update_video_generation(video_id, summary_text, chat_url):
                raise RuntimeError("Không thể cập nhật video sau khi tiếp tục.")
            if complete_for_audio:
                clear_checkpoint(video_id)

            update(
                "🔎 Đang tự động kiểm tra kịch bản trước bước audio..."
                if pipeline["audio"]
                else "✅ Đã hoàn thành các bước tự động của pipeline."
            )
            audio_task = None
            audio_error = ""
            audio_review = None
            if complete_for_audio and pipeline["audio"]:
                audio_review, audio_task, audio_error = _auto_review_and_create_audio(
                    video_id,
                    requested_voice_id=video.get("voice_id", ""),
                    requested_voice_name=video.get("voice_name", ""),
                )
            elif not complete_for_audio:
                audio_review = _prepare_audio_review(video_id)

            with _jobs_lock:
                _jobs[job_id].update({
                    "status": "done",
                    "progress": (
                        (
                            "⚠️ Phần đọc đã tự động duyệt và gửi audio; "
                            "còn bước ChatGPT cần tiếp tục."
                            if audio_task and not audio_error
                            else "⚠️ Đã lưu checkpoint mới; còn bước cần tiếp tục."
                        )
                        if generation_warning
                        else (
                            "✅ Kịch bản đã hoàn thành; pipeline không tự tạo audio."
                            if not pipeline["audio"]
                            else (
                                "⛔ Kịch bản không đạt kiểm tra tự động; chưa tạo audio."
                                if audio_review and audio_review.get("status") == "blocked"
                                else (
                                    "⚠️ Kịch bản đã tự động duyệt; audio chưa thể khởi tạo."
                                    if audio_error
                                    else "🎙️ Kịch bản đã tự động duyệt; Genmax đang tạo audio."
                                )
                            )
                        )
                    ),
                    "result": {
                        "success": True,
                        "full_transcript": video["transcript"],
                        "summary": summary_text,
                        "title": video["title"],
                        "prompt_version": prompt_version,
                        "voice_id": video.get("voice_id", ""),
                        "voice_name": video.get("voice_name", ""),
                        "pipeline": pipeline,
                        "chat_url": chat_url,
                        "video_id": video_id,
                        "audio_task": (
                            _audio_task_response(audio_task) if audio_task else None
                        ),
                        "audio_error": audio_error or None,
                        "audio_review": (
                            _audio_review_response(audio_review)
                            if audio_review
                            else None
                        ),
                        "generation_warning": generation_warning,
                        "complete_for_audio": complete_for_audio,
                    },
                })
        except Exception as exc:
            error_message = str(exc) or repr(exc)
            with _jobs_lock:
                _jobs[job_id].update({
                    "status": "error",
                    "progress": f"❌ Lỗi tiếp tục video: {error_message[:100]}",
                    "error": error_message,
                })
        finally:
            if profile_reserved:
                _finish_chatgpt_operation()

    thread = threading.Thread(target=_resume, daemon=True)
    try:
        thread.start()
    except Exception:
        _finish_chatgpt_operation()
        raise
    return {"job_id": job_id}


def _system_job_center_item(job: dict, queue_position: int | None) -> dict:
    payload = job.get("payload") or {}
    return {
        "id": job["id"],
        "raw_id": job["id"],
        "type": job["job_type"],
        "type_label": "Tạo video",
        "status": job["status"],
        "title": (
            job.get("generated_title")
            or job.get("original_title")
            or job.get("title")
            or payload.get("url", "")
        ),
        "original_title": job.get("original_title") or job.get("title", ""),
        "generated_title": job.get("generated_title", ""),
        "video_url": job.get("video_url") or payload.get("url", ""),
        "progress": job.get("progress", ""),
        "error": job.get("error", ""),
        "video_id": job.get("video_id"),
        "prompt_version": job.get("prompt_version", ""),
        "voice_id": job.get("voice_id", ""),
        "pipeline": chatgpt_projects.normalize_prompt_pipeline(
            payload.get("pipeline")
        ),
        "queue_position": queue_position,
        "attempt": job.get("attempt", 0),
        "recovery_count": job.get("recovery_count", 0),
        "recovery_limit": len(VIDEO_RECOVERY_DELAYS_SECONDS),
        "resume_from_step": job.get("resume_from_step", ""),
        "next_retry_at": job.get("next_retry_at", ""),
        "created_at": job.get("created_at", ""),
        "updated_at": job.get("updated_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
        "can_cancel": job["status"] in {"queued", "running", "retry_wait", "paused"},
        "can_retry": job["status"] in {"error", "canceled"},
        "can_pause": job["status"] in {"queued", "retry_wait"},
        "can_resume": job["status"] == "paused",
    }


def _audio_job_center_item(task: dict) -> dict:
    status_map = {
        "pending": "queued",
        "processing": "running",
        "completed": "done",
        "failed": "error",
        "interrupted": "error",
    }
    status = status_map.get(task.get("status"), task.get("status", "error"))
    progress_map = {
        "pending": "Đang chờ Genmax",
        "processing": "Genmax đang tạo audio",
        "completed": "Audio hoàn thành",
        "failed": "Tạo audio thất bại",
        "interrupted": "Audio bị gián đoạn",
    }
    return {
        "id": f"audio:{task['video_id']}",
        "raw_id": str(task["video_id"]),
        "type": "audio",
        "type_label": "Tạo audio",
        "status": status,
        "title": task.get("title") or f"Video #{task['video_id']}",
        "original_title": task.get("original_title", ""),
        "generated_title": task.get("generated_title", ""),
        "video_url": task.get("video_url", ""),
        "progress": progress_map.get(task.get("status"), task.get("status", "")),
        "error": task.get("error", ""),
        "video_id": task["video_id"],
        "voice_id": task.get("voice_id", ""),
        "created_at": task.get("created_at", ""),
        "updated_at": task.get("updated_at", ""),
        "can_cancel": False,
        "can_retry": False,
    }


def _audio_review_job_center_item(review: dict) -> dict:
    status_map = {
        "pending": "awaiting_review",
        "blocked": "review_blocked",
        "approved": "done",
    }
    progress_map = {
        "pending": "Bản ghi cũ chưa chạy bước tự động kiểm tra audio",
        "blocked": "Kịch bản không đạt kiểm tra tự động; chưa gửi Genmax",
        "approved": "Kịch bản đã được hệ thống tự động duyệt",
    }
    status = review.get("status", "blocked")
    return {
        "id": f"audio-review:{review['video_id']}",
        "raw_id": str(review["video_id"]),
        "type": "audio_review",
        "type_label": "Kiểm duyệt audio",
        "status": status_map.get(status, "review_blocked"),
        "title": review.get("title") or f"Video #{review['video_id']}",
        "original_title": review.get("original_title", ""),
        "generated_title": review.get("generated_title", ""),
        "video_url": review.get("video_url", ""),
        "progress": progress_map.get(status, status),
        "error": (
            "; ".join(
                item.get("message", "")
                for item in (review.get("report") or {}).get("errors", [])
                if item.get("message")
            )
            if status == "blocked"
            else ""
        ),
        "video_id": review["video_id"],
        "created_at": review.get("updated_at", ""),
        "updated_at": review.get("updated_at", ""),
        "can_cancel": False,
        "can_retry": False,
    }


def _download_job_center_item(job: dict) -> dict:
    status_map = {
        "completed": "done",
        "completed_with_errors": "error",
        "stopped": "canceled",
        "stopping": "running",
    }
    status = status_map.get(job.get("status"), job.get("status", "running"))
    download_videos = [
        {
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
        }
        for item in job.get("items", [])
    ]
    return {
        "id": f"download:{job['id']}",
        "raw_id": job["id"],
        "type": "youtube_download",
        "type_label": "Tải YouTube",
        "status": status,
        "title": f"Tải {job.get('total', 0)} video",
        "download_videos": download_videos,
        "progress": (
            f"{job.get('progress', 0)}% · "
            f"{job.get('completed', 0)}/{job.get('total', 0)} hoàn thành"
        ),
        "error": (
            f"{job.get('failed', 0)} video tải lỗi"
            if job.get("failed")
            else ""
        ),
        "created_at": job.get("created_at", ""),
        "updated_at": job.get("created_at", ""),
        "can_cancel": job.get("status") in {"running", "paused"},
        "can_retry": False,
        "can_pause": job.get("status") == "running",
        "can_resume": job.get("status") == "paused",
    }


def _job_matches_video_search(item: dict, query: str) -> bool:
    normalized_query = db.normalize_search_text(query)
    if not normalized_query:
        return True

    values = [
        item.get("title", ""),
        item.get("original_title", ""),
        item.get("generated_title", ""),
        item.get("video_url", ""),
        item.get("video_id", ""),
        item.get("id", ""),
        item.get("raw_id", ""),
    ]
    for video in item.get("download_videos", []):
        values.extend((video.get("title", ""), video.get("url", ""), video.get("id", "")))
    searchable = db.normalize_search_text(" ".join(str(value or "") for value in values))
    return normalized_query in searchable


@app.get("/api/jobs")
def list_jobs(
    limit: int = 100,
    job_type: Optional[str] = None,
    search: Optional[str] = None,
):
    requested_limit = max(1, min(limit, 500))
    normalized_search = db.normalize_search_text(search or "")[:300]
    source_limit = 500 if normalized_search else requested_limit
    items: list[dict] = []

    if job_type in {None, "video_generation"}:
        system_jobs = db.list_system_jobs(
            limit=source_limit,
            job_type="video_generation",
        )
        queued_jobs = sorted(
            (job for job in system_jobs if job["status"] == "queued"),
            key=lambda job: job["created_at"],
        )
        queue_positions = {
            job["id"]: index for index, job in enumerate(queued_jobs, start=1)
        }
        items.extend(
            _system_job_center_item(job, queue_positions.get(job["id"]))
            for job in system_jobs
        )

    if job_type in {None, "audio"}:
        items.extend(
            _audio_job_center_item(task)
            for task in db.list_audio_tasks(limit=source_limit)
        )

    if job_type in {None, "audio_review"}:
        items.extend(
            _audio_review_job_center_item(review)
            for review in db.list_audio_reviews(limit=source_limit)
        )

    if job_type in {None, "youtube_download"}:
        items.extend(
            _download_job_center_item(job)
            for job in download_jobs.list_jobs(limit=source_limit)
        )

    operation, prompt_version = _get_chatgpt_state()
    if operation and operation != "video" and job_type in {None, "chatgpt"}:
        items.append({
            "id": "chatgpt:current",
            "raw_id": "current",
            "type": "chatgpt",
            "type_label": "Tác vụ ChatGPT",
            "status": "running",
            "title": operation,
            "progress": "Đang sử dụng phiên ChatGPT",
            "error": "",
            "prompt_version": prompt_version,
            "created_at": "",
            "updated_at": "",
            "can_cancel": False,
            "can_retry": False,
        })

    if normalized_search:
        items = [item for item in items if _job_matches_video_search(item, normalized_search)]

    items.sort(
        key=lambda item: (
            item["status"] in {"queued", "running", "retry_wait", "paused"},
            item.get("updated_at") or item.get("created_at") or "",
        ),
        reverse=True,
    )
    items = items[:requested_limit]
    for item in items:
        item.pop("download_videos", None)
    return {
        "items": items,
        "counts": {
            "total": len(items),
            "active": sum(
                item["status"] in {"queued", "running", "retry_wait", "paused"}
                for item in items
            ),
            "error": sum(
                item["status"] in {"error", "review_blocked"} for item in items
            ),
        },
    }


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    job = db.get_system_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    updated_job = db.request_cancel_system_job(job_id)
    _sync_legacy_job(updated_job)
    _kick_video_queue()
    return {
        "success": True,
        "job": _system_job_center_item(
            updated_job,
            db.get_system_job_queue_position(job_id),
        ),
    }


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    try:
        updated_job = db.retry_system_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated_job:
        raise HTTPException(status_code=404, detail="Job not found")
    _sync_legacy_job(updated_job)
    _kick_video_queue()
    return {
        "success": True,
        "job": _system_job_center_item(
            updated_job,
            db.get_system_job_queue_position(job_id),
        ),
    }


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    try:
        updated_job = db.pause_system_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated_job:
        raise HTTPException(status_code=404, detail="Job not found")
    _sync_legacy_job(updated_job)
    _kick_video_queue()
    return {"success": True}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    try:
        updated_job = db.resume_system_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated_job:
        raise HTTPException(status_code=404, detail="Job not found")
    _sync_legacy_job(updated_job)
    _kick_video_queue()
    return {"success": True}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    persistent_job = db.get_system_job(job_id)
    if persistent_job:
        _sync_legacy_job(persistent_job)
        with _jobs_lock:
            return dict(_jobs[job_id])
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            return dict(job)
    raise HTTPException(status_code=404, detail="Job not found")


@app.get("/api/chatgpt-status")
def get_chatgpt_status():
    operation, prompt_version = _get_chatgpt_state()
    return {
        "busy": _chatgpt_profile_lock.locked(),
        "operation": operation,
        "prompt_version": prompt_version,
    }


@app.get("/api/videos")
def get_videos(
    limit: int = 10,
    offset: int = 0,
    is_published: Optional[int] = None,
    prompt_version: Optional[str] = None,
    search: Optional[str] = None,
):
    res = db.get_all_videos(
        limit=limit,
        offset=offset,
        is_published=is_published,
        prompt_version=prompt_version,
        search_query=search,
    )
    for v in res.get("items", []):
        v["has_checkpoint"] = bool(load_checkpoint(v["id"]))
    return res

class GenerateThumbnailsRequest(BaseModel):
    script: str
    video_id: int = None  # optional - if given, updates db record
    thumbnail_type: Literal["with_text", "without_text", "both"]


class GenerateChaptersRequest(BaseModel):
    video_id: int


class GenerateMetadataRequest(BaseModel):
    video_id: int


def replace_metadata_section(
    script: str,
    metadata: str,
) -> str:
    metadata_match = re.search(
        r"### \[METADATA & QUIZ\]\n(.*?)(?=\n### \[|\Z)",
        script,
        flags=re.DOTALL,
    )
    if not metadata_match:
        raise RuntimeError("Video script does not contain METADATA & QUIZ.")

    return (
        script[:metadata_match.start(1)]
        + metadata.strip()
        + "\n"
        + script[metadata_match.end(1):]
    )


@app.post("/api/generate-metadata")
async def generate_metadata_endpoint(req: GenerateMetadataRequest):
    from auto_yt.services.chatgpt_worker import generate_metadata_only

    video = db.get_video(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not _try_start_chatgpt_operation(
        "metadata",
        video.get("prompt_version", ""),
    ):
        return {"success": False, "error": CHATGPT_BUSY_ERROR}

    try:
        loop = asyncio.get_event_loop()
        metadata = await loop.run_in_executor(
            None,
            lambda: generate_metadata_only(
                video.get("chat_url", ""),
                video.get("prompt_version", ""),
            ),
        )
        latest_video = db.get_video(req.video_id)
        if not latest_video:
            raise RuntimeError("Video not found")
        updated_script = replace_metadata_section(
            latest_video["generated_script"],
            metadata,
        )
        if not db.update_script(req.video_id, updated_script):
            raise RuntimeError("Video not found")
        return {
            "success": True,
            "video_id": req.video_id,
            "metadata": metadata,
            "script": updated_script,
        }
    except Exception as exc:
        print(f"Error in generate_metadata_endpoint: {exc}", file=sys.stderr)
        return {"success": False, "error": str(exc)}
    finally:
        _finish_chatgpt_operation()


@app.post("/api/generate-chapters")
async def generate_chapters_endpoint(req: GenerateChaptersRequest):
    from auto_yt.services.chatgpt_worker import generate_chapters_only

    video = db.get_video(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not _try_start_chatgpt_operation(
        "chapters",
        video.get("prompt_version", ""),
    ):
        return {"success": False, "error": CHATGPT_BUSY_ERROR}

    loop = asyncio.get_event_loop()
    try:
        existing_chapters_match = re.search(
            r"### \[CHAPTERS\]\n(.*?)(?=\n### \[|\Z)",
            video["generated_script"],
            flags=re.DOTALL,
        )
        reuse_existing_response = not (
            existing_chapters_match
            and existing_chapters_match.group(1).strip()
        )
        chapters = await loop.run_in_executor(
            None,
            lambda: generate_chapters_only(
                video["generated_script"],
                video.get("chat_url", ""),
                video.get("prompt_version", ""),
                reuse_existing_response,
            ),
        )
    finally:
        _finish_chatgpt_operation()

    latest_video = db.get_video(req.video_id)
    if not latest_video:
        raise HTTPException(status_code=404, detail="Video not found")
    script = latest_video["generated_script"]
    chapter_section = f"### [CHAPTERS]\n{chapters}"
    if "### [CHAPTERS]" in script:
        updated_script = re.sub(
            r"### \[CHAPTERS\]\n.*?(?=\n### \[|\Z)",
            chapter_section,
            script,
            flags=re.DOTALL,
        )
    else:
        updated_script = f"{script.rstrip()}\n\n{chapter_section}"

    if not db.update_script(req.video_id, updated_script):
        raise HTTPException(status_code=404, detail="Video not found")

    return {
        "success": True,
        "video_id": req.video_id,
        "chapters": chapters,
        "script": updated_script,
    }


@app.post("/api/generate-thumbnails")
async def generate_thumbnails_endpoint(req: GenerateThumbnailsRequest, background_tasks: BackgroundTasks):
    from auto_yt.services.chatgpt_worker import generate_thumbnails_only
    import concurrent.futures

    # Resolve the prompt version before reserving the ChatGPT profile so
    # Settings can lock only the version used by this thumbnail job.
    resolved_chat_url = ""
    resolved_prompt_version = ""
    if req.video_id:
        video = db.get_video(req.video_id)
        if video:
            resolved_chat_url = video.get("chat_url", "")
            resolved_prompt_version = video.get("prompt_version", "")
            print(f"Using chat_url from DB: {resolved_chat_url}")
    if not resolved_prompt_version:
        with _prompts_config_lock:
            resolved_prompt_version = _read_prompts_config().get(
                "active_version",
                "default",
            )

    if not _try_start_chatgpt_operation(
        "thumbnails",
        resolved_prompt_version,
    ):
        return {"success": False, "error": CHATGPT_BUSY_ERROR}

    try:
        loop = asyncio.get_event_loop()
        def generate_requested_thumbnails():
            requested_types = (
                ("with_text", "without_text")
                if req.thumbnail_type == "both"
                else (req.thumbnail_type,)
            )
            combined_result = {
                "thumb_text": None,
                "thumb_notext": None,
                "image1_url": "",
                "image2_url": "",
                "image1_urls": [],
                "image2_urls": [],
            }
            for thumbnail_type in requested_types:
                partial_result = generate_thumbnails_only(
                    req.script,
                    resolved_chat_url,
                    resolved_prompt_version,
                    thumbnail_type,
                )
                expected_image_key = (
                    "image1_url"
                    if thumbnail_type == "with_text"
                    else "image2_url"
                )
                expected_images_key = (
                    "image1_urls"
                    if thumbnail_type == "with_text"
                    else "image2_urls"
                )
                image_urls = _normalize_thumbnail_urls(
                    partial_result.get(expected_images_key),
                    partial_result.get(expected_image_key, ""),
                )
                if not image_urls:
                    thumbnail_label = (
                        "có chữ"
                        if thumbnail_type == "with_text"
                        else "không chữ"
                    )
                    raise RuntimeError(
                        "ChatGPT không trả về ảnh thumbnail mới "
                        f"{thumbnail_label}. Ảnh cũ được giữ nguyên."
                    )
                partial_result[expected_images_key] = image_urls
                partial_result[expected_image_key] = image_urls[0]
                for key, value in partial_result.items():
                    if value:
                        combined_result[key] = value
            return combined_result

        result = await loop.run_in_executor(None, generate_requested_thumbnails)
        
        # If video_id provided, patch the stored script to add image URLs
        if req.video_id:
            video = db.get_video(req.video_id)
            if video:
                script = video["generated_script"]
                import re
                
                thumbnail_types = (
                    ("with_text", "without_text")
                    if req.thumbnail_type == "both"
                    else (req.thumbnail_type,)
                )
                thumbnail_configs = {
                    "with_text": (
                        "THUMBNAIL CÓ CHỮ",
                        "thumb_text",
                        "image1_url",
                        "image1_urls",
                    ),
                    "without_text": (
                        "THUMBNAIL KHÔNG CHỮ",
                        "thumb_notext",
                        "image2_url",
                        "image2_urls",
                    ),
                }

                for thumbnail_type in thumbnail_types:
                    section_title, text_key, image_key, images_key = thumbnail_configs[
                        thumbnail_type
                    ]
                    generated_text = result.get(text_key)
                    image_urls = _normalize_thumbnail_urls(
                        result.get(images_key),
                        result.get(image_key, ""),
                    )
                    section_pattern = rf'### \[{re.escape(section_title)}\]\n(.*?)(?=\n### \[|\Z)'
                    section_match = re.search(section_pattern, script, re.DOTALL)
                    current_text = section_match.group(1).strip() if section_match else ""
                    updated_text = generated_text or current_text

                    if image_urls:
                        updated_text = re.sub(r'\[IMAGE_URL:.*?\]', '', updated_text).strip()
                        image_markers = "\n\n".join(
                            f"[IMAGE_URL:{image_url}]" for image_url in image_urls
                        )
                        updated_text += f"\n\n{image_markers}"

                    if section_match:
                        script = re.sub(
                            rf'(### \[{re.escape(section_title)}\]\n).*?(?=\n### \[|\Z)',
                            rf'\1{updated_text.strip()}\n',
                            script,
                            flags=re.DOTALL,
                        )
                        
                db.update_script(req.video_id, script)
        
        return {"success": True, **result}
    except Exception as e:
        print(f"Error in generate_thumbnails_endpoint: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}
    finally:
        _finish_chatgpt_operation()


@app.get("/api/videos/{video_id}/audio-review")
def get_audio_review(video_id: int):
    if not db.get_video(video_id):
        raise HTTPException(status_code=404, detail="Video not found")
    review = _prepare_audio_review(video_id)
    return {"success": True, "audio_review": _audio_review_response(review)}


@app.post("/api/videos/{video_id}/audio-review/approve")
def approve_audio_review(
    video_id: int,
    request: ApproveAudioReviewRequest,
):
    if not request.confirm_credit_charge:
        raise HTTPException(
            status_code=400,
            detail="Phải xác nhận Genmax có thể trừ credit khi tạo audio.",
        )
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    review = _prepare_audio_review(video_id)
    report = review.get("report") or {}
    if not report.get("can_approve"):
        return {
            "success": False,
            "requires_review": True,
            "error": "Kịch bản còn lỗi bắt buộc; chưa thể gửi sang Genmax.",
            "audio_review": _audio_review_response(review),
        }

    selected_voice = None
    if request.voice_id:
        try:
            selected_voice = voice_config.get_voice(request.voice_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    approved_review = db.upsert_audio_review(
        video_id=video_id,
        script_hash=review["script_hash"],
        status="approved",
        report=report,
        reviewed_at=db.utc_now(),
    )
    try:
        task = _ensure_audio_task(
            video_id,
            requested_voice_id=(selected_voice or {}).get("id", ""),
            requested_voice_name=(selected_voice or {}).get("name", ""),
        )
    except Exception as exc:
        current_task = db.get_audio_task(video_id)
        return {
            "success": False,
            "error": str(exc),
            "audio_review": _audio_review_response(approved_review),
            "audio_task": (
                _audio_task_response(current_task) if current_task else None
            ),
        }
    return {
        "success": task.get("status") != "failed",
        "audio_review": _audio_review_response(approved_review),
        "audio_task": _audio_task_response(task),
    }


@app.post("/api/videos/{video_id}/generate-audio")
def generate_audio_for_video(video_id: int):
    """Automatically audit, approve and safely start the Genmax workflow."""
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    script = video["generated_script"]
    if "### [AUDIO]" in script:
        review = _automatically_approve_audio_review(video_id)
        stored_task = db.get_audio_task(video_id)
        if stored_task and stored_task["status"] != "failed":
            return {
                "success": True,
                "audio_review": _audio_review_response(review),
                "audio_task": _audio_task_response(stored_task),
            }
        if not stored_task:
            return {
                "success": True,
                "audio_review": _audio_review_response(review),
                "audio_task": {
                    "video_id": video_id,
                    "status": "completed",
                    "audio_url": script.split("### [AUDIO]", 1)[1].strip(),
                    "error": "",
                    "voice_id": video.get("voice_id", ""),
                    "voice_name": video.get("voice_name", ""),
                },
            }

    review, task, audio_error = _auto_review_and_create_audio(video_id)
    if review.get("status") != "approved":
        return {
            "success": False,
            "quality_blocked": True,
            "error": audio_error,
            "audio_review": _audio_review_response(review),
            "audio_task": None,
        }
    if audio_error:
        return {
            "success": False,
            "error": audio_error,
            "audio_review": _audio_review_response(review),
            "audio_task": _audio_task_response(task) if task else None,
        }

    if task["status"] == "failed":
        return {
            "success": False,
            "error": (
                "Task Genmax đã thất bại. Hệ thống không tự retry để tránh "
                "trừ credit lần nữa."
            ),
            "audio_review": _audio_review_response(review),
            "audio_task": _audio_task_response(task),
        }
    return {
        "success": True,
        "audio_review": _audio_review_response(review),
        "audio_task": _audio_task_response(task),
    }


@app.get("/api/videos/{video_id}/audio-status")
def get_audio_status(video_id: int):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    task = db.get_audio_task(video_id)
    if task:
        if task["status"] in {"pending", "processing"}:
            try:
                # Status polling is also a recovery path. This updates local
                # state immediately when Genmax has finished, even if a
                # background watcher previously hit a transient API error.
                task = _sync_audio_task(video_id)
            except Exception as exc:
                print(
                    f"On-demand audio sync failed for video {video_id}: {exc}",
                    file=sys.stderr,
                )
            if task["status"] in {"pending", "processing"}:
                _start_audio_watcher(video_id)
        elif task["status"] == "completed" and task.get("audio_url"):
            # The persistent audio task is the source of truth. A long-running
            # chapter or metadata update from an older app version may have
            # overwritten the script after audio completed. Restore the marker
            # so every saved video becomes playable again without new credits.
            _save_audio_url(
                video_id,
                task["audio_url"],
                task.get("voice_id", ""),
                task.get("voice_name", ""),
            )
        return {"success": True, "audio_task": _audio_task_response(task)}

    script = video["generated_script"]
    if "### [AUDIO]" in script:
        return {
            "success": True,
            "audio_task": {
                "video_id": video_id,
                "status": "completed",
                "audio_url": script.split("### [AUDIO]", 1)[1].strip(),
                "error": "",
                "voice_id": video.get("voice_id", ""),
                "voice_name": video.get("voice_name", ""),
            },
        }
    return {
        "success": True,
        "audio_task": {
            "video_id": video_id,
            "status": "not_started",
            "audio_url": "",
            "error": "",
            "voice_id": video.get("voice_id", ""),
            "voice_name": video.get("voice_name", ""),
        },
    }


@app.post("/api/videos/{video_id}/regenerate-audio")
def regenerate_audio_for_video(
    video_id: int,
    request: RegenerateAudioRequest,
):
    if not request.confirm_credit_charge:
        raise HTTPException(
            status_code=400,
            detail="Phải xác nhận Genmax sẽ trừ credit cho toàn bộ audio mới.",
        )

    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    review = _automatically_approve_audio_review(video_id)
    if review.get("status") != "approved":
        raise HTTPException(
            status_code=409,
            detail="Kịch bản không đạt kiểm tra tự động; chưa tạo lại audio.",
        )
    try:
        selected_voice = voice_config.get_voice(request.voice_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    filtered_script = apply_tts_filters(
        get_clean_script_for_tts(video["generated_script"])
    )
    if not filtered_script:
        raise HTTPException(
            status_code=400,
            detail="Không tìm thấy kịch bản để tạo lại audio.",
        )
    requested_hash = tts.get_generation_request_hash(
        filtered_script,
        selected_voice["id"],
    )
    stored_task = db.get_audio_task(video_id)
    if stored_task and stored_task["status"] in {"pending", "processing"}:
        raise HTTPException(
            status_code=409,
            detail="Video đang có audio job chạy. Hãy đợi job hoàn tất.",
        )
    if stored_task and stored_task["request_hash"] == requested_hash:
        if stored_task["status"] == "completed":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Kịch bản và giọng đọc không thay đổi; audio hiện tại đã đúng "
                    "để tránh tạo trùng và tốn credit."
                ),
            )
        if stored_task["status"] == "failed":
            raise HTTPException(
                status_code=409,
                detail="Audio cùng giọng đã lỗi. Hãy dùng Retry Audio.",
            )
        if stored_task["status"] == AUDIO_INTERRUPTED_STATUS:
            raise HTTPException(
                status_code=409,
                detail="Audio cùng giọng đang dang dở. Hãy dùng Tiếp tục Audio.",
            )

    try:
        task = _ensure_audio_task(
            video_id,
            requested_voice_id=selected_voice["id"],
            requested_voice_name=selected_voice["name"],
        )
    except Exception as exc:
        current_task = db.get_audio_task(video_id)
        return {
            "success": False,
            "error": str(exc),
            "audio_task": (
                _audio_task_response(current_task) if current_task else None
            ),
        }

    return {
        "success": task["status"] != "failed",
        "audio_task": _audio_task_response(task),
        "preserved_previous_audio": "### [AUDIO]" in video["generated_script"],
    }


@app.post("/api/videos/{video_id}/retry-audio")
def retry_audio_for_video(video_id: int, request: RetryAudioRequest):
    if not request.confirm_credit_charge:
        raise HTTPException(
            status_code=400,
            detail="Phải xác nhận Genmax sẽ trừ credit lần nữa.",
        )

    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    review = _automatically_approve_audio_review(video_id)
    if review.get("status") != "approved":
        raise HTTPException(
            status_code=409,
            detail="Kịch bản không đạt kiểm tra tự động; không thể retry audio.",
        )

    with _audio_submit_lock:
        task = db.get_audio_task(video_id)
        if not task:
            raise HTTPException(status_code=404, detail="Audio task not found")
        if task["status"] != "failed":
            raise HTTPException(
                status_code=409,
                detail="Chỉ được retry task đã thất bại.",
            )

        current_script = apply_tts_filters(
            get_clean_script_for_tts(video["generated_script"])
        )
        current_request_hash = tts.get_generation_request_hash(
            current_script,
            task.get("voice_id") or AUDIO_VOICE_ID,
        )
        if current_request_hash != task.get("request_hash"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Kịch bản hoặc giọng đọc đã đổi; không thể retry task cũ. "
                    "Hãy duyệt kịch bản và tạo audio mới."
                ),
            )

        segments = _get_audio_segments(task)
        if segments:
            failed_segments = [
                segment
                for segment in segments
                if segment.get("status") == "failed"
            ]
            if not failed_segments:
                raise HTTPException(
                    status_code=409,
                    detail="Không tìm thấy đoạn audio Genmax đã thất bại để retry.",
                )
            for segment in failed_segments:
                text_hash = segment.get("text_hash", "")
                characters = segment.get("characters", 0)
                retried_task = tts.retry_tts_task(segment["task_id"])
                segment.update(
                    _segment_from_remote(
                        segment["index"],
                        "",
                        retried_task,
                        task.get("voice_id") or AUDIO_VOICE_ID,
                    )
                )
                segment["text_hash"] = text_hash
                segment["characters"] = characters
            updated_task = db.upsert_audio_task(
                video_id=video_id,
                request_hash=task["request_hash"],
                task_id=task["task_id"],
                status="processing",
                segments_json=json.dumps(segments, ensure_ascii=False),
                voice_id=task.get("voice_id", ""),
                voice_name=task.get("voice_name", ""),
            )
            _start_audio_watcher(video_id)
            return {
                "success": True,
                "audio_task": _audio_task_response(updated_task),
            }

        retried_task = tts.retry_tts_task(task["task_id"])
        updated_task = db.upsert_audio_task(
            video_id=video_id,
            request_hash=task["request_hash"],
            task_id=retried_task["id"],
            status=retried_task.get("status", "pending"),
            voice_id=task.get("voice_id", ""),
            voice_name=task.get("voice_name", ""),
        )
        _start_audio_watcher(video_id)
        return {
            "success": True,
            "audio_task": _audio_task_response(updated_task),
        }


@app.put("/api/videos/{video_id}/publish")
def publish_video(video_id: int, is_published: int):
    success = db.toggle_published(video_id, is_published)
    if not success:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"success": True}


@app.put("/api/videos/{video_id}/audio-duration")
def save_audio_duration(video_id: int, request: AudioDurationRequest):
    if not math.isfinite(request.duration_seconds) or request.duration_seconds <= 0:
        raise HTTPException(status_code=400, detail="Audio duration must be positive")
    success = db.update_audio_duration(video_id, request.duration_seconds)
    if not success:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"success": True}

@app.get("/api/videos/{video_id}")
def get_video(video_id: int):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    video["has_checkpoint"] = bool(load_checkpoint(video_id))
    return video

@app.delete("/api/videos/{video_id}")
def delete_video(video_id: int):
    success = db.delete_video(video_id)
    if not success:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"success": True}

# --- AUTO LOGIN ENDPOINTS ---

@app.get("/api/account", response_model=AccountData)
def get_account():
    if not ACCOUNT_PATH.exists():
        return AccountData()
    try:
        data = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
        account = _extract_gpt_account(data)
        return AccountData(
            email=account.get("email", ""),
            password=account.get("password", ""),
            totp_secret=account.get("totp_secret") or "",
            headless=account.get("headless", True)
        )
    except Exception:
        return AccountData()

@app.post("/api/account")
def save_account(account: AccountData):
    _save_account_payload(account.dict())
    return {"success": True}

@app.post("/api/clear-account")
def clear_account():
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    # Xóa thư mục Profile
    if profile_dir.exists():
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass
    # Xóa file Account
    if ACCOUNT_PATH.exists():
        try:
            ACCOUNT_PATH.unlink()
        except Exception:
            pass
    return {"success": True}

@app.get("/api/prompts")
def get_prompts():
    with _prompts_config_lock:
        if not PROMPTS_PATH.exists():
            # Initialize with default if missing
            default_data = chatgpt_projects.add_project_defaults(DEFAULT_PROMPTS_DATA)
            _write_prompts_config(default_data)
            return default_data
        return _read_prompts_config()


PROMPT_FIELD_KEYS = frozenset(
    DEFAULT_PROMPTS_DATA["versions"]["default"]["prompts"].keys()
)


def _read_prompts_config() -> dict:
    try:
        data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
        if (
            "active_version" not in data
            or not isinstance(data.get("versions"), dict)
            or not data["versions"]
        ):
            return chatgpt_projects.add_project_defaults(DEFAULT_PROMPTS_DATA)
        return chatgpt_projects.add_project_defaults(data)
    except (OSError, json.JSONDecodeError):
        return chatgpt_projects.add_project_defaults(DEFAULT_PROMPTS_DATA)


def _write_prompts_config(data: dict) -> dict:
    try:
        normalized_data = chatgpt_projects.validate_prompt_projects(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_PATH.write_text(
        json.dumps(normalized_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized_data


def _get_prompt_version(data: dict, version_id: str) -> dict:
    version = data.get("versions", {}).get(version_id)
    if not isinstance(version, dict):
        raise HTTPException(status_code=404, detail="Không tìm thấy bộ prompt.")
    return version


def _get_prompt_default_voice_id(version_id: str = "") -> str:
    with _prompts_config_lock:
        data = _read_prompts_config()
        resolved_version_id = version_id.strip() or data.get(
            "active_version",
            "default",
        )
        version = data.get("versions", {}).get(resolved_version_id, {})
        return str(version.get("default_voice_id", "") or "").strip()


def _get_prompt_pipeline(version_id: str = "") -> dict[str, bool]:
    with _prompts_config_lock:
        data = _read_prompts_config()
        resolved_version_id = version_id.strip() or data.get(
            "active_version",
            "default",
        )
        version = data.get("versions", {}).get(resolved_version_id, {})
        return chatgpt_projects.normalize_prompt_pipeline(version.get("pipeline"))


def _get_active_prompt_version_id() -> str:
    with _prompts_config_lock:
        data = _read_prompts_config()
        active_version = str(data.get("active_version", "default") or "").strip()
        return active_version or "default"


def _get_locked_prompt_version() -> str:
    _, prompt_version = _get_chatgpt_state()
    return prompt_version if _chatgpt_profile_lock.locked() else ""


def _assert_prompt_version_editable(version_id: str) -> None:
    if version_id and version_id == _get_locked_prompt_version():
        raise HTTPException(
            status_code=409,
            detail=(
                "Bộ prompt này đang được một job sử dụng. "
                "Hãy đợi job hoàn tất hoặc chọn bộ prompt khác để chỉnh sửa."
            ),
        )


@app.get("/api/voices")
def get_voices():
    return voice_config.load_voice_config()


@app.post("/api/voices")
def save_voices(data: VoiceConfigData):
    try:
        return voice_config.save_voice_config(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/api/prompts")
def save_prompts(data: PromptsData):
    with _prompts_config_lock:
        incoming_data = data.model_dump()
        locked_version = _get_locked_prompt_version()
        if locked_version:
            saved_data = _read_prompts_config()
            saved_locked_version = saved_data.get("versions", {}).get(
                locked_version
            )
            if saved_locked_version is not None:
                # Save additions, deletions and edits to every other version,
                # while preserving the exact configuration used by the job.
                incoming_data.setdefault("versions", {})[
                    locked_version
                ] = saved_locked_version
        return _write_prompts_config(incoming_data)


@app.patch("/api/prompts/{version_id}/name")
def save_prompt_version_name(version_id: str, payload: PromptVersionNameData):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tên bộ prompt không được để trống.")
    if len(name) > 100:
        raise HTTPException(
            status_code=400,
            detail="Tên bộ prompt không được dài quá 100 ký tự.",
        )
    with _prompts_config_lock:
        _assert_prompt_version_editable(version_id)
        data = _read_prompts_config()
        version = _get_prompt_version(data, version_id)
        version["name"] = name
        normalized_data = _write_prompts_config(data)
    return {
        "version_id": version_id,
        "version": normalized_data["versions"][version_id],
    }


@app.patch("/api/prompts/{version_id}/project")
def save_prompt_project(version_id: str, payload: PromptProjectData):
    with _prompts_config_lock:
        _assert_prompt_version_editable(version_id)
        data = _read_prompts_config()
        version = _get_prompt_version(data, version_id)
        try:
            version["project_url"] = chatgpt_projects.validate_project_url(
                payload.project_url
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        normalized_data = _write_prompts_config(data)
    return {
        "version_id": version_id,
        "version": normalized_data["versions"][version_id],
    }


@app.patch("/api/prompts/{version_id}/default-voice")
def save_prompt_default_voice(
    version_id: str,
    payload: PromptDefaultVoiceData,
):
    selected_voice_id = payload.voice_id.strip()
    if selected_voice_id:
        try:
            selected_voice_id = voice_config.get_voice(selected_voice_id)["id"]
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    with _prompts_config_lock:
        _assert_prompt_version_editable(version_id)
        data = _read_prompts_config()
        version = _get_prompt_version(data, version_id)
        version["default_voice_id"] = selected_voice_id
        normalized_data = _write_prompts_config(data)
    return {
        "version_id": version_id,
        "version": normalized_data["versions"][version_id],
    }


@app.patch("/api/prompts/{version_id}/pipeline")
def save_prompt_pipeline(version_id: str, payload: PromptPipelineData):
    with _prompts_config_lock:
        _assert_prompt_version_editable(version_id)
        data = _read_prompts_config()
        version = _get_prompt_version(data, version_id)
        version["pipeline"] = chatgpt_projects.validate_prompt_pipeline(
            payload.model_dump()
        )
        normalized_data = _write_prompts_config(data)
    return {
        "version_id": version_id,
        "pipeline": normalized_data["versions"][version_id]["pipeline"],
    }


@app.patch("/api/prompts/{version_id}/fields/{prompt_key}")
def save_prompt_field(
    version_id: str,
    prompt_key: str,
    payload: PromptFieldData,
):
    if prompt_key not in PROMPT_FIELD_KEYS:
        raise HTTPException(status_code=404, detail="Không tìm thấy menu prompt.")
    with _prompts_config_lock:
        _assert_prompt_version_editable(version_id)
        data = _read_prompts_config()
        version = _get_prompt_version(data, version_id)
        prompts = version.get("prompts")
        if not isinstance(prompts, dict):
            raise HTTPException(status_code=400, detail="Bộ prompt không hợp lệ.")
        prompts[prompt_key] = payload.value
        normalized_data = _write_prompts_config(data)
    return {
        "version_id": version_id,
        "prompt_key": prompt_key,
        "value": normalized_data["versions"][version_id]["prompts"][prompt_key],
    }

@app.post("/api/login-chatgpt")
async def trigger_login():
    from auto_yt.services.chatgpt_login import login_gpt_auto, restore_session
    from playwright.async_api import async_playwright

    profile_reserved = False
    try:
        if not ACCOUNT_PATH.exists():
            return {"success": False, "error": "Chưa có thông tin tài khoản"}

        if not _try_start_chatgpt_operation("login"):
            return {"success": False, "error": CHATGPT_BUSY_ERROR}
        profile_reserved = True

        data = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
        account = _extract_gpt_account(data)
        
        async def _run_login():
            async with async_playwright() as p:
                profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
                profile_dir.mkdir(parents=True, exist_ok=True)
                
                context = await p.chromium.launch_persistent_context(
                    str(profile_dir),
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    )
                )
                page = context.pages[0] if context.pages else await context.new_page()
                page.set_default_timeout(60000)
                
                saved_cookies = account.get("session_cookie") or []
                if saved_cookies:
                    try:
                        result = await restore_session(saved_cookies, page)
                    except Exception:
                        result = await login_gpt_auto(account, page)
                else:
                    result = await login_gpt_auto(account, page)
                
                # Save session
                account["session_cookie"] = result.get("cookies", [])
                
                existing = {}
                if ACCOUNT_PATH.exists():
                    try:
                        existing = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
                    except Exception: pass
                existing["gpt_account1"] = account
                ACCOUNT_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                
                return result
                
        result = await _run_login()
        return {"success": True, "message": "Login successful"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if profile_reserved:
            _finish_chatgpt_operation()

@app.post("/api/open-profile")
async def open_profile():
    from playwright.async_api import async_playwright
    import asyncio

    if not _try_start_chatgpt_operation("profile"):
        return {"success": False, "error": CHATGPT_BUSY_ERROR}

    async def _launch():
        try:
            async with async_playwright() as p:
                profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
                context = await p.chromium.launch_persistent_context(
                    str(profile_dir),
                    headless=False,
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                    ignore_default_args=["--enable-automation"]
                )
                try:
                    page = await context.new_page()
                    await page.goto("https://chatgpt.com/")
                    await page.wait_for_timeout(600000) # 10 mins
                except Exception:
                    pass
                finally:
                    await context.close()
        finally:
            _finish_chatgpt_operation()

    # Launch as a separate asyncio task so it doesn't block the API
    try:
        asyncio.create_task(_launch())
    except Exception:
        _finish_chatgpt_operation()
        raise
    return {"success": True, "message": "Browser profile opened on server."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True, loop="asyncio")
