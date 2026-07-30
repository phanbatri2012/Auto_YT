from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Literal, Optional
import asyncio
import json
import threading
import time
import uuid
from auto_yt.paths import ACCOUNT_PATH, DATA_DIR, CHROME_USER_DATA_DIR, gpt_profile_dir, PROMPTS_PATH, THUMBNAILS_DIR
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
import auto_yt.services.tts_service as tts
import re

# In-memory job store: job_id -> {status, progress, result, error}
_jobs: dict = {}
_jobs_lock = threading.Lock()

class AccountData(BaseModel):
    email: str = ""
    password: str = ""
    totp_secret: str = ""
    headless: bool = True

class PromptVersion(BaseModel):
    name: str
    prompts: dict

class PromptsData(BaseModel):
    active_version: str
    versions: dict[str, PromptVersion]

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

class VideoRequest(BaseModel):
    url: str
    prompt_version: Optional[str] = None

class VideoResponse(BaseModel):
    success: bool
    full_transcript: str
    summary: str
    chat_url: str = ""
    error: str = None
    video_id: Optional[int] = None


class RetryAudioRequest(BaseModel):
    confirm_credit_charge: bool

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


AUDIO_VOICE_ID = "e1d9617c-045c-4072-8d17-9be0ec113723"
AUDIO_POLL_INTERVAL_SECONDS = 15
_audio_submit_lock = threading.Lock()
_audio_watchers: dict[int, threading.Thread] = {}
_audio_watchers_lock = threading.Lock()


def _audio_task_response(task: dict) -> dict:
    return {
        "video_id": task["video_id"],
        "task_id": task["task_id"],
        "status": task["status"],
        "audio_url": task.get("audio_url", ""),
        "error": task.get("error", ""),
        "updated_at": task["updated_at"],
    }


def _save_audio_url(video_id: int, audio_url: str) -> str:
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
    return updated_script


def _sync_audio_task(video_id: int) -> dict:
    stored_task = db.get_audio_task(video_id)
    if not stored_task:
        raise RuntimeError("Không tìm thấy audio task.")

    remote_task = tts.get_tts_task(stored_task["task_id"])
    status = remote_task.get("status", stored_task["status"])
    audio_url = (remote_task.get("result") or {}).get("audio_url", "")
    error = remote_task.get("error") or remote_task.get("detail_error") or ""

    if status == "completed" and not audio_url:
        raise RuntimeError("Task Genmax hoàn thành nhưng thiếu URL audio.")

    task = db.upsert_audio_task(
        video_id=video_id,
        request_hash=stored_task["request_hash"],
        task_id=stored_task["task_id"],
        status=status,
        audio_url=audio_url,
        error=str(error),
    )
    if status == "completed":
        _save_audio_url(video_id, audio_url)
    return task


def _watch_audio_task(video_id: int) -> None:
    try:
        while True:
            try:
                task = _sync_audio_task(video_id)
                if task["status"] in {"completed", "failed"}:
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


def _ensure_audio_task(video_id: int) -> dict:
    video = db.get_video(video_id)
    if not video:
        raise RuntimeError("Video không tồn tại.")

    script_for_tts = get_clean_script_for_tts(video["generated_script"])
    if not script_for_tts:
        raise RuntimeError(
            "Không tìm thấy kịch bản để đọc (thiếu INTRO/BODY/OUTRO)."
        )

    filtered_script = apply_tts_filters(script_for_tts)
    request_hash = tts.get_request_hash(filtered_script, AUDIO_VOICE_ID)

    with _audio_submit_lock:
        stored_task = db.get_audio_task(video_id)
        if stored_task:
            if stored_task["request_hash"] != request_hash:
                if stored_task["status"] in {"pending", "processing"}:
                    raise RuntimeError(
                        "Video đang có một audio task cho phiên bản kịch bản trước."
                    )
            else:
                if stored_task["status"] == "completed":
                    _save_audio_url(video_id, stored_task["audio_url"])
                elif stored_task["status"] in {"pending", "processing"}:
                    _start_audio_watcher(video_id)
                return stored_task

        shared_task = db.get_audio_task_by_request_hash(request_hash)
        if shared_task:
            task = db.upsert_audio_task(
                video_id=video_id,
                request_hash=request_hash,
                task_id=shared_task["task_id"],
                status=shared_task["status"],
                audio_url=shared_task.get("audio_url", ""),
                error=shared_task.get("error", ""),
            )
            if task["status"] == "completed":
                _save_audio_url(video_id, task["audio_url"])
            elif task["status"] in {"pending", "processing"}:
                _start_audio_watcher(video_id)
            return task

        # Fail closed: if history cannot be checked, no new paid task is submitted.
        remote_task = tts.find_matching_task(filtered_script, AUDIO_VOICE_ID)
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
            )
            if status == "completed" and audio_url:
                _save_audio_url(video_id, audio_url)
            elif status in {"pending", "processing"}:
                _start_audio_watcher(video_id)
            return task

        submitted_task = tts.submit_tts_task(filtered_script, AUDIO_VOICE_ID)
        task = db.upsert_audio_task(
            video_id=video_id,
            request_hash=request_hash,
            task_id=submitted_task["id"],
            status=submitted_task.get("status", "pending"),
        )
        _start_audio_watcher(video_id)
        return task


