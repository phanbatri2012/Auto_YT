from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
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
import auto_yt.services.database as db
import auto_yt.services.audio_utils as audio_utils
import auto_yt.services.tts_service as tts
from auto_yt.services import voice_config
from auto_yt.services import chatgpt_projects
import re

# In-memory job store: job_id -> {status, progress, result, error}
_jobs: dict = {}
_jobs_lock = threading.Lock()
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

class PromptVersion(BaseModel):
    name: str
    prompts: dict
    project_url: str = chatgpt_projects.DEFAULT_CHATGPT_PROJECT_URL
    default_voice_id: str = ""

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


class AudioDurationRequest(BaseModel):
    duration_seconds: float

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
    # Extract only INTRO, BODY, and OUTRO for reading
    script = ""
    try:
        # Match from ### [INTRO] to ### [METADATA
        match = re.search(r"### \[INTRO\](.*?)### \[METADATA", text, re.DOTALL)
        if match:
            script = match.group(1)
            # Remove inner ### tags
            script = re.sub(r"### \[[^\]]+\]", "", script)
            script = re.sub(r"\n\s*\n", "\n\n", script).strip()
    except Exception:
        pass
    return script


AUDIO_VOICE_ID = voice_config.DEFAULT_VOICE_ID
AUDIO_POLL_INTERVAL_SECONDS = 30
AUDIO_INTERRUPTED_STATUS = "interrupted"
_audio_submit_lock = threading.Lock()
_audio_watchers: dict[int, threading.Thread] = {}
_audio_watchers_lock = threading.Lock()
_audio_sync_locks: dict[int, threading.Lock] = {}
_audio_sync_locks_guard = threading.Lock()


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
def resume_audio_watchers() -> None:
    for task in db.get_active_audio_tasks():
        _start_audio_watcher(task["video_id"])

