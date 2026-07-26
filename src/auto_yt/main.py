from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import asyncio
import json
import threading
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

            # Generate Audio
            script_for_tts = get_clean_script_for_tts(summary_text)
            if script_for_tts:
                try:
                    update("🎙️ Đang tạo giọng đọc AI (tối đa 5 phút)...")
                    filtered_script = apply_tts_filters(script_for_tts)
                    voice_id = "e1d9617c-045c-4072-8d17-9be0ec113723"
                    audio_url = tts.generate_tts(filtered_script, voice_id)
                    if audio_url:
                        summary_text += f"\n\n### [AUDIO]\n{audio_url}"
                except Exception as e:
                    print(f"Error generating Audio: {e}")

            update("💾 Đang lưu vào database...")
            video_id = db.save_video(request.url, title, full_transcript, summary_text, chat_url, request.prompt_version or 'default')

            with _jobs_lock:
                _jobs[job_id].update({
                    "status": "done",
                    "progress": "✅ Hoàn thành!",
                    "result": {
                        "success": True,
                        "full_transcript": full_transcript,
                        "summary": summary_text,
                        "chat_url": chat_url,
                        "video_id": video_id
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
        result = await loop.run_in_executor(
            None,
            lambda: generate_thumbnails_only(req.script, resolved_chat_url, resolved_prompt_version)
        )
        
        # If video_id provided, patch the stored script to add image URLs
        if req.video_id:
            video = db.get_video(req.video_id)
            if video:
                script = video["generated_script"]
                import re
                
                # Handling Thumbnail 1
                new_text1 = result.get("thumb_text")
                if not new_text1:  # Treat empty string as None
                    new_text1 = None
                
                if new_text1 is None:
                    # User provided a custom prompt, so we keep the original text but append the image url
                    match1 = re.search(r'### \[THUMBNAIL CÓ CHỮ\]\n(.*?)(?=\n### \[|\Z)', script, re.DOTALL)
                    if match1:
                        candidate = match1.group(1).strip()
                        # Only keep if it has meaningful content (not just emoji or short reaction)
                        if len(candidate) > 20:
                            new_text1 = candidate
                
                if new_text1 is None:
                    new_text1 = ""
                    
                if new_text1 or result.get("image1_url"):
                    if result.get("image1_url"):
                        # Remove any existing [IMAGE_URL] tag before appending the new one
                        new_text1 = re.sub(r'\[IMAGE_URL:.*?\]', '', new_text1).strip()
                        new_text1 += f"\n\n[IMAGE_URL:{result['image1_url']}]"
                    
                    if "### [THUMBNAIL CÓ CHỮ]" in script:
                        script = re.sub(r'(### \[THUMBNAIL CÓ CHỮ\]\n).*?(?=\n### \[|\Z)', rf'\1{new_text1.strip()}\n\n', script, flags=re.DOTALL)

                # Handling Thumbnail 2
                new_text2 = result.get("thumb_notext")
                if not new_text2:
                    new_text2 = None
                    
                if new_text2 is None:
                    match2 = re.search(r'### \[THUMBNAIL KHÔNG CHỮ\]\n(.*?)(?=\n### \[|\Z)', script, re.DOTALL)
                    if match2:
                        candidate2 = match2.group(1).strip()
                        if len(candidate2) > 20:
                            new_text2 = candidate2
                        
                if new_text2 is None:
                    new_text2 = ""
                    
                if new_text2 or result.get("image2_url"):
                    if result.get("image2_url"):
                        new_text2 = re.sub(r'\[IMAGE_URL:.*?\]', '', new_text2).strip()
                        new_text2 += f"\n\n[IMAGE_URL:{result['image2_url']}]"
                        
                    if "### [THUMBNAIL KHÔNG CHỮ]" in script:
                        script = re.sub(r'(### \[THUMBNAIL KHÔNG CHỮ\]\n).*?(?=\n### \[|\Z)', rf'\1{new_text2.strip()}\n', script, flags=re.DOTALL)
                        
                db.update_script(req.video_id, script)
        
        return {"success": True, **result}
    except Exception as e:
        print(f"Error in generate_thumbnails_endpoint: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


@app.post("/api/videos/{video_id}/generate-audio")
def generate_audio_for_video(video_id: int):
    """Generate TTS audio for an existing video. Returns job_id immediately."""
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    script = video["generated_script"]
    if "### [AUDIO]" in script:
        return {"success": False, "error": "Video đã có audio. Không cần tạo lại."}

    script_for_tts = get_clean_script_for_tts(script)
    if not script_for_tts:
        return {"success": False, "error": "Không tìm thấy kịch bản để đọc (thiếu INTRO/BODY/OUTRO)."}

    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "progress": "🎙️ Đang tạo giọng đọc AI...", "result": None, "error": None}

    def _run():
        try:
            filtered_script = apply_tts_filters(script_for_tts)
            voice_id = "e1d9617c-045c-4072-8d17-9be0ec113723"
            audio_url = tts.generate_tts(filtered_script, voice_id)
            if not audio_url:
                raise Exception("TTS API không trả về URL audio.")
            new_script = script + f"\n\n### [AUDIO]\n{audio_url}"
            db.update_script(video_id, new_script)
            with _jobs_lock:
                _jobs[job_id].update({
                    "status": "done",
                    "progress": "✅ Tạo audio thành công!",
                    "result": {"success": True, "audio_url": audio_url, "updated_script": new_script}
                })
        except Exception as e:
            with _jobs_lock:
                _jobs[job_id].update({
                    "status": "error",
                    "progress": f"❌ Lỗi: {str(e)[:100]}",
                    "error": str(e)
                })

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


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