@app.on_event("startup")
def resume_audio_watchers() -> None:
    for task in db.get_active_audio_tasks():
        _start_audio_watcher(task["video_id"])

@app.post("/api/process-video")
def process_video(request: VideoRequest):
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "progress": "⏳ Đang khởi động...", "result": None, "error": None}

    def _run():
        def update(msg: str):
            with _jobs_lock:
                _jobs[job_id]["progress"] = msg

        try:
            update("📥 Đang tải phụ đề YouTube...")
            full_transcript = get_video_transcript(request.url)
            title = get_video_title(request.url)

            combined_text = f"TIÊU ĐỀ KỊCH BẢN: {title}\n\nNỘI DUNG:\n{full_transcript}"

            update("🤖 ChatGPT đang viết kịch bản (5-15 phút)...")
            worker_result = process_prompt_via_chatgpt(combined_text, request.prompt_version)
            summary_text = worker_result["script"] if isinstance(worker_result, dict) else worker_result
            chat_url = worker_result.get("chat_url", "") if isinstance(worker_result, dict) else ""

            update("💾 Đang lưu vào database...")
            video_id = db.save_video(request.url, title, full_transcript, summary_text, chat_url, request.prompt_version or 'default')
            audio_task = None
            audio_error = None
            if get_clean_script_for_tts(summary_text):
                try:
                    update("🎙️ Đang kiểm tra và gửi yêu cầu audio an toàn...")
                    audio_task = _ensure_audio_task(video_id)
                    summary_text = db.get_video(video_id)["generated_script"]
                except Exception as exc:
                    audio_error = str(exc)

            with _jobs_lock:
                _jobs[job_id].update({
                    "status": "done",
                    "progress": "✅ Hoàn thành!",
                    "result": {
                        "success": True,
                        "full_transcript": full_transcript,
                        "summary": summary_text,
                        "title": title,
                        "chat_url": chat_url,
                        "video_id": video_id,
                        "audio_task": (
                            _audio_task_response(audio_task) if audio_task else None
                        ),
                        "audio_error": audio_error,
                    }
                })

        except Exception as e:
            error_msg = str(e)
            if not error_msg:
                error_msg = repr(e)
            if "Could not retrieve a transcript" in error_msg or "Subtitles are disabled" in error_msg:
                error_msg = "Video này không có phụ đề (Transcript). Vui lòng chọn video khác."
            with _jobs_lock:
                _jobs[job_id].update({
                    "status": "error",
                    "progress": f"❌ Lỗi: {error_msg[:100]}",
                    "error": error_msg
                })

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/videos")
def get_videos(limit: int = 10, offset: int = 0, is_published: Optional[int] = None):
    return db.get_all_videos(limit=limit, offset=offset, is_published=is_published)

class GenerateThumbnailsRequest(BaseModel):
    script: str
    video_id: int = None  # optional - if given, updates db record
    thumbnail_type: Literal["with_text", "without_text", "both"]


class GenerateChaptersRequest(BaseModel):
    video_id: int


@app.post("/api/generate-chapters")
async def generate_chapters_endpoint(req: GenerateChaptersRequest):
    from auto_yt.services.chatgpt_worker import generate_chapters_only

    video = db.get_video(req.video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    loop = asyncio.get_event_loop()
    chapters = await loop.run_in_executor(
        None,
        lambda: generate_chapters_only(
            video["generated_script"],
            video.get("chat_url", ""),
            video.get("prompt_version", ""),
        ),
    )

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
        "chapters": chapters,
        "script": updated_script,
    }