@app.post("/api/process-video")
def process_video(request: VideoRequest):
    resolved_prompt_version = request.prompt_version or _get_active_prompt_version_id()
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

    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "progress": "⏳ Đang khởi động...", "result": None, "error": None}

    if not _try_start_chatgpt_operation(
        "video",
        resolved_prompt_version,
    ):
        with _jobs_lock:
            _jobs[job_id].update({
                "status": "error",
                "progress": f"❌ Lỗi: {CHATGPT_BUSY_ERROR}",
                "error": CHATGPT_BUSY_ERROR,
            })
        return {"job_id": job_id}

    def _run():
        profile_reserved = True
        video_id = None

        def update(msg: str):
            with _jobs_lock:
                _jobs[job_id]["progress"] = msg

        try:
            update("📥 Đang tải phụ đề YouTube...")
            full_transcript = get_video_transcript(request.url)
            title = get_video_title(request.url)

            update("💾 Đang lưu bản nháp an toàn...")
            video_id = db.save_video(
                request.url,
                title,
                full_transcript,
                INITIAL_GENERATED_SCRIPT,
                "",
                resolved_prompt_version,
                selected_voice["id"],
                selected_voice["name"],
            )

            combined_text = f"TIÊU ĐỀ KỊCH BẢN: {title}\n\nNỘI DUNG:\n{full_transcript}"

            update("🤖 ChatGPT đang viết kịch bản (5-15 phút)...")
            worker_result = process_prompt_via_chatgpt(
                combined_text,
                resolved_prompt_version,
            )
            _finish_chatgpt_operation()
            profile_reserved = False
            summary_text = worker_result["script"] if isinstance(worker_result, dict) else worker_result
            chat_url = worker_result.get("chat_url", "") if isinstance(worker_result, dict) else ""
            generation_warning = (
                worker_result.get("warning", "")
                if isinstance(worker_result, dict)
                else ""
            )
            complete_for_audio = (
                worker_result.get("complete_for_audio", True)
                if isinstance(worker_result, dict)
                else True
            )

            update("💾 Đang lưu vào database...")
            if not db.update_video_generation(video_id, summary_text, chat_url):
                raise RuntimeError("Không thể cập nhật bản nháp video.")
            audio_task = None
            audio_error = None
            if complete_for_audio and get_clean_script_for_tts(summary_text):
                try:
                    update("🎙️ Đang kiểm tra và gửi yêu cầu audio an toàn...")
                    audio_task = _ensure_audio_task(video_id)
                    summary_text = db.get_video(video_id)["generated_script"]
                except Exception as exc:
                    audio_error = str(exc)

            with _jobs_lock:
                _jobs[job_id].update({
                    "status": "done",
                    "progress": (
                        "⚠️ Đã lưu phần hoàn tất; một bước ChatGPT cần tạo lại."
                        if generation_warning
                        else "✅ Hoàn thành!"
                    ),
                    "result": {
                        "success": True,
                        "full_transcript": full_transcript,
                        "summary": summary_text,
                        "title": title,
                        "prompt_version": resolved_prompt_version,
                        "voice_id": selected_voice["id"],
                        "voice_name": selected_voice["name"],
                        "chat_url": chat_url,
                        "video_id": video_id,
                        "audio_task": (
                            _audio_task_response(audio_task) if audio_task else None
                        ),
                        "audio_error": audio_error,
                        "generation_warning": generation_warning,
                        "complete_for_audio": complete_for_audio,
                    }
                })

        except Exception as e:
            error_msg = str(e)
            if not error_msg:
                error_msg = repr(e)
            if "Could not retrieve a transcript" in error_msg or "Subtitles are disabled" in error_msg:
                error_msg = "Video này không có phụ đề (Transcript). Vui lòng chọn video khác."
            if video_id is not None:
                error_msg = (
                    f"{error_msg} Bản nháp video #{video_id} đã được lưu ở Dashboard."
                )
            with _jobs_lock:
                _jobs[job_id].update({
                    "status": "error",
                    "progress": f"❌ Lỗi: {error_msg[:100]}",
                    "error": error_msg
                })
        finally:
            if profile_reserved:
                _finish_chatgpt_operation()

    thread = threading.Thread(target=_run, daemon=True)
    try:
        thread.start()
    except Exception:
        _finish_chatgpt_operation()
        raise
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


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
    return db.get_all_videos(
        limit=limit,
        offset=offset,
        is_published=is_published,
        prompt_version=prompt_version,
        search_query=search,
    )

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
        updated_script = replace_metadata_section(
            video["generated_script"],
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

    script = video["generated_script"]
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


@app.post("/api/videos/{video_id}/generate-audio")
def generate_audio_for_video(video_id: int):
    """Create or safely resume the persistent Genmax audio workflow."""
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    script = video["generated_script"]
    if "### [AUDIO]" in script:
        stored_task = db.get_audio_task(video_id)
        if stored_task and stored_task["status"] != "failed":
            return {
                "success": True,
                "audio_task": _audio_task_response(stored_task),
            }
        if not stored_task:
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

    try:
        task = _ensure_audio_task(video_id)
    except Exception as exc:
        stored_task = db.get_audio_task(video_id)
        return {
            "success": False,
            "error": str(exc),
            "audio_task": (
                _audio_task_response(stored_task) if stored_task else None
            ),
        }

    if task["status"] == "failed":
        return {
            "success": False,
            "error": (
                "Task Genmax đã thất bại. Hệ thống không tự retry để tránh "
                "trừ credit lần nữa."
            ),
            "audio_task": _audio_task_response(task),
        }
    return {"success": True, "audio_task": _audio_task_response(task)}


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

    with _audio_submit_lock:
        task = db.get_audio_task(video_id)
        if not task:
            raise HTTPException(status_code=404, detail="Audio task not found")
        if task["status"] != "failed":
            raise HTTPException(
                status_code=409,
                detail="Chỉ được retry task đã thất bại.",
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