@app.post("/api/generate-thumbnails")
async def generate_thumbnails_endpoint(req: GenerateThumbnailsRequest, background_tasks: BackgroundTasks):
    from auto_yt.services.chatgpt_worker import generate_thumbnails_only
    import concurrent.futures
    
    try:
        # Look up chat_url from DB if video_id provided
        resolved_chat_url = ""
        resolved_prompt_version = ""
        if req.video_id:
            video = db.get_video(req.video_id)
            if video:
                resolved_chat_url = video.get("chat_url", "")
                resolved_prompt_version = video.get("prompt_version", "")
                print(f"Using chat_url from DB: {resolved_chat_url}")

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
                if not partial_result.get(expected_image_key):
                    thumbnail_label = (
                        "có chữ"
                        if thumbnail_type == "with_text"
                        else "không chữ"
                    )
                    raise RuntimeError(
                        "ChatGPT không trả về ảnh thumbnail mới "
                        f"{thumbnail_label}. Ảnh cũ được giữ nguyên."
                    )
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
                    "with_text": ("THUMBNAIL CÓ CHỮ", "thumb_text", "image1_url"),
                    "without_text": ("THUMBNAIL KHÔNG CHỮ", "thumb_notext", "image2_url"),
                }

                for thumbnail_type in thumbnail_types:
                    section_title, text_key, image_key = thumbnail_configs[thumbnail_type]
                    generated_text = result.get(text_key)
                    image_url = result.get(image_key)
                    section_pattern = rf'### \[{re.escape(section_title)}\]\n(.*?)(?=\n### \[|\Z)'
                    section_match = re.search(section_pattern, script, re.DOTALL)
                    current_text = section_match.group(1).strip() if section_match else ""
                    updated_text = generated_text or current_text

                    if image_url:
                        updated_text = re.sub(r'\[IMAGE_URL:.*?\]', '', updated_text).strip()
                        updated_text += f"\n\n[IMAGE_URL:{image_url}]"

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


@app.post("/api/videos/{video_id}/generate-audio")
def generate_audio_for_video(video_id: int):
    """Create or resume the single persistent Genmax task for a video."""
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    script = video["generated_script"]
    if "### [AUDIO]" in script:
        stored_task = db.get_audio_task(video_id)
        if stored_task:
            return {
                "success": True,
                "audio_task": _audio_task_response(stored_task),
            }
        return {
            "success": True,
            "audio_task": {
                "video_id": video_id,
                "status": "completed",
                "audio_url": script.split("### [AUDIO]", 1)[1].strip(),
                "error": "",
            },
        }

    try:
        task = _ensure_audio_task(video_id)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

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
            },
        }
    return {
        "success": True,
        "audio_task": {
            "video_id": video_id,
            "status": "not_started",
            "audio_url": "",
            "error": "",
        },
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

        retried_task = tts.retry_tts_task(task["task_id"])
        updated_task = db.upsert_audio_task(
            video_id=video_id,
            request_hash=task["request_hash"],
            task_id=retried_task["id"],
            status=retried_task.get("status", "pending"),
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
    if not PROMPTS_PATH.exists():
        # Initialize with default if missing
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        PROMPTS_PATH.write_text(json.dumps(DEFAULT_PROMPTS_DATA, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_PROMPTS_DATA
    try:
        data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
        # Validate structure roughly
        if "active_version" not in data or "versions" not in data:
            return DEFAULT_PROMPTS_DATA
        return data
    except Exception:
        return DEFAULT_PROMPTS_DATA

@app.post("/api/prompts")
def save_prompts(data: PromptsData):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_PATH.write_text(
        json.dumps(data.dict(), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return {"success": True}

@app.post("/api/login-chatgpt")
async def trigger_login():
    from auto_yt.services.chatgpt_login import login_gpt_auto, restore_session
    from playwright.async_api import async_playwright

    try:
        if not ACCOUNT_PATH.exists():
            return {"success": False, "error": "Chưa có thông tin tài khoản"}
            
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

@app.post("/api/open-profile")
async def open_profile():
    from playwright.async_api import async_playwright
    import asyncio
    
    async def _launch():
        async with async_playwright() as p:
            profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
            context = await p.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"]
            )
            page = await context.new_page()
            await page.goto("https://chatgpt.com/")
            # Keep open for a bit or until closed
            try:
                await page.wait_for_timeout(600000) # 10 mins
            except:
                pass
            await context.close()
            
    # Launch as a separate asyncio task so it doesn't block the API
    import asyncio
    asyncio.create_task(_launch())
    return {"success": True, "message": "Browser profile opened on server."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True, loop="asyncio")
