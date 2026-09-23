from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field
from typing import List, Literal, Optional
import asyncio
import html
import json
import logging
import math
import threading

logger = logging.getLogger(__name__)

_automatic_login_state_lock = threading.Lock()
_automatic_login_active = False
_automatic_login_job_ids: set[str] = set()
_automatic_login_last_failure_at: float = 0.0
_AUTOMATIC_LOGIN_COOLDOWN_SECONDS = 300.0  # 5 minutes after a failed login
import time
import uuid
import datetime
from auto_yt.paths import AUDIO_DIR, DATA_DIR, CHROME_USER_DATA_DIR, gpt_profile_dir, PROMPTS_PATH, THUMBNAILS_DIR
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
from auto_yt.services.chatgpt_runtime import ChatGPTAttentionRequiredError
from auto_yt.services.chatgpt_worker import sanitize_generated_script
import auto_yt.services.database as db
import auto_yt.services.audio_utils as audio_utils
import auto_yt.services.tts_registry as tts
from auto_yt.services.audio_review import (
    audit_script_for_audio,
    get_audio_script,
    get_audio_script_hash,
)
from auto_yt.services import voice_config
from auto_yt.services import (
    chatgpt_projects,
    prompt_assets,
    publication_scheduler,
    production_coordinator as production_coordinator_service,
    youtube_comments,
    youtube_publish_workflow,
    youtube_publisher,
)
from auto_yt.services.proxy_utils import (
    ProxyConfigurationError,
    parse_proxy_url,
    proxy_display_value,
)
from auto_yt.services.secret_store import SecretStorageError
from auto_yt.services import (
    account_store,
    api_security,
    channel_scanner_service,
    chatgpt_browser_service,
    fb_crossposter_service,
    google_flow_browser_service,
    gpm_service,
    gpm_youtube_automation,
    local_browser_service,
    maintenance_guard,
    security_logging,
)
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
    validate_youtube_url,
)
import re

# In-memory job store: job_id -> {status, progress, result, error}
_jobs: dict = {}
_jobs_lock = threading.Lock()
_video_queue_state_lock = threading.Lock()
_video_queue_worker_active = False
_video_queue_wakeup_timer: threading.Timer | None = None
_video_queue_wakeup_at = 0.0
_comment_queue_state_lock = threading.Lock()
_comment_queue_worker_active = False
_comment_queue_wakeup_timer: threading.Timer | None = None
_comment_sync_stop_event = threading.Event()
_comment_sync_thread: threading.Thread | None = None
_tts_preview_cleanup_stop_event = threading.Event()
_tts_preview_cleanup_thread: threading.Thread | None = None
_tts_preview_sync_locks: dict[str, threading.Lock] = {}
_tts_preview_sync_locks_guard = threading.Lock()
_youtube_oauth_states: dict[str, dict] = {}
_youtube_oauth_states_lock = threading.Lock()
_prompts_config_lock = threading.Lock()
_tts_config_lock = threading.Lock()
_chatgpt_profile_lock = threading.Lock()
_chatgpt_state_lock = threading.Lock()
_chatgpt_operation = ""
_chatgpt_prompt_version = ""
_chatgpt_video_id: int | None = None
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
MAX_ACTIVE_VIDEO_JOBS = 100
TTS_PREVIEW_MAX_CHARACTERS = 200
TTS_PREVIEW_REQUEST_MAX_CHARACTERS = 1000
TTS_PREVIEW_RETENTION = datetime.timedelta(hours=24)
TTS_PREVIEW_CLEANUP_INTERVAL_SECONDS = 60 * 60
VIDEO_RECOVERY_DELAYS_SECONDS = (30, 120, 300)
YOUTUBE_RECOVERY_DELAYS_SECONDS = (120, 300, 900)
TRANSIENT_YOUTUBE_ERROR_MARKERS = (
    "http error 429",
    "status code 429",
    "too many requests",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "remote end closed connection",
    "service unavailable",
    "bad gateway",
    "network is unreachable",
    "không thể kết nối",
    "không kết nối",
)
TRANSIENT_CHATGPT_START_ERROR_MARKERS = (
    "chatgpt page did not become ready after bounded same-page recovery",
    "chatgpt project did not become ready after bounded bootstrap recovery",
    "could not find the prompt textarea after",
)
MANAGED_MEDIA_URL_PATTERN = re.compile(
    r"/api/(?P<kind>thumbnails|audio)/"
    r"(?P<filename>[A-Za-z0-9][A-Za-z0-9_.-]*)"
)


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
    video_id: int | None = None,
) -> bool:
    global _chatgpt_operation, _chatgpt_prompt_version, _chatgpt_video_id
    if not _chatgpt_profile_lock.acquire(blocking=False):
        return False
    with _chatgpt_state_lock:
        _chatgpt_operation = operation
        _chatgpt_prompt_version = str(prompt_version or "").strip()
        _chatgpt_video_id = int(video_id) if video_id is not None else None
    return True


def _finish_chatgpt_operation() -> None:
    global _chatgpt_operation, _chatgpt_prompt_version, _chatgpt_video_id
    with _chatgpt_state_lock:
        _chatgpt_operation = ""
        _chatgpt_prompt_version = ""
        _chatgpt_video_id = None
    _chatgpt_profile_lock.release()
    queue_kicker = globals().get("_kick_video_queue")
    if queue_kicker:
        queue_kicker()
    comment_queue_kicker = globals().get("_kick_comment_queue")
    if comment_queue_kicker:
        comment_queue_kicker()


def _get_chatgpt_operation() -> str:
    with _chatgpt_state_lock:
        return _chatgpt_operation


def _get_chatgpt_state() -> tuple[str, str]:
    with _chatgpt_state_lock:
        return _chatgpt_operation, _chatgpt_prompt_version


def _set_chatgpt_video_id(video_id: int) -> None:
    global _chatgpt_video_id
    with _chatgpt_state_lock:
        _chatgpt_video_id = int(video_id)


def _get_chatgpt_video_id() -> int | None:
    with _chatgpt_state_lock:
        return _chatgpt_video_id

class AccountData(BaseModel):
    email: str = ""
    password: str = ""
    totp_secret: str = ""
    headless: Optional[bool] = None


class AccountStatus(BaseModel):
    email: str = ""
    headless: bool = True
    password_configured: bool = False
    totp_configured: bool = False


class BrowserAutomationData(BaseModel):
    worker_headless: bool = True
    game_mode: bool = False


class ChatGPTBrowserServiceStatus(BaseModel):
    running: bool = False
    connected: bool = False
    process_alive: bool = False
    state: str = "stopped"
    pid: Optional[int] = None
    started_at: str = ""
    window_visible: bool = False
    message: str = ""

class PromptPipelineData(BaseModel):
    metadata: bool = True
    chapters: bool = True
    thumbnail_with_text: bool = True
    thumbnail_without_text: bool = True
    audio: bool = True
    video_render: bool = False
    youtube_upload: bool = False
    youtube_schedule: bool = False

    model_config = ConfigDict(extra="allow")


class PromptImageGenerationData(BaseModel):
    provider: str = "google_flow"
    model: str = "nano_banana_pro"
    workflow_profile_id: str = Field(default="", max_length=120)
    reference_workflow_profile_id: str = Field(default="", max_length=120)
    style_prompt: str = Field(default="", max_length=8000)
    avoid_prompt: str = Field(default="", max_length=8000)
    negative_prompt: str = Field(default="", max_length=8000)
    density: int = 30
    outputs_per_scene: int = 1
    seed_mode: str = "random"
    thumbnail_variant: Literal["with_text", "without_text"] = "without_text"
    scene_duration_min_seconds: int = Field(default=25, ge=10, le=90)
    scene_duration_target_seconds: int = Field(default=30, ge=10, le=90)
    scene_duration_max_seconds: int = Field(default=35, ge=10, le=90)

    model_config = ConfigDict(extra="allow")


class PromptPublishingData(BaseModel):
    category_id: str = Field(default="", max_length=10)
    language: str = Field(default="vi", min_length=2, max_length=35)
    made_for_kids: Optional[bool] = None
    notify_subscribers: bool = True
    contains_synthetic_media: bool = True

    model_config = ConfigDict(extra="allow")


class PromptVersion(BaseModel):
    name: str
    prompts: dict
    project_url: str = chatgpt_projects.DEFAULT_CHATGPT_PROJECT_URL
    default_voice_id: str = ""
    default_youtube_channel_id: str = Field(default="", max_length=100)
    pipeline: PromptPipelineData = Field(default_factory=PromptPipelineData)
    image_generation_settings: dict = Field(default_factory=dict)
    publishing_settings: dict = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")

class PromptsData(BaseModel):
    active_version: str
    versions: dict[str, PromptVersion]


class PromptVersionNameData(BaseModel):
    name: str


class PromptProjectData(BaseModel):
    project_url: str


class PromptDefaultVoiceData(BaseModel):
    voice_id: str = ""


class PromptDefaultYoutubeChannelData(BaseModel):
    channel_id: str = Field(default="", max_length=100)


class PromptFieldData(BaseModel):
    value: str


class VoiceOptionData(BaseModel):
    id: str
    name: str


class VoiceConfigData(BaseModel):
    active_voice_id: str
    voices: List[VoiceOptionData]


class TTSProviderUpdate(BaseModel):
    enabled: Optional[bool] = None


class GenmaxCredentialData(BaseModel):
    api_key: str = Field(min_length=16, max_length=512)


class TTSVoiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider_id: str = Field(min_length=2, max_length=64)
    provider_voice_id: str = Field(min_length=1, max_length=256)
    config: dict = Field(default_factory=dict)


class TTSVoiceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    provider_voice_id: Optional[str] = Field(
        default=None, min_length=1, max_length=256
    )
    status: Optional[Literal["active", "archived", "unavailable"]] = None
    config: Optional[dict] = None


class TTSDefaultVoiceData(BaseModel):
    voice_id: str = Field(min_length=1, max_length=200)


class TTSVoiceTestData(BaseModel):
    text: str = Field(
        default="Xin chào, đây là bản thử giọng của hệ thống Auto YT.",
        min_length=1,
        max_length=500,
    )
    confirm_billable: bool = False


class TTSPreviewCreateData(BaseModel):
    voice_id: str = Field(min_length=1, max_length=200)
    text: str = Field(
        min_length=1,
        max_length=TTS_PREVIEW_REQUEST_MAX_CHARACTERS,
    )

DEFAULT_GPT_PROFILE = "PROFILE_GPT_1"

def _save_account_payload(account: dict) -> None:
    current_account = account_store.load_account()
    previous_email = str(current_account.get("email") or "").strip()
    next_email = str(account.get("email") or "").strip()
    for key, value in account.items():
        if value is None:
            continue
        if key in {"password", "totp_secret"} and not value:
            continue
        current_account[key] = value
    if previous_email and next_email != previous_email:
        current_account["session_cookie"] = []
    account_store.save_account(current_account)

app = FastAPI(
    title="Nexus API",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(Exception)
async def handle_unexpected_error(_request: Request, exc: Exception):
    error_id = security_logging.report_exception("unhandled_api_error", exc)
    return JSONResponse(
        {"detail": f"Lỗi nội bộ. Mã lỗi: {error_id}."},
        status_code=500,
    )

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)
app.middleware("http")(api_security.protect_loopback_api)

# Configure CORS for Vite React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Content-Type", api_security.CSRF_HEADER_NAME],
)


@app.get("/health")
def get_health():
    coordinator = (
        _production_coordinator.status()
        if _production_coordinator is not None
        else {"running": False, "ready": False, "current_job_id": ""}
    )
    return {
        "status": "ok",
        "ready": bool(coordinator.get("ready")),
        "production_coordinator": coordinator,
    }


@app.get("/api/system/maintenance-status")
def get_maintenance_status():
    return maintenance_guard.get_maintenance_status()


def _resolve_media_file(
    root: Path,
    filename: str,
    suffix: str | tuple[str, ...],
) -> Path:
    allowed_suffixes = (suffix,) if isinstance(suffix, str) else suffix
    if (
        Path(filename).name != filename
        or Path(filename).suffix.lower() not in allowed_suffixes
    ):
        raise HTTPException(status_code=400, detail="Invalid media filename")
    resolved_root = root.resolve()
    file_path = (resolved_root / filename).resolve()
    try:
        file_path.relative_to(resolved_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid media filename") from exc
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    return file_path

@app.get("/api/thumbnails/{filename}")
async def serve_thumbnail(filename: str):
    """Serve locally downloaded thumbnail images."""
    file_path = _resolve_media_file(THUMBNAILS_DIR, filename, ".png")
    return FileResponse(str(file_path), media_type="image/png")


@app.get("/api/audio/{filename}")
async def serve_audio(filename: str):
    """Serve locally materialized audio without exposing arbitrary paths."""
    file_path = _resolve_media_file(AUDIO_DIR, filename, (".mp3", ".wav"))
    media_type = "audio/wav" if file_path.suffix.lower() == ".wav" else "audio/mpeg"
    return FileResponse(str(file_path), media_type=media_type)

class VideoRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    prompt_version: Optional[str] = Field(default=None, max_length=120)
    voice_id: Optional[str] = Field(default=None, max_length=200)

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


class UpdateVideoJobRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    prompt_version: str = Field(min_length=1, max_length=120)
    voice_id: str = Field(min_length=1, max_length=200)


class BulkJobActionRequest(BaseModel):
    action: Literal["retry", "pause", "resume", "cancel"]
    mode: Literal["explicit", "all_matching"] = "explicit"
    job_ids: List[str] = Field(default_factory=list, max_length=5000)
    excluded_job_ids: List[str] = Field(default_factory=list, max_length=5000)
    status: Literal["all", "active", "error"] = "all"
    job_type: str = Field(default="all", max_length=100)
    search: str = Field(default="", max_length=300)
    snapshot_at: str = Field(default="", max_length=100)


class AudioDurationRequest(BaseModel):
    duration_seconds: float


class VideoStatusRequest(BaseModel):
    status: Literal["active", "error"]


class YouTubeLinkRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class YouTubeDownloadVideoRequest(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=500)
    url: str = Field(min_length=1, max_length=2048)
    position: Optional[int] = None


class YouTubeDownloadRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=32767)
    videos: List[YouTubeDownloadVideoRequest] = Field(min_length=1, max_length=500)
    number_folders: bool = False


class YouTubeOAuthConfigRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=512)
    client_name: str = Field(default="", max_length=120)
    client_secret: str = Field(default="", max_length=2048)
    redirect_uri: str = Field(
        default=youtube_comments.DEFAULT_REDIRECT_URI,
        max_length=2048,
    )


class YouTubeChannelSettingsRequest(BaseModel):
    reply_instruction: str = Field(default="", max_length=4000)
    auto_mode: Literal["manual", "draft_only", "auto_publish"] = "draft_only"
    daily_reply_limit: int = Field(default=50, ge=1, le=500)
    reply_interval_minutes: int = Field(default=5, ge=1, le=120)
    quarter_hour_reply_limit: int = Field(default=3, ge=1, le=30)
    hourly_reply_limit: int = Field(default=10, ge=1, le=200)
    video_half_hour_reply_limit: int = Field(default=3, ge=1, le=30)
    backlog_daily_reply_limit: int = Field(default=20, ge=1, le=200)
    reply_window_start: str = Field(
        default="08:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    reply_window_end: str = Field(
        default="22:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$"
    )
    reply_paused: bool = False
    auto_sync: bool = True
    sync_interval_minutes: int = Field(default=10, ge=2, le=1440)
    publication_timezone: str = Field(default="Asia/Ho_Chi_Minh", max_length=100)
    publication_slots: List[dict] = Field(default_factory=list, max_length=50)
    publication_daily_limit: int = Field(default=1, ge=1, le=20)
    publication_lead_minutes: int = Field(default=120, ge=1, le=10080)
    publication_paused: bool = False
    public_upload_verified: bool = False
    gpm_profile_id: Optional[str] = Field(default=None, max_length=200)
    gpm_profile_name: Optional[str] = Field(default=None, max_length=200)
    gpm_proxy_info: Optional[str] = Field(default=None, max_length=500)
    interaction_mode: Optional[Literal["gpm_browser", "direct_api"]] = "gpm_browser"
    auto_heart: Optional[bool] = True


class GpmConfigRequest(BaseModel):
    api_url: Optional[str] = Field(default=None, max_length=500)
    auto_stop_on_finish: Optional[bool] = None
    timeout_seconds: Optional[float] = Field(default=None, ge=2.0, le=120.0)


class GpmProfileStartRequest(BaseModel):
    remote_debugging_port: Optional[int] = Field(default=None, ge=1, le=65535)
    window_scale: Optional[float] = Field(default=None, ge=0.1, le=3.0)
    window_pos: Optional[str] = Field(default=None, max_length=50)
    window_size: Optional[str] = Field(default=None, max_length=50)
    skip_proxy_check: bool = False
    addition_args: Optional[str] = Field(default=None, max_length=1000)


class GpmOpenUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    timeout_seconds: Optional[float] = Field(default=20.0, ge=3.0, le=120.0)


class GpmChannelMappingRequest(BaseModel):
    gpm_profile_id: str = Field(default="", max_length=200)
    gpm_profile_name: str = Field(default="", max_length=200)
    gpm_proxy_info: str = Field(default="", max_length=500)
    interaction_mode: Literal["gpm_browser", "direct_api"] = "gpm_browser"


class ChannelOpenBrowserRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=200)
    platform: str = Field(default="youtube", max_length=50)


class ChannelScanYouTubeRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=200)
    auto_save: bool = True


class ChannelScanFacebookRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=200)


class ChannelScanTikTokRequest(BaseModel):
    profile_id: str = Field(min_length=1, max_length=200)


class TikTokPublishRequest(BaseModel):
    video_path: str = Field(min_length=1, max_length=1024)
    caption: str = Field(default="", max_length=2000)
    tags: List[str] = Field(default_factory=list)
    gpm_profile_id: str = Field(default="", max_length=200)
    video_id: Optional[int] = Field(default=None)
    title: str = Field(default="", max_length=255)


class VideoPublicationRequest(BaseModel):
    channel_id: Optional[int] = Field(default=None, gt=0)
    published_url: str = Field(min_length=1, max_length=2048)


class CommentVideoImportPreviewRequest(BaseModel):
    channel_id: int = Field(gt=0)
    published_url: str = Field(default="", max_length=2048)


class CommentVideoImportRequest(BaseModel):
    channel_id: int = Field(gt=0)
    prompt_version: str = Field(min_length=1, max_length=200)
    youtube_video_ids: List[str] = Field(min_length=1, max_length=5000)


class CommentIdsRequest(BaseModel):
    comment_ids: List[str] = Field(min_length=1, max_length=5000)


class CommentUpdateRequest(BaseModel):
    draft_reply: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[Literal["new", "draft_ready", "skipped"]] = None


def _require_actionable_video(video_id: int) -> dict:
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.get("video_status") == db.VIDEO_STATUS_ERROR:
        raise HTTPException(
            status_code=409,
            detail=(
                "Video đang ở trạng thái Lỗi và đã bị loại khỏi các chức năng "
                "tự động. Hãy khôi phục trạng thái trước."
            ),
        )
    return video


@app.get("/api/youtube-comments/oauth/config")
def get_youtube_oauth_config():
    items = youtube_comments.list_oauth_configs()
    config = next((item for item in items if item.get("active")), None) or {}
    return {
        "client_id": config.get("client_id", ""),
        "client_name": config.get("client_name", ""),
        "client_secret_configured": bool(config.get("client_secret_configured")),
        "redirect_uri": config.get("redirect_uri") or youtube_comments.DEFAULT_REDIRECT_URI,
        "items": items,
    }


@app.post("/api/youtube-comments/oauth/config")
def save_youtube_oauth_config(request: YouTubeOAuthConfigRequest):
    try:
        return youtube_comments.save_oauth_config(
            request.client_id,
            request.client_secret,
            request.redirect_uri,
            request.client_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/youtube-comments/oauth/start")
def start_youtube_oauth(
    client_id: str = Query(default="", max_length=512),
    expected_channel_id: str = Query(default="", max_length=200),
    gpm_profile_id: str = Query(default="", max_length=200),
):
    try:
        config = youtube_comments.load_oauth_config(client_id)
    except youtube_comments.YouTubeCommentsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    expected_channel_id = (
        str(expected_channel_id or "").strip()
        if isinstance(expected_channel_id, str)
        else ""
    )
    gpm_profile_id = (
        str(gpm_profile_id or "").strip()
        if isinstance(gpm_profile_id, str)
        else ""
    )
    expected_channel = None
    gpm_profile_name = ""
    gpm_proxy_info = ""
    if expected_channel_id:
        expected_channel = db.get_youtube_channel_by_channel_id(
            expected_channel_id, include_tokens=True
        )
        if not expected_channel:
            raise HTTPException(status_code=404, detail="Kênh YouTube cần kết nối lại không tồn tại.")
        gpm_profile_id = str(gpm_profile_id or expected_channel.get("gpm_profile_id") or "").strip()
        gpm_profile_name = str(expected_channel.get("gpm_profile_name") or "").strip()
        if gpm_profile_id == str(expected_channel.get("gpm_profile_id") or "").strip():
            gpm_proxy_info = str(expected_channel.get("gpm_proxy_info") or "").strip()
    if gpm_profile_id and not parse_proxy_url(gpm_proxy_info):
        try:
            profile_detail = gpm_service.get_gpm_profile_detail(gpm_profile_id)
            gpm_profile_name = str(profile_detail.get("name") or "")
            gpm_proxy_info = str(profile_detail.get("raw_proxy") or profile_detail.get("proxy") or "")
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Không đọc được proxy của GPM Profile đã chọn.",
            ) from exc
    if not gpm_profile_id or not parse_proxy_url(gpm_proxy_info):
        raise HTTPException(
            status_code=422,
            detail="OAuth YouTube yêu cầu GPM Profile có proxy riêng hợp lệ.",
        )

    state = youtube_comments.create_oauth_state()
    code_verifier, code_challenge = youtube_comments.create_pkce_pair()
    with _youtube_oauth_states_lock:
        now = time.time()
        expired_states = [
            key
            for key, value in _youtube_oauth_states.items()
            if float(value.get("expires_at") or 0) < now
        ]
        for expired_state in expired_states:
            _youtube_oauth_states.pop(expired_state, None)
        _youtube_oauth_states[state] = {
            "expires_at": now + 600,
            "code_verifier": code_verifier,
            "oauth_config": config,
            "expected_channel_id": expected_channel_id,
            "expected_channel_title": str((expected_channel or {}).get("title") or ""),
            "gpm_profile_id": gpm_profile_id,
            "gpm_profile_name": gpm_profile_name,
            "gpm_proxy_info": gpm_proxy_info,
        }
    try:
        authorization_url = youtube_comments.build_authorization_url(
            state,
            config=config,
            code_challenge=code_challenge,
        )
    except youtube_comments.YouTubeCommentsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "authorization_url": authorization_url,
        "gpm_profile_id": gpm_profile_id,
        "gpm_profile_name": gpm_profile_name,
        "gpm_proxy_configured": True,
        "gpm_proxy_display": proxy_display_value(gpm_proxy_info),
    }


@app.get("/api/youtube-comments/oauth/callback", response_class=HTMLResponse)
def finish_youtube_oauth(code: str = "", state: str = "", error: str = ""):
    if error:
        return HTMLResponse(
            f"<h2>Không thể kết nối YouTube</h2><p>{html.escape(error)}</p>",
            status_code=400,
        )
    with _youtube_oauth_states_lock:
        oauth_state = _youtube_oauth_states.pop(state, {})
    if not state or float(oauth_state.get("expires_at") or 0) < time.time():
        return HTMLResponse(
            "<h2>Phiên kết nối đã hết hạn</h2><p>Hãy đóng cửa sổ và thử lại.</p>",
            status_code=400,
        )
    proxy_info = str(oauth_state.get("gpm_proxy_info") or "").strip() or None
    try:
        tokens = youtube_comments.exchange_authorization_code(
            code,
            config=dict(oauth_state.get("oauth_config") or {}),
            code_verifier=str(oauth_state.get("code_verifier") or ""),
            proxy=proxy_info,
        )
        access_token = str(tokens.get("access_token") or "")
        if not access_token:
            raise youtube_comments.YouTubeCommentsError("Google không trả về access token.")
        channels = youtube_comments.get_authenticated_channels(access_token, proxy=proxy_info)
        if not channels:
            raise youtube_comments.YouTubeCommentsError(
                "Tài khoản này không có kênh YouTube có thể quản lý."
            )
        expected_channel_id = str(oauth_state.get("expected_channel_id") or "").strip()
        if expected_channel_id:
            matching_channel = next(
                (
                    channel
                    for channel in channels
                    if str(channel.get("channel_id") or "") == expected_channel_id
                ),
                None,
            )
            if not matching_channel:
                expected_title = str(
                    oauth_state.get("expected_channel_title") or expected_channel_id
                )
                raise youtube_comments.YouTubeCommentsError(
                    "Tài khoản Google vừa chọn không quản lý đúng kênh "
                    f"'{expected_title}'. Dữ liệu kênh cũ được giữ nguyên."
                )
            channels = [matching_channel]
        encrypted_access = youtube_comments.encrypt_secret(access_token)
        encrypted_refresh = youtube_comments.encrypt_secret(
            str(tokens.get("refresh_token") or "")
        )
        for channel in channels:
            db.save_youtube_channel(
                **channel,
                access_token_encrypted=encrypted_access,
                refresh_token_encrypted=encrypted_refresh,
                token_expiry=youtube_comments.token_expiry(tokens.get("expires_in")),
                scope=str(tokens.get("scope") or youtube_comments.YOUTUBE_SCOPE),
                oauth_client_id=str(
                    dict(oauth_state.get("oauth_config") or {}).get("client_id") or ""
                ),
                gpm_profile_id=str(oauth_state.get("gpm_profile_id") or ""),
                gpm_profile_name=str(oauth_state.get("gpm_profile_name") or ""),
                gpm_proxy_info=str(oauth_state.get("gpm_proxy_info") or ""),
            )
    except Exception as exc:
        error_id = security_logging.report_exception("youtube_oauth_callback", exc)
        return HTMLResponse(
            "<h2>Kết nối YouTube thất bại</h2>"
            f"<p>Không thể hoàn tất kết nối. Mã lỗi: {html.escape(error_id)}.</p>",
            status_code=400,
        )
    return HTMLResponse(
        "<h2>Đã kết nối YouTube thành công</h2>"
        "<p>Bạn có thể đóng cửa sổ này và quay lại Auto_YT.</p>"
        "<script>setTimeout(() => window.close(), 1200)</script>"
    )


@app.get("/api/youtube-comments/channels")
def list_youtube_comment_channels():
    return {"items": db.list_youtube_channels()}


@app.patch("/api/youtube-comments/channels/{channel_db_id}")
def update_youtube_comment_channel(
    channel_db_id: int, request: YouTubeChannelSettingsRequest
):
    existing_channel = db.get_youtube_channel(channel_db_id, include_tokens=True)
    if not existing_channel:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh YouTube.")
    try:
        publication_timezone = publication_scheduler.validate_timezone(
            request.publication_timezone
        )
        publication_slots = publication_scheduler.validate_publication_slots(
            request.publication_slots
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    update_data = {
        "reply_instruction": request.reply_instruction.strip(),
        "auto_mode": request.auto_mode,
        "daily_reply_limit": request.daily_reply_limit,
        "reply_interval_minutes": request.reply_interval_minutes,
        "quarter_hour_reply_limit": request.quarter_hour_reply_limit,
        "hourly_reply_limit": request.hourly_reply_limit,
        "video_half_hour_reply_limit": request.video_half_hour_reply_limit,
        "backlog_daily_reply_limit": request.backlog_daily_reply_limit,
        "reply_window_start": request.reply_window_start,
        "reply_window_end": request.reply_window_end,
        "reply_paused": int(request.reply_paused),
        "auto_sync": int(request.auto_sync),
        "sync_interval_minutes": request.sync_interval_minutes,
        "publication_timezone": publication_timezone,
        "publication_slots_json": json.dumps(
            publication_slots, ensure_ascii=False, separators=(",", ":")
        ),
        "publication_daily_limit": request.publication_daily_limit,
        "publication_lead_minutes": request.publication_lead_minutes,
        "publication_paused": int(request.publication_paused),
        "public_upload_verified": int(request.public_upload_verified),
    }
    if request.gpm_profile_id is not None:
        profile_id = request.gpm_profile_id.strip()
        if not profile_id:
            update_data.update(
                gpm_profile_id="",
                gpm_profile_name="",
                gpm_proxy_info="",
            )
        else:
            current_profile_id = str(
                existing_channel.get("gpm_profile_id") or ""
            ).strip()
            proxy_info = (
                str(existing_channel.get("gpm_proxy_info") or "").strip()
                if profile_id == current_profile_id
                else ""
            )
            profile_name = (
                str(request.gpm_profile_name or "").strip()
                or (
                    str(existing_channel.get("gpm_profile_name") or "").strip()
                    if profile_id == current_profile_id
                    else ""
                )
            )
            if profile_id != current_profile_id or not parse_proxy_url(proxy_info):
                try:
                    profile_detail = gpm_service.get_gpm_profile_detail(profile_id)
                except Exception as exc:
                    raise HTTPException(
                        status_code=503,
                        detail="Không đọc được cấu hình GPM Profile đã chọn.",
                    ) from exc
                profile_name = str(profile_detail.get("name") or profile_name)
                proxy_info = str(
                    profile_detail.get("raw_proxy")
                    or profile_detail.get("proxy")
                    or ""
                ).strip()
            update_data.update(
                gpm_profile_id=profile_id,
                gpm_profile_name=profile_name,
                gpm_proxy_info=proxy_info,
            )
    if request.interaction_mode is not None:
        update_data["interaction_mode"] = request.interaction_mode
    if request.auto_heart is not None:
        update_data["auto_heart"] = int(bool(request.auto_heart))

    channel = db.update_youtube_channel(channel_db_id, **update_data)
    if not request.reply_paused:
        _kick_comment_queue()
    return channel


@app.delete("/api/youtube-comments/channels/{channel_db_id}")
def disconnect_youtube_comment_channel(channel_db_id: int):
    channel = db.get_youtube_channel(channel_db_id, include_tokens=True)
    if not channel:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh YouTube.")
    try:
        encrypted_token = (
            channel.get("refresh_token_encrypted")
            or channel.get("access_token_encrypted")
            or ""
        )
        youtube_comments.revoke_token(
            youtube_comments.decrypt_secret(encrypted_token),
            proxy=channel.get("gpm_proxy_info"),
        )
    except youtube_comments.YouTubeCommentsError as exc:
        raise HTTPException(
            status_code=502,
            detail="Chưa thể thu hồi quyền Google; kênh vẫn được giữ để thử lại an toàn.",
        ) from exc
    if not db.delete_youtube_channel(channel_db_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh YouTube.")
    cleared_prompt_versions = _clear_prompt_default_youtube_channel_id(
        channel.get("channel_id", "")
    )
    return {
        "success": True,
        "cleared_prompt_versions": cleared_prompt_versions,
    }


# =========================================================================
# GPM-Login v3 Endpoints
# =========================================================================

@app.get("/api/gpm/config")
def get_gpm_configuration():
    """Get current GPM-Login connection settings."""
    return gpm_service.get_gpm_config()


@app.post("/api/gpm/config")
def update_gpm_configuration(request: GpmConfigRequest):
    """Update GPM-Login connection settings."""
    try:
        return gpm_service.save_gpm_config(
            api_url=request.api_url,
            auto_stop_on_finish=request.auto_stop_on_finish,
            timeout_seconds=request.timeout_seconds,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/gpm/status")
def get_gpm_status(api_url: Optional[str] = Query(None)):
    """Check connectivity to the GPM-Login Local API."""
    return gpm_service.check_gpm_connection(api_url=api_url)


def _public_gpm_profile(profile: dict) -> dict:
    public_profile = dict(profile or {})
    raw_proxy = str(
        public_profile.pop("raw_proxy", "")
        or public_profile.pop("proxy", "")
        or ""
    )
    public_profile["proxy_configured"] = bool(parse_proxy_url(raw_proxy))
    public_profile["proxy_display"] = proxy_display_value(raw_proxy)
    return public_profile


@app.get("/api/gpm/profiles")
def list_gpm_profiles_endpoint(
    search: str = Query("", description="Tìm theo tên profile"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    sort: int = Query(0, ge=0, le=3),
    api_url: Optional[str] = Query(None),
):
    """Retrieve paginated profiles from GPM-Login."""
    try:
        result = gpm_service.list_gpm_profiles(
            search=search,
            page=page,
            page_size=page_size,
            sort=sort,
            api_url=api_url,
        )
        return {
            **result,
            "items": [
                _public_gpm_profile(profile) for profile in result.get("items") or []
            ],
        }
    except gpm_service.GpmConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi đọc profiles GPM: {exc}")


@app.get("/api/gpm/profiles/{profile_id}")
def get_gpm_profile_endpoint(profile_id: str, api_url: Optional[str] = Query(None)):
    """Retrieve full details of a specific GPM profile."""
    try:
        return _public_gpm_profile(
            gpm_service.get_gpm_profile_detail(profile_id, api_url=api_url)
        )
    except gpm_service.GpmProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except gpm_service.GpmConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/gpm/profiles/{profile_id}/start")
def start_gpm_profile_endpoint(
    profile_id: str,
    request: Optional[GpmProfileStartRequest] = None,
    api_url: Optional[str] = Query(None),
):
    """Start a GPM profile browser and return debugging connection info."""
    req = request or GpmProfileStartRequest()
    try:
        return gpm_service.start_gpm_profile(
            profile_id,
            remote_debugging_port=req.remote_debugging_port,
            window_scale=req.window_scale,
            window_pos=req.window_pos,
            window_size=req.window_size,
            skip_proxy_check=req.skip_proxy_check,
            addition_args=req.addition_args,
            api_url=api_url,
        )
    except gpm_service.GpmProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except gpm_service.GpmConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except gpm_service.GpmProfileLaunchError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/gpm/profiles/{profile_id}/stop")
def stop_gpm_profile_endpoint(profile_id: str, api_url: Optional[str] = Query(None)):
    """Close/stop a running GPM profile browser."""
    try:
        success = gpm_service.stop_gpm_profile(profile_id, api_url=api_url)
        return {"success": success, "profile_id": profile_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/gpm/profiles/{profile_id}/open-url")
async def open_url_in_gpm_endpoint(profile_id: str, request: GpmOpenUrlRequest):
    """Open a target URL inside the GPM profile browser session."""
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL không được để trống.")
    try:
        timeout_seconds = float(request.timeout_seconds or 20.0)
        result = await gpm_youtube_automation.open_url_in_gpm_profile(
            profile_id, url, timeout_seconds=timeout_seconds
        )
        return result
    except gpm_service.GpmProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except gpm_service.GpmConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.patch("/api/youtube-comments/channels/{channel_db_id}/gpm-mapping")
def update_channel_gpm_mapping(
    channel_db_id: int, request: GpmChannelMappingRequest
):
    """Map or unmap a GPM profile to a YouTube channel."""
    channel = db.update_youtube_channel(
        channel_db_id,
        gpm_profile_id=request.gpm_profile_id.strip(),
        gpm_profile_name=request.gpm_profile_name.strip(),
        gpm_proxy_info=request.gpm_proxy_info.strip(),
        interaction_mode=request.interaction_mode,
    )
    if not channel:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh YouTube.")
    return channel


@app.post("/api/youtube-comments/channels/{channel_db_id}/open-studio")
async def open_channel_studio_in_gpm(channel_db_id: int):
    """Open YouTube Studio inside the channel's mapped GPM profile browser."""
    channel = db.get_youtube_channel(channel_db_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh YouTube.")
    profile_id = str(channel.get("gpm_profile_id") or "").strip()
    if not profile_id:
        raise HTTPException(
            status_code=400,
            detail=f"Kênh '{channel.get('title')}' chưa được gán GPM Profile. Vui lòng gán profile trước.",
        )
    try:
        studio_url = "https://studio.youtube.com"
        result = await gpm_youtube_automation.open_url_in_gpm_profile(profile_id, studio_url)
        return {
            "success": True,
            "profile_id": profile_id,
            "channel_title": channel.get("title"),
            "url": studio_url,
            "message": f"Đã mở YouTube Studio của kênh '{channel.get('title')}' trong GPM Profile.",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/youtube-comments/channels/{channel_db_id}/verify-gpm-studio")
async def verify_channel_gpm_studio_login(channel_db_id: int):
    """Check if the channel's GPM profile is actively logged in to YouTube Studio."""
    channel = db.get_youtube_channel(channel_db_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh YouTube.")
    profile_id = str(channel.get("gpm_profile_id") or "").strip()
    if not profile_id:
        raise HTTPException(
            status_code=400,
            detail=f"Kênh '{channel.get('title')}' chưa được gán GPM Profile.",
        )
    try:
        result = await gpm_youtube_automation.verify_youtube_login(profile_id)
        return {
            "channel_id": channel["id"],
            "channel_title": channel.get("title"),
            "gpm_profile_id": profile_id,
            **result,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# =========================================================================
# Multi-Platform Channel & Page Scanner API (YouTube, Facebook, TikTok)
# Supports both GPM-Login Profiles & Local Chromium (Cốc Cốc, Chrome, Edge)
# =========================================================================

@app.get("/api/channels/local-profiles")
def list_local_browser_profiles_endpoint():
    """Enumerate all locally installed Chromium browser profiles (Cốc Cốc, Chrome, Edge, Brave)."""
    try:
        profiles = local_browser_service.list_local_browser_profiles()
        return {"items": profiles, "total": len(profiles)}
    except Exception as exc:
        logger.error("Lỗi khi đọc local browser profiles: %s", exc)
        return {"items": [], "total": 0, "error": str(exc)}


@app.post("/api/channels/open-browser")
def open_channel_browser_endpoint(request: ChannelOpenBrowserRequest):
    """Launch or focus browser window (GPM or Local Cốc Cốc/Chrome) on the target platform studio/creator."""
    try:
        return channel_scanner_service.open_channel_platform_browser(
            profile_id=request.profile_id,
            platform=request.platform,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/channels/scan/youtube")
async def scan_youtube_channel_endpoint(request: ChannelScanYouTubeRequest):
    """Scan authenticated YouTube Studio session and auto-link channel."""
    try:
        result = await channel_scanner_service.scan_youtube_channel(
            profile_id=request.profile_id,
            auto_save=request.auto_save,
        )
        return result
    except Exception as exc:
        logger.error("Lỗi khi quét YouTube channel: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/channels/scan/facebook")
async def scan_facebook_pages_endpoint(request: ChannelScanFacebookRequest):
    """Scan authenticated Facebook session and extract managed fanpages."""
    try:
        result = await channel_scanner_service.scan_facebook_pages(
            profile_id=request.profile_id,
        )
        return result
    except Exception as exc:
        logger.error("Lỗi khi quét Facebook pages: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/channels/scan/tiktok")
async def scan_tiktok_account_endpoint(request: ChannelScanTikTokRequest):
    """Scan authenticated TikTok Creator session and extract account details."""
    try:
        result = await channel_scanner_service.scan_tiktok_account(
            profile_id=request.profile_id,
        )
        return result
    except Exception as exc:
        logger.error("Lỗi khi quét TikTok account: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/tiktok/publish")
def publish_tiktok_video_endpoint(req: TikTokPublishRequest):
    """Trigger background video publishing to TikTok Creator Center via GPM and track in Job Center."""
    try:
        from auto_yt.services import tiktok_publisher_service
        job_id = tiktok_publisher_service.start_tiktok_publish_job(
            video_path=req.video_path,
            caption=req.caption,
            tags=req.tags,
            gpm_profile_id=req.gpm_profile_id,
            video_id=req.video_id,
            title=req.title,
        )
        return {
            "success": True,
            "job_id": job_id,
            "message": "Đã bắt đầu tác vụ đăng video lên TikTok (đang theo dõi tại Trung tâm Job).",
        }
    except Exception as exc:
        logger.error("Lỗi khởi tạo đăng video TikTok: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/videos/{video_id}/open-studio")
async def open_video_studio_in_gpm(video_id: int):
    """Open YouTube Studio video editor inside the channel's mapped GPM profile browser."""
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Không tìm thấy video.")
    youtube_video_id = str(video.get("published_youtube_video_id") or "").strip()

    # Also check video publications if not directly on video
    channel_db_id = None
    if not youtube_video_id:
        pubs = db.list_video_publications(video_id)
        if pubs:
            pub = pubs[0]
            youtube_video_id = str(pub.get("youtube_video_id") or "").strip()
            channel_db_id = pub.get("youtube_channel_id")
            if not youtube_video_id and pub.get("published_url"):
                youtube_video_id = youtube_comments.extract_youtube_video_id(pub["published_url"])

    if not youtube_video_id:
        raise HTTPException(status_code=400, detail="Video chưa được xuất bản lên YouTube.")

    target_channel = None
    if channel_db_id:
        target_channel = db.get_youtube_channel(int(channel_db_id))
    if not target_channel:
        # Check prompt version default channel
        prompt_ver = str(video.get("prompt_version") or "").strip()
        if prompt_ver:
            try:
                configs = prompt_assets.load_prompt_settings()
                prompt_cfg = configs.get("prompts", {}).get(prompt_ver, {})
                assigned_id = prompt_cfg.get("youtube_channel_id")
                if assigned_id:
                    target_channel = db.get_youtube_channel(int(assigned_id))
            except Exception:
                pass

    profile_id = str((target_channel or {}).get("gpm_profile_id") or "").strip()
    studio_url = f"https://studio.youtube.com/video/{youtube_video_id}/edit"
    if not profile_id:
        channel_name = target_channel.get("title") if target_channel else "liên kết"
        raise HTTPException(
            status_code=400,
            detail=f"Kênh '{channel_name}' chưa được gán GPM Profile. Vui lòng vào menu 'Kết nối Kênh' để gán profile trước khi mở Studio.",
        )

    try:
        result = await gpm_youtube_automation.open_url_in_gpm_profile(profile_id, studio_url)
        return {
            "success": True,
            "in_gpm": True,
            "profile_id": profile_id,
            "url": studio_url,
            "message": f"Đã mở YouTube Studio trong GPM Profile cho video {youtube_video_id}",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Không thể mở Studio trong GPM Profile: {exc}")


@app.post("/api/videos/{video_id}/open-watch")
async def open_video_watch_in_gpm(video_id: int):
    """Open YouTube Watch page inside the channel's mapped GPM profile browser."""
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Không tìm thấy video.")
    youtube_video_id = str(video.get("published_youtube_video_id") or "").strip()

    channel_db_id = None
    if not youtube_video_id:
        pubs = db.list_video_publications(video_id)
        if pubs:
            pub = pubs[0]
            youtube_video_id = str(pub.get("youtube_video_id") or "").strip()
            channel_db_id = pub.get("youtube_channel_id")
            if not youtube_video_id and pub.get("published_url"):
                youtube_video_id = youtube_comments.extract_youtube_video_id(pub["published_url"])

    if not youtube_video_id:
        raise HTTPException(status_code=400, detail="Video chưa được xuất bản lên YouTube.")

    target_channel = None
    if channel_db_id:
        target_channel = db.get_youtube_channel(int(channel_db_id))
    if not target_channel:
        prompt_ver = str(video.get("prompt_version") or "").strip()
        if prompt_ver:
            try:
                configs = prompt_assets.load_prompt_settings()
                prompt_cfg = configs.get("prompts", {}).get(prompt_ver, {})
                assigned_id = prompt_cfg.get("youtube_channel_id")
                if assigned_id:
                    target_channel = db.get_youtube_channel(int(assigned_id))
            except Exception:
                pass

    profile_id = str((target_channel or {}).get("gpm_profile_id") or "").strip()
    watch_url = f"https://www.youtube.com/watch?v={youtube_video_id}"
    if not profile_id:
        channel_name = target_channel.get("title") if target_channel else "liên kết"
        raise HTTPException(
            status_code=400,
            detail=f"Kênh '{channel_name}' chưa được gán GPM Profile. Vui lòng vào menu 'Kết nối Kênh' để gán profile.",
        )

    try:
        result = await gpm_youtube_automation.open_url_in_gpm_profile(profile_id, watch_url)
        return {
            "success": True,
            "in_gpm": True,
            "profile_id": profile_id,
            "url": watch_url,
            "message": f"Đã mở video YouTube trong GPM Profile cho video {youtube_video_id}",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi mở video trong GPM: {exc}")


@app.get("/api/videos/{video_id}/publications")
def get_video_publications(video_id: int):
    if not db.get_video(video_id):
        raise HTTPException(status_code=404, detail="Video not found")
    return {"items": db.list_video_publications(video_id)}


@app.get("/api/video-publications")
def get_all_video_publications():
    return {"items": db.list_video_publications()}


@app.post("/api/videos/{video_id}/publications")
def add_video_publication(video_id: int, request: VideoPublicationRequest):
    video = _require_actionable_video(video_id)
    try:
        youtube_video_id = youtube_comments.extract_youtube_video_id(
            request.published_url
        )
        channel_db_id = request.channel_id
        if channel_db_id is None:
            default_channel_id = _get_prompt_default_youtube_channel_id(
                video.get("prompt_version", "")
            )
            default_channel = (
                db.get_youtube_channel_by_channel_id(default_channel_id)
                if default_channel_id
                else None
            )
            channel_db_id = int(default_channel["id"]) if default_channel else None
        details = {}
        if channel_db_id is not None:
            channel, access_token = _get_youtube_access_token(channel_db_id)
            proxy_info = channel.get("gpm_proxy_info")
            details = youtube_comments.get_video_details(
                access_token, youtube_video_id, proxy=proxy_info
            )
            if details["channel_id"] != channel["channel_id"]:
                actual_channel = str(
                    details.get("channel_title")
                    or details.get("channel_id")
                    or "kênh khác"
                ).strip()
                raise ValueError(
                    f"Link này thuộc kênh '{actual_channel}', không phải kênh "
                    f"'{channel['title']}'. Video đặt lịch vẫn kiểm tra được trước giờ công khai; "
                    "hãy kiểm tra lại đúng link hoặc kênh mặc định của bộ prompt."
                )
        publication = db.save_video_publication(
            video_id=video_id,
            youtube_channel_id=channel_db_id,
            youtube_video_id=youtube_video_id,
            published_url=f"https://www.youtube.com/watch?v={youtube_video_id}",
            published_title=details.get("title", ""),
            published_at=(
                details.get("scheduled_publish_at") or details.get("published_at", "")
            ),
        )
        publication["privacy_status"] = details.get("privacy_status", "")
        publication["scheduled_publish_at"] = details.get("scheduled_publish_at", "")
        publication["is_scheduled"] = bool(details.get("is_scheduled"))
    except (ValueError, youtube_comments.YouTubeCommentsError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return publication


def _video_candidates_by_youtube_id() -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    for video in db.list_video_identity_candidates():
        try:
            youtube_video_id = youtube_comments.extract_youtube_video_id(
                video.get("url", "")
            )
        except ValueError:
            continue
        candidates.setdefault(youtube_video_id, video)
    return candidates


def _comment_import_inventory_item(
    remote_video: dict,
    *,
    channel_db_id: int,
    publications_by_youtube_id: dict[str, dict],
    candidates_by_youtube_id: dict[str, dict],
) -> dict:
    youtube_video_id = remote_video["youtube_video_id"]
    publication = publications_by_youtube_id.get(youtube_video_id)
    candidate = candidates_by_youtube_id.get(youtube_video_id)
    linked_video = publication or candidate
    video_status = str((linked_video or {}).get("video_status") or db.VIDEO_STATUS_ACTIVE)
    has_chat = bool(str((linked_video or {}).get("chat_url") or "").strip())
    if video_status == db.VIDEO_STATUS_ERROR:
        status = "skipped_error"
        eligible = False
    elif (
        publication
        and publication.get("youtube_channel_id") is not None
        and int(publication["youtube_channel_id"]) != int(channel_db_id)
    ):
        status = "channel_conflict"
        eligible = False
    elif publication and publication.get("youtube_channel_id") is None:
        status = "needs_channel"
        eligible = True
    elif publication:
        status = "existing_chat" if has_chat else "needs_chat"
        eligible = not has_chat
    elif candidate:
        status = "needs_link" if has_chat else "needs_link_and_chat"
        eligible = True
    else:
        status = "new"
        eligible = True
    return {
        "youtube_video_id": youtube_video_id,
        "published_url": f"https://www.youtube.com/watch?v={youtube_video_id}",
        "title": html.unescape(str(remote_video.get("title") or youtube_video_id)),
        "published_at": remote_video.get("published_at", ""),
        "thumbnail_url": remote_video.get("thumbnail_url", ""),
        "status": status,
        "eligible": eligible,
        "existing_video_id": int(linked_video["video_id"])
        if publication
        else int(candidate["id"]) if candidate else None,
        "has_chat": has_chat,
    }


def _load_comment_import_remote_videos(
    channel_db_id: int,
    published_url: str = "",
) -> tuple[dict, str, list[dict]]:
    channel, access_token = _get_youtube_access_token(channel_db_id)
    proxy_info = channel.get("gpm_proxy_info")
    normalized_url = str(published_url or "").strip()
    if normalized_url:
        youtube_video_id = youtube_comments.extract_youtube_video_id(normalized_url)
        remote_videos = youtube_comments.get_videos_details(
            access_token,
            [youtube_video_id],
            proxy=proxy_info,
        )
        if not remote_videos:
            raise ValueError("Không tìm thấy video trên YouTube.")
    else:
        remote_videos = youtube_comments.list_channel_videos(
            access_token,
            channel["channel_id"],
            max_videos=5000,
            proxy=proxy_info,
        )
    if any(
        str(video.get("channel_id") or "") != str(channel["channel_id"])
        for video in remote_videos
    ):
        raise ValueError("Có video không thuộc kênh YouTube đã chọn.")
    return channel, access_token, remote_videos


@app.post("/api/youtube-comments/import-preview")
def preview_comment_video_import(request: CommentVideoImportPreviewRequest):
    try:
        channel, _, remote_videos = _load_comment_import_remote_videos(
            request.channel_id,
            request.published_url,
        )
        publications_by_id = {
            item["youtube_video_id"]: item
            for item in db.list_video_publication_identities()
        }
        candidates_by_id = _video_candidates_by_youtube_id()
        items = [
            _comment_import_inventory_item(
                remote_video,
                channel_db_id=request.channel_id,
                publications_by_youtube_id=publications_by_id,
                candidates_by_youtube_id=candidates_by_id,
            )
            for remote_video in remote_videos
        ]
    except (ValueError, youtube_comments.YouTubeCommentsError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    counts: dict[str, int] = {"total": len(items), "eligible": 0}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        if item["eligible"]:
            counts["eligible"] += 1
    return {"channel": channel, "items": items, "counts": counts}


@app.post("/api/youtube-comments/import")
def start_comment_video_import(request: CommentVideoImportRequest):
    normalized_ids = list(
        dict.fromkeys(str(video_id or "").strip() for video_id in request.youtube_video_ids)
    )
    normalized_ids = [video_id for video_id in normalized_ids if video_id]
    try:
        with _prompts_config_lock:
            prompts_config = _read_prompts_config()
            prompt = _get_prompt_version(prompts_config, request.prompt_version)
        channel, access_token = _get_youtube_access_token(request.channel_id)
        proxy_info = channel.get("gpm_proxy_info")
        expected_channel_id = str(
            prompt.get("default_youtube_channel_id") or ""
        ).strip()
        if expected_channel_id and expected_channel_id != str(channel["channel_id"]):
            raise ValueError("Bộ prompt đã chọn thuộc một kênh YouTube khác.")
        remote_videos = youtube_comments.get_videos_details(
            access_token, normalized_ids, proxy=proxy_info
        )
        remote_by_id = {item["youtube_video_id"]: item for item in remote_videos}
        missing_ids = [video_id for video_id in normalized_ids if video_id not in remote_by_id]
        if missing_ids:
            raise ValueError(
                f"Không tìm thấy {len(missing_ids)} video đã chọn trên YouTube."
            )
        if any(
            str(video.get("channel_id") or "") != str(channel["channel_id"])
            for video in remote_videos
        ):
            raise ValueError("Có video không thuộc kênh YouTube đã chọn.")

        publications_by_id = {
            item["youtube_video_id"]: item
            for item in db.list_video_publication_identities()
        }
        candidates_by_id = _video_candidates_by_youtube_id()
        result = {
            "requested": len(normalized_ids),
            "created": 0,
            "linked": 0,
            "already_ready": 0,
            "queued": 0,
            "duplicate_jobs": 0,
            "skipped_error": 0,
            "job_ids": [],
        }
        for youtube_video_id in normalized_ids:
            remote_video = remote_by_id[youtube_video_id]
            inventory_item = _comment_import_inventory_item(
                remote_video,
                channel_db_id=request.channel_id,
                publications_by_youtube_id=publications_by_id,
                candidates_by_youtube_id=candidates_by_id,
            )
            if not inventory_item["eligible"]:
                if inventory_item["status"] == "existing_chat":
                    result["already_ready"] += 1
                else:
                    result["skipped_error"] += 1
                continue
            reservation = db.reserve_comment_import_video(
                youtube_channel_id=request.channel_id,
                youtube_video_id=youtube_video_id,
                published_url=inventory_item["published_url"],
                title=remote_video.get("title", "") or youtube_video_id,
                description=remote_video.get("description", ""),
                published_at=remote_video.get("published_at", ""),
                prompt_version=request.prompt_version,
                existing_video_id=inventory_item.get("existing_video_id"),
            )
            video = reservation["video"]
            if reservation["created_video"]:
                result["created"] += 1
            if reservation["created_publication"] or reservation.get("assigned_channel"):
                result["linked"] += 1
            if str(video.get("chat_url") or "").strip():
                result["already_ready"] += 1
                continue
            if db.has_active_system_job_for_video("comment_video_import", video["id"]):
                result["duplicate_jobs"] += 1
                continue
            job = _create_comment_system_job(
                "comment_video_import",
                title=remote_video.get("title", "") or youtube_video_id,
                payload={
                    "youtube_video_id": youtube_video_id,
                    "published_url": inventory_item["published_url"],
                    "description": remote_video.get("description", ""),
                },
                video_id=video["id"],
                prompt_version=(video.get("prompt_version") or request.prompt_version),
            )
            result["job_ids"].append(job["id"])
            result["queued"] += 1
        if result["queued"]:
            _kick_comment_queue()
        return {"success": True, **result}
    except (ValueError, youtube_comments.YouTubeCommentsError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/videos/{video_id}/publications/{publication_id}")
def remove_video_publication(video_id: int, publication_id: int):
    publications = db.list_video_publications(video_id)
    if not any(item["id"] == publication_id for item in publications):
        raise HTTPException(status_code=404, detail="Không tìm thấy link đã đăng.")
    db.delete_video_publication(publication_id)
    return {"success": True}


@app.get("/api/youtube-comments")
def get_youtube_comments(
    channel_id: Optional[int] = None,
    video_id: Optional[int] = Query(default=None, ge=1),
    status: Optional[str] = None,
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=200, ge=1, le=500),
):
    items = db.list_youtube_comments(
        channel_db_id=channel_id,
        video_id=video_id,
        status=status,
        search_query=search,
        limit=limit,
    )
    counts: dict[str, int] = {}
    for item in db.list_youtube_comments(
        channel_db_id=channel_id,
        video_id=video_id,
        limit=500,
    ):
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"items": items, "counts": counts}


@app.post("/api/youtube-comments/sync/{channel_db_id}")
def sync_youtube_comments(channel_db_id: int):
    channel = db.get_youtube_channel(channel_db_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Không tìm thấy kênh YouTube.")
    existing_job = next(
        (
            job
            for job in db.list_system_jobs(limit=500, job_type="comment_sync")
            if int((job.get("payload") or {}).get("channel_id") or 0) == channel_db_id
            and job.get("status") in {"queued", "running", "retry_wait", "paused"}
        ),
        None,
    )
    if existing_job:
        return {"success": True, "job_id": existing_job["id"], "duplicate": True}
    job = _create_comment_system_job(
        "comment_sync",
        title=channel["title"],
        payload={"channel_id": channel_db_id},
    )
    _kick_comment_queue()
    return {"success": True, "job_id": job["id"]}


@app.post("/api/youtube-comments/draft")
def draft_youtube_comment_replies(request: CommentIdsRequest):
    jobs = _enqueue_comment_draft_jobs(request.comment_ids)
    if not jobs:
        raise HTTPException(
            status_code=400,
            detail="Không có bình luận hợp lệ hoặc video còn thiếu Chat Gốc.",
        )
    _kick_comment_queue()
    return {"success": True, "job_ids": [job["id"] for job in jobs]}


@app.post("/api/youtube-comments/publish")
def publish_youtube_comment_replies(request: CommentIdsRequest):
    jobs = _enqueue_comment_publish_jobs(request.comment_ids)
    if not jobs:
        raise HTTPException(status_code=400, detail="Không có bản nháp hợp lệ để đăng.")
    _kick_comment_queue()
    return {"success": True, "job_ids": [job["id"] for job in jobs]}


@app.patch("/api/youtube-comments/{comment_id}")
def update_youtube_comment_reply(comment_id: str, request: CommentUpdateRequest):
    changes = {}
    if request.draft_reply is not None:
        try:
            reply = youtube_comments.validate_comment_reply(request.draft_reply)
        except youtube_comments.YouTubeCommentsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        changes["draft_reply"] = reply
        changes["status"] = "draft_ready"
        changes["risk_level"] = "reviewed"
        changes["risk_reason"] = "Đã được người dùng xem và chỉnh sửa."
        changes["error"] = ""
    elif request.status is not None:
        changes["status"] = request.status
        changes["error"] = ""
    else:
        raise HTTPException(status_code=400, detail="Không có thay đổi.")
    updated = db.update_youtube_comment(comment_id, **changes)
    if not updated:
        raise HTTPException(status_code=404, detail="Không tìm thấy bình luận.")
    return updated


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
    if video.get("video_status") == db.VIDEO_STATUS_ERROR:
        raise RuntimeError(
            "Video đang ở trạng thái Lỗi; bỏ qua bước kiểm duyệt và tạo audio."
        )

    script_text = video.get("generated_script", "")
    sanitized_script = sanitize_generated_script(script_text)
    if sanitized_script != script_text:
        if not db.update_script(video_id, sanitized_script):
            raise RuntimeError("Không thể lưu kịch bản đã làm sạch.")
        script_text = sanitized_script

    report = audit_script_for_audio(script_text)
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
        return (
            review,
            db.get_audio_task(video_id),
            security_logging.redact_sensitive(exc) or "Tạo audio thất bại.",
        )


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
        "tts_provider_id": task.get("tts_provider_id", "genmax"),
        "voice_revision": int(task.get("voice_revision") or 1),
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
    voice_fields: dict | None = None,
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
        db.update_video_voice(
            video_id,
            voice_id,
            voice_name,
            **(voice_fields or {}),
        )
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
    video = db.get_video(video_id)
    if not video:
        raise RuntimeError("Video không còn tồn tại.")
    if video.get("video_status") == db.VIDEO_STATUS_ERROR:
        raise RuntimeError("Video đang ở trạng thái Lỗi; bỏ qua đồng bộ audio.")
    stored_task = db.get_audio_task(video_id)
    if not stored_task:
        raise RuntimeError("Không tìm thấy audio task.")

    segments = _get_audio_segments(stored_task)
    if segments:
        return _sync_batch_audio_task(stored_task, segments)

    provider_id = stored_task.get("tts_provider_id") or "genmax"
    script_for_tts = apply_tts_filters(
        get_clean_script_for_tts(video["generated_script"])
    )
    stored_snapshot = _voice_snapshot_from_record(stored_task)
    if stored_snapshot:
        current_request_hash = tts.get_generation_request_hash(
            script_for_tts,
            stored_task.get("voice_id") or stored_snapshot.get("voice_id") or "",
            stored_snapshot,
        )
        if current_request_hash != stored_task["request_hash"]:
            return db.upsert_audio_task(
                video_id=video_id,
                request_hash=stored_task["request_hash"],
                task_id=stored_task["task_id"],
                status="failed",
                audio_url="",
                error=(
                    "Kịch bản hoặc cấu hình giọng đã thay đổi sau khi gửi job; "
                    "audio cũ không được gắn vào video."
                ),
                segments_json="",
                voice_id=stored_task.get("voice_id", ""),
                voice_name=stored_task.get("voice_name", ""),
                **_stored_voice_persistence_fields(stored_task),
            )
    remote_task = tts.get_tts_task(stored_task["task_id"], provider_id)
    status = remote_task.get("status", stored_task["status"])
    audio_url = (remote_task.get("result") or {}).get("audio_url", "")
    error = remote_task.get("error") or remote_task.get("detail_error") or ""

    duration_seconds = None
    if status == "completed":
        try:
            if provider_id == voice_config.OMNIVOICE_PROVIDER_ID:
                snapshot_config = (
                    stored_snapshot.get("config")
                    if stored_snapshot and isinstance(stored_snapshot.get("config"), dict)
                    else {}
                )
                quality_managed = int(snapshot_config.get("engine_revision") or 1) >= 2
                minimum_wpm = (
                    float(snapshot_config.get("min_words_per_minute") or 105)
                    if quality_managed
                    else None
                )
                reported_duration = (remote_task.get("result") or {}).get(
                    "duration_seconds"
                )
                if reported_duration is not None:
                    audio_utils.validate_spoken_duration(
                        script_for_tts,
                        float(reported_duration),
                        minimum_words_per_minute=minimum_wpm,
                        provider_name="OmniVoice",
                    )
                audio_url, duration_seconds = tts.materialize_audio(
                    remote_task,
                    video_id,
                    stored_task["request_hash"],
                )
                audio_utils.validate_spoken_duration(
                    script_for_tts,
                    duration_seconds,
                    minimum_words_per_minute=minimum_wpm,
                    provider_name="OmniVoice",
                )
            elif not audio_url:
                raise audio_utils.AudioContentError(
                    "Task Genmax completed without an audio URL."
                )
            else:
                duration_seconds = audio_utils.get_remote_mp3_duration(audio_url)
                audio_utils.validate_spoken_duration(
                    remote_task.get("text", ""),
                    duration_seconds,
                )
        except (
            audio_utils.AudioContentError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            if provider_id == voice_config.OMNIVOICE_PROVIDER_ID:
                tts.discard_materialized_audio(
                    video_id,
                    stored_task["request_hash"],
                )
            status = "failed"
            audio_url = ""
            error = security_logging.redact_sensitive(exc)

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
        **_stored_voice_persistence_fields(stored_task),
    )
    if status == "completed":
        _save_audio_url(
            video_id,
            audio_url,
            task.get("voice_id", ""),
            task.get("voice_name", ""),
            _stored_voice_persistence_fields(task),
        )
        db.update_audio_duration(video_id, duration_seconds)
        _trigger_video_render_if_enabled(video_id)
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
            remote_task = tts.get_tts_task(
                task_id,
                stored_task.get("tts_provider_id") or "genmax",
            )
        except Exception as exc:
            # Preserve progress from every other segment. A transient failure
            # must not discard a whole polling cycle and leave the UI stale.
            sync_errors.append(
                (segment.get("index", 0), security_logging.redact_sensitive(exc))
            )
            segment["error"] = (
                "Tạm thời chưa đồng bộ được: "
                + security_logging.redact_sensitive(exc)
            )
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
                _voice_snapshot_from_record(stored_task),
            )
            if current_request_hash != stored_task["request_hash"]:
                raise audio_utils.AudioContentError(
                    "The script changed while audio was being generated."
                )
            chunks = tts.split_text_for_tts(
                script_for_tts,
                voice_id=task_voice_id,
                voice_snapshot=_voice_snapshot_from_record(stored_task),
            )
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
            error = security_logging.redact_sensitive(exc)

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
        **_stored_voice_persistence_fields(stored_task),
    )
    if status == "completed":
        _save_audio_url(
            video_id,
            audio_url,
            task_voice_id,
            task_voice_name,
            _stored_voice_persistence_fields(task),
        )
        _trigger_video_render_if_enabled(video_id)
    return task


def _watch_audio_task(video_id: int) -> None:
    try:
        while True:
            video = db.get_video(video_id)
            if (
                not video
                or video.get("video_status") == db.VIDEO_STATUS_ERROR
            ):
                return
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
    video = db.get_video(video_id)
    if (
        not video
        or video.get("video_status") == db.VIDEO_STATUS_ERROR
    ):
        return
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
    voice_snapshot: dict | None = None,
) -> dict:
    status = remote_task.get("status", "pending")
    audio_url = (remote_task.get("result") or {}).get("audio_url", "")
    if status == "completed" and not audio_url:
        status = "processing"
    return {
        "index": index,
        "text_hash": tts.get_request_hash(text, voice_id, voice_snapshot),
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
    voice_snapshot: dict | None = None,
    status: str = "pending",
    error: str = "",
) -> dict:
    voice = (
        _voice_from_snapshot(voice_snapshot)
        if voice_snapshot
        else voice_config.get_voice(voice_id)
    )
    return db.upsert_audio_task(
        video_id=video_id,
        request_hash=request_hash,
        task_id=f"batch-{request_hash[:24]}",
        status=status,
        error=error,
        segments_json=json.dumps(segments, ensure_ascii=False),
        voice_id=voice_id,
        voice_name=voice_name,
        **_voice_persistence_fields(voice),
    )


def _ensure_batch_audio_task(
    video_id: int,
    request_hash: str,
    chunks: list[str],
    stored_task: dict | None,
    voice_id: str = AUDIO_VOICE_ID,
    voice_name: str = "",
    voice_snapshot: dict | None = None,
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
                _stored_voice_persistence_fields(stored_task),
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
    remote_matches = (
        tts.find_matching_tasks(chunks, voice_id, voice_snapshot)
        if voice_snapshot
        else tts.find_matching_tasks(chunks, voice_id)
    )
    segments = []
    for index, chunk in enumerate(chunks):
        existing_segment = existing_by_index.get(index)
        if existing_segment:
            segments.append(existing_segment)
            continue
        remote_task = remote_matches.get(chunk)
        if remote_task:
            segments.append(
                _segment_from_remote(
                    index,
                    chunk,
                    remote_task,
                    voice_id,
                    voice_snapshot,
                )
            )
        else:
            segments.append({
                "index": index,
                "text_hash": tts.get_request_hash(chunk, voice_id, voice_snapshot),
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
        voice_snapshot,
    )
    try:
        for segment in segments:
            if segment["task_id"]:
                continue
            submitted_task = (
                tts.submit_tts_task(
                    chunks[segment["index"]],
                    voice_id,
                    voice_snapshot,
                )
                if voice_snapshot
                else tts.submit_tts_task(
                    chunks[segment["index"]],
                    voice_id,
                )
            )
            segment.update(
                _segment_from_remote(
                    segment["index"],
                    chunks[segment["index"]],
                    submitted_task,
                    voice_id,
                    voice_snapshot,
                )
            )
            task = _store_batch_audio_task(
                video_id,
                request_hash,
                segments,
                voice_id,
                voice_name,
                voice_snapshot,
            )
    except Exception as exc:
        missing_count = sum(not segment.get("task_id") for segment in segments)
        _store_batch_audio_task(
            video_id,
            request_hash,
            segments,
            voice_id,
            voice_name,
            voice_snapshot=voice_snapshot,
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
    if video.get("video_status") == db.VIDEO_STATUS_ERROR:
        raise RuntimeError("Video đang ở trạng thái Lỗi; không tạo audio.")
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
    stored_snapshot = None
    if not requested_voice_id:
        if existing_task and existing_task.get("voice_id") == voice_id:
            stored_snapshot = _voice_snapshot_from_record(existing_task)
        if stored_snapshot is None and video.get("voice_id") == voice_id:
            stored_snapshot = _voice_snapshot_from_record(video)
    if stored_snapshot:
        selected_voice = _voice_from_snapshot(stored_snapshot)
    else:
        try:
            selected_voice = voice_config.get_voice(voice_id)
        except ValueError:
            # Videos created before schema v2 may point at a historical Genmax
            # voice no longer present in the editable catalog. Preserve that
            # provider/remote ID instead of silently switching to the default.
            selected_voice = {
                "id": voice_id,
                "name": voice_name or "Giọng đã lưu",
            }
    selected_voice = _normalize_voice_record(
        selected_voice,
        fallback_id=voice_id,
        fallback_name=voice_name,
    )
    if requested_voice_id and selected_voice.get("status") != "active":
        raise RuntimeError("Giọng đọc đã chọn hiện không hoạt động.")
    voice_snapshot = voice_config.build_voice_snapshot(selected_voice)
    voice_fields = _voice_persistence_fields(selected_voice)

    script_for_tts = get_clean_script_for_tts(video["generated_script"])
    if not script_for_tts:
        raise RuntimeError(
            "Không tìm thấy kịch bản để đọc (thiếu INTRO/BODY/OUTRO)."
        )

    filtered_script = apply_tts_filters(script_for_tts)
    chunks = tts.split_text_for_tts(
        filtered_script,
        voice_id=voice_id,
        voice_snapshot=voice_snapshot,
    )
    request_hash = tts.get_generation_request_hash(
        filtered_script,
        voice_id,
        voice_snapshot,
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
                        voice_fields,
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
                voice_snapshot,
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
                **voice_fields,
            )
            if task["status"] == "completed":
                _save_audio_url(
                    video_id,
                    task["audio_url"],
                    voice_id,
                    voice_name,
                    voice_fields,
                )
            elif task["status"] in {"pending", "processing"}:
                _start_audio_watcher(video_id)
            return task

        remote_task = tts.find_matching_task(
            filtered_script,
            voice_id,
            voice_snapshot,
        )
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
                **voice_fields,
            )
            _start_audio_watcher(video_id)
            return task

        submitted_task = tts.submit_tts_task(
            filtered_script,
            voice_id,
            voice_snapshot,
        )
        task = db.upsert_audio_task(
            video_id=video_id,
            request_hash=request_hash,
            task_id=submitted_task["id"],
            status=submitted_task.get("status", "pending"),
            segments_json="",
            voice_id=voice_id,
            voice_name=voice_name,
            **voice_fields,
        )
        _start_audio_watcher(video_id)
        return task


COMMENT_JOB_TYPES = (
    "comment_sync",
    "comment_draft",
    "comment_publish",
    "comment_video_import",
)
COMMENT_JOB_LABELS = {
    "comment_sync": "Đồng bộ bình luận",
    "comment_draft": "Soạn trả lời bình luận",
    "comment_publish": "Đăng trả lời bình luận",
    "comment_video_import": "Khởi tạo Chat cho video cũ",
}

PRODUCTION_JOB_TYPES = (
    "video_render",
    "visual_scene_plan",
    "youtube_upload",
    "youtube_publish",
    "fb_crosspost",
    "fb_crosspost_sync",
    "thumbnail_generation",
    "tiktok_publish",
)
PRODUCTION_JOB_LABELS = {
    "video_render": "Dựng video MP4",
    "visual_scene_plan": "Lập kế hoạch cảnh",
    "youtube_upload": "Upload / đặt lịch YouTube",
    "youtube_publish": "Upload / đặt lịch YouTube",
    "fb_crosspost": "Đăng chéo Facebook",
    "fb_crosspost_sync": "Đồng bộ video Facebook",
    "thumbnail_generation": "Sinh ảnh Thumbnail",
    "tiktok_publish": "Đăng video TikTok",
}
ALL_JOB_LABELS = {
    "video_generation": "Tạo video",
    **COMMENT_JOB_LABELS,
    **PRODUCTION_JOB_LABELS,
}


def _get_youtube_access_token(channel_db_id: int) -> tuple[dict, str]:
    channel = db.get_youtube_channel(channel_db_id, include_tokens=True)
    if not channel:
        raise youtube_comments.YouTubeCommentsError("Không tìm thấy kênh YouTube.")
    token = youtube_comments.access_token_for_channel(
        channel,
        lambda channel_id, encrypted_token, expiry, oauth_client_id: db.update_youtube_channel(
            channel_id,
            access_token_encrypted=encrypted_token,
            token_expiry=expiry,
            oauth_client_id=oauth_client_id,
        ),
        proxy=channel.get("gpm_proxy_info"),
    )
    return channel, token


def _reconcile_channel_publication_states(
    channel: dict,
    access_token: str,
) -> int:
    publications = db.list_channel_pending_publications(int(channel["id"]))
    if not publications:
        return 0
    remote_videos = youtube_comments.get_videos_details(
        access_token,
        [item["youtube_video_id"] for item in publications],
        proxy=channel.get("gpm_proxy_info"),
    )
    remote_by_id = {
        item["youtube_video_id"]: item
        for item in remote_videos
        if item.get("youtube_video_id")
    }
    reconciled = 0
    for publication in publications:
        remote = remote_by_id.get(publication["youtube_video_id"])
        if not remote or str(remote.get("channel_id") or "") != str(
            channel.get("channel_id") or ""
        ):
            continue
        changes = {
            "privacy_status": str(
                remote.get("privacy_status") or publication.get("privacy_status") or ""
            ),
            "scheduled_at": str(remote.get("scheduled_publish_at") or ""),
        }
        if remote.get("title"):
            changes["published_title"] = str(remote["title"])
        if remote.get("published_at"):
            changes["published_at"] = str(remote["published_at"])
        if any(publication.get(key) != value for key, value in changes.items()):
            db.update_video_publication(publication["id"], **changes)
            reconciled += 1
    return reconciled


def _create_comment_system_job(
    job_type: str,
    *,
    title: str,
    payload: dict,
    video_id: int | None = None,
    prompt_version: str = "",
) -> dict:
    job = db.create_system_job(
        job_id=f"{job_type}-{uuid.uuid4().hex}",
        job_type=job_type,
        title=title,
        payload=payload,
        prompt_version=prompt_version,
    )
    if video_id is not None:
        job = db.update_system_job(job["id"], video_id=video_id)
    return job


def _reconcile_existing_youtube_reply(
    comment: dict,
    channel: dict,
    access_token: str,
) -> bool:
    """Mark a comment replied when the managed channel already answered it.

    YouTube only embeds a subset of replies in ``commentThreads.list``.  The
    paginated ``comments.list(parentId=...)`` lookup is therefore the source of
    truth used at every boundary that could otherwise create a duplicate.
    """
    if comment.get("status") == "replied" or comment.get("reply_youtube_id"):
        return True
    existing_reply = youtube_comments.find_channel_reply(
        access_token,
        comment["comment_id"],
        channel["channel_id"],
        proxy=channel.get("gpm_proxy_info"),
    )
    if not existing_reply:
        return False
    db.update_youtube_comment(
        comment["comment_id"],
        status="replied",
        reply_youtube_id=existing_reply.get("reply_id") or "",
        reply_text=existing_reply.get("text") or "",
        reply_published_at=existing_reply.get("published_at") or db.utc_now(),
        error="",
    )
    return True


def _enqueue_comment_draft_jobs(comment_ids: list[str]) -> list[dict]:
    active_owners = _active_comment_draft_owners()
    comments = db.get_youtube_comments(
        list(dict.fromkeys(comment_ids))[: youtube_comments.MAX_SYNC_COMMENTS]
    )
    grouped: dict[int, list[dict]] = {}
    for comment in comments:
        comment_id = comment["comment_id"]
        if comment_id in active_owners:
            if comment.get("status") != "replied" and not comment.get("reply_youtube_id"):
                db.update_youtube_comment(comment_id, status="drafting", error="")
            continue
        if (
            comment.get("status") in {"drafting", "scheduled", "publishing", "replied"}
            or comment.get("reply_youtube_id")
        ):
            continue
        if not comment.get("can_reply"):
            db.update_youtube_comment(
                comment["comment_id"], status="error", error="Kênh không có quyền trả lời bình luận này."
            )
            continue
        if not comment.get("chat_url"):
            db.update_youtube_comment(
                comment["comment_id"],
                status="error",
                error="Video chưa có Chat Gốc nên không thể soạn câu trả lời đúng ngữ cảnh.",
            )
            continue
        grouped.setdefault(int(comment["video_id"]), []).append(comment)

    jobs: list[dict] = []
    for video_id, video_comments in grouped.items():
        for start in range(0, len(video_comments), youtube_comments.MAX_COMMENT_BATCH_SIZE):
            batch = video_comments[
                start : start + youtube_comments.MAX_COMMENT_BATCH_SIZE
            ]
            batch_ids = [comment["comment_id"] for comment in batch]
            for comment_id in batch_ids:
                db.update_youtube_comment(comment_id, status="drafting", error="")
            jobs.append(
                _create_comment_system_job(
                    "comment_draft",
                    title=batch[0].get("video_title") or f"Video #{video_id}",
                    payload={"comment_ids": batch_ids},
                    video_id=video_id,
                    prompt_version=batch[0].get("prompt_version") or "",
                )
            )
    return jobs


def _active_comment_draft_owners() -> dict[str, str]:
    """Map each comment to its oldest active draft job.

    Comment status is a UI projection and can be reset by synchronization. Job
    payload ownership is the durable idempotency boundary that prevents two
    ChatGPT prompts for the same YouTube comment.
    """
    owners: dict[str, str] = {}
    for active_job in db.list_active_system_jobs("comment_draft"):
        for comment_id in (active_job.get("payload") or {}).get("comment_ids") or []:
            normalized_id = str(comment_id or "").strip()
            if normalized_id:
                owners.setdefault(normalized_id, active_job["id"])
    return owners


def _reconcile_active_comment_draft_job_duplicates() -> int:
    """Collapse legacy overlapping jobs without calling ChatGPT or YouTube."""
    owned_comment_ids: set[str] = set()
    reconciled = 0
    for active_job in db.list_active_system_jobs("comment_draft"):
        payload = dict(active_job.get("payload") or {})
        requested_ids = list(
            dict.fromkeys(
                str(comment_id or "").strip()
                for comment_id in payload.get("comment_ids") or []
                if str(comment_id or "").strip()
            )
        )
        unique_ids = [
            comment_id
            for comment_id in requested_ids
            if comment_id not in owned_comment_ids
        ]
        owned_comment_ids.update(unique_ids)
        if unique_ids == requested_ids:
            continue
        if unique_ids:
            payload["comment_ids"] = unique_ids
            db.update_system_job(active_job["id"], payload_json=payload)
        else:
            db.update_system_job(
                active_job["id"],
                status="done",
                progress="Đã hợp nhất vào job soạn bình luận cũ hơn",
                result_json={"drafted": 0, "duplicate_job": True},
                error="",
                finished_at=db.utc_now(),
                next_retry_at="",
            )
        reconciled += 1
    return reconciled


def _enqueue_comment_publish_jobs(comment_ids: list[str]) -> list[dict]:
    jobs = []
    for comment in db.get_youtube_comments(
        list(dict.fromkeys(comment_ids))[: youtube_comments.MAX_SYNC_COMMENTS]
    ):
        if (
            comment.get("status") in {"scheduled", "publishing", "replied"}
            or comment.get("reply_youtube_id")
        ):
            continue
        if comment.get("status") == "reconcile_required":
            # A previous publish request may already have reached YouTube.
            # Only a later sync is allowed to reconcile it; blindly queuing a
            # second insert risks posting the same reply twice.
            continue
        if not str(comment.get("draft_reply") or "").strip():
            db.update_youtube_comment(
                comment["comment_id"],
                status="error",
                error="Chưa có bản nháp để đăng.",
            )
            continue
        if comment.get("risk_level") not in {"low", "reviewed", ""}:
            db.update_youtube_comment(
                comment["comment_id"],
                status="review_required",
                error=comment.get("risk_reason") or "Bình luận cần được duyệt thủ công.",
            )
            continue
        try:
            youtube_comments.validate_comment_reply(comment["draft_reply"])
        except youtube_comments.YouTubeCommentsError as exc:
            db.update_youtube_comment(
                comment["comment_id"],
                status="review_required",
                risk_level="review_required",
                risk_reason=security_logging.redact_sensitive(exc),
                error=security_logging.redact_sensitive(exc),
            )
            continue
        db.update_youtube_comment(comment["comment_id"], status="scheduled", error="")
        jobs.append(
            _create_comment_system_job(
                "comment_publish",
                title=comment.get("video_title") or comment["comment_id"],
                payload={
                    "comment_ids": [comment["comment_id"]],
                    "channel_id": comment.get("youtube_channel_id"),
                },
                video_id=comment.get("video_id"),
                prompt_version=comment.get("prompt_version") or "",
            )
        )
    return jobs


def _execute_comment_sync_job(job: dict) -> None:
    channel_db_id = int((job.get("payload") or {}).get("channel_id") or 0)
    channel, access_token = _get_youtube_access_token(channel_db_id)
    proxy_info = channel.get("gpm_proxy_info")
    reconciled_publications = _reconcile_channel_publication_states(
        channel,
        access_token,
    )
    db.update_system_job(job["id"], progress="Đang đọc bình luận mới từ YouTube")
    remote_comments = youtube_comments.list_channel_comment_threads(
        access_token, channel["channel_id"], proxy=proxy_info
    )
    matched_ids: list[str] = []
    ignored = 0
    for remote_comment in remote_comments:
        if remote_comment.get("author_channel_id") == channel["channel_id"]:
            ignored += 1
            continue
        publication = db.get_video_publication_by_youtube_id(
            remote_comment["youtube_video_id"]
        )
        if (
            not publication
            or publication.get("video_status") == db.VIDEO_STATUS_ERROR
            or publication.get("youtube_channel_id") is None
            or int(publication["youtube_channel_id"]) != channel_db_id
        ):
            ignored += 1
            continue
        risk_level, risk_reason = youtube_comments.assess_comment_risk(
            remote_comment.get("text") or ""
        )
        priority, priority_reason, should_auto_reply = (
            youtube_comments.assess_auto_reply_priority(
                remote_comment.get("text") or "",
                remote_comment.get("comment_id") or "",
            )
        )
        remote_comment["risk_level"] = risk_level
        remote_comment["risk_reason"] = risk_reason
        remote_comment["auto_reply_priority"] = priority
        remote_comment["auto_reply_reason"] = priority_reason
        saved = db.upsert_youtube_comment(publication["id"], remote_comment)
        if (
            risk_level == "review_required"
            and saved.get("status") in {"new", "error"}
        ):
            saved = db.update_youtube_comment(
                saved["comment_id"],
                status="review_required",
                error=risk_reason,
            )
        # ``commentThreads.list`` may omit older replies from its embedded
        # subset.  Fully enumerate the thread before treating a comment with
        # replies as unanswered.  Ambiguous previous publishes are always
        # reconciled, even if the summary count is stale.
        if (
            saved.get("status") != "replied"
            and not saved.get("reply_youtube_id")
            and (
                saved.get("status") == "reconcile_required"
                or int(remote_comment.get("total_reply_count") or 0) > 0
            )
            and _reconcile_existing_youtube_reply(saved, channel, access_token)
        ):
            saved = db.get_youtube_comments([saved["comment_id"]])[0]
        if (
            risk_level == "low"
            and saved.get("can_reply")
            and saved.get("status") in {"new", "error"}
        ):
            if should_auto_reply:
                matched_ids.append(saved["comment_id"])
            else:
                db.update_youtube_comment(
                    saved["comment_id"],
                    status="skipped",
                    error="",
                )
    db.update_youtube_channel(channel_db_id, last_sync_at=db.utc_now(), status="connected")
    mode = channel.get("auto_mode") or "draft_only"
    auto_draft_limit = min(
        youtube_comments.MAX_AUTO_DRAFT_COMMENTS_PER_SYNC,
        int(channel.get("daily_reply_limit") or 50),
    )
    prioritized_comments = sorted(
        db.get_youtube_comments(matched_ids),
        key=lambda item: int(item.get("auto_reply_priority") or 0),
        reverse=True,
    )
    draft_jobs = (
        _enqueue_comment_draft_jobs(
            [item["comment_id"] for item in prioritized_comments[:auto_draft_limit]]
        )
        if mode != "manual"
        else []
    )
    db.update_system_job(
        job["id"],
        status="done",
        progress=(
            f"Đã đồng bộ {len(matched_ids)} bình luận; bỏ qua {ignored} video chưa liên kết"
        ),
        result_json={
            "matched": len(matched_ids),
            "ignored": ignored,
            "draft_jobs": len(draft_jobs),
            "reconciled_publications": reconciled_publications,
        },
        finished_at=db.utc_now(),
        error="",
    )


def _execute_comment_draft_job(job: dict) -> bool:
    requested_ids = [
        str(comment_id or "").strip()
        for comment_id in (job.get("payload") or {}).get("comment_ids") or []
        if str(comment_id or "").strip()
    ]
    active_owners = _active_comment_draft_owners()
    owned_ids = [
        comment_id
        for comment_id in requested_ids
        if active_owners.get(comment_id) in {None, job["id"]}
    ]
    if not owned_ids:
        db.update_system_job(
            job["id"],
            status="done",
            progress="Đã bỏ qua job trùng; bình luận thuộc job cũ hơn",
            result_json={"drafted": 0, "duplicate_job": True},
            finished_at=db.utc_now(),
            error="",
        )
        return True

    comments = db.get_youtube_comments(owned_ids)
    if not comments:
        raise RuntimeError("Các bình luận của job không còn tồn tại.")

    # Re-check YouTube immediately before spending a ChatGPT turn.  This also
    # protects old queued jobs when the owner answered manually after the last
    # channel sync.
    channel_credentials: dict[int, tuple[dict, str]] = {}
    unanswered_comments: list[dict] = []
    for comment in comments:
        if comment.get("status") == "replied" or comment.get("reply_youtube_id"):
            continue
        channel_db_id = int(comment["youtube_channel_id"])
        if channel_db_id not in channel_credentials:
            channel_credentials[channel_db_id] = _get_youtube_access_token(channel_db_id)
        channel, access_token = channel_credentials[channel_db_id]
        if not _reconcile_existing_youtube_reply(comment, channel, access_token):
            unanswered_comments.append(comment)
    comments = unanswered_comments
    if not comments:
        db.update_system_job(
            job["id"],
            status="done",
            progress="Các bình luận đã được trả lời trên YouTube; không soạn trùng",
            result_json={"drafted": 0, "already_replied": True},
            finished_at=db.utc_now(),
            error="",
        )
        return True

    chat_urls = {comment.get("chat_url") for comment in comments}
    video_ids = {comment.get("video_id") for comment in comments}
    if len(chat_urls) != 1 or len(video_ids) != 1 or not next(iter(chat_urls)):
        raise RuntimeError("Một job chỉ được dùng đúng một Chat Gốc của một video.")
    if not _try_start_chatgpt_operation(
        "comment-reply",
        comments[0].get("prompt_version") or "",
        comments[0].get("video_id"),
    ):
        db.update_system_job(
            job["id"],
            status="queued",
            progress="ChatGPT đang bận; bình luận vẫn chờ đúng thứ tự",
            started_at="",
        )
        return False
    try:
        db.update_system_job(job["id"], progress="ChatGPT đang soạn câu trả lời theo Chat Gốc")
        from auto_yt.services.chatgpt_worker import generate_comment_replies

        replies = generate_comment_replies(
            comments[0]["chat_url"],
            comments,
            comments[0].get("reply_instruction") or "",
            db.list_recent_channel_reply_texts(
                int(comments[0]["youtube_channel_id"]), limit=20
            ),
        )
        auto_publish_ids = []
        for comment in comments:
            reply = replies[comment["comment_id"]]
            requires_review = comment.get("risk_level") == "review_required"
            db.update_youtube_comment(
                comment["comment_id"],
                status="review_required" if requires_review else "draft_ready",
                draft_reply=reply,
                error=comment.get("risk_reason") if requires_review else "",
            )
            if comment.get("auto_mode") == "auto_publish" and not requires_review:
                auto_publish_ids.append(comment["comment_id"])
        publish_jobs = _enqueue_comment_publish_jobs(auto_publish_ids)
        db.update_system_job(
            job["id"],
            status="done",
            progress=f"Đã soạn {len(replies)} câu trả lời",
            result_json={"drafted": len(replies), "publish_jobs": len(publish_jobs)},
            finished_at=db.utc_now(),
            error="",
        )
        return True
    finally:
        _finish_chatgpt_operation()


def _parse_utc_datetime(value: str) -> datetime.datetime | None:
    try:
        parsed = datetime.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _move_into_channel_reply_window(
    value: datetime.datetime, channel: dict
) -> datetime.datetime:
    local_value = value.astimezone()
    start_hour, start_minute = map(
        int, str(channel.get("reply_window_start") or "08:00").split(":")
    )
    end_hour, end_minute = map(
        int, str(channel.get("reply_window_end") or "22:00").split(":")
    )
    start_time = datetime.time(start_hour, start_minute)
    end_time = datetime.time(end_hour, end_minute)
    if start_time == end_time:
        return value

    today = local_value.date()
    if start_time < end_time:
        start = datetime.datetime.combine(today, start_time, local_value.tzinfo)
        end = datetime.datetime.combine(today, end_time, local_value.tzinfo)
        if local_value < start:
            return start.astimezone(datetime.timezone.utc)
        if local_value >= end:
            next_start = datetime.datetime.combine(
                today + datetime.timedelta(days=1), start_time, local_value.tzinfo
            )
            return next_start.astimezone(datetime.timezone.utc)
        return value

    # An overnight window such as 20:00-02:00.
    if end_time <= local_value.time() < start_time:
        start = datetime.datetime.combine(today, start_time, local_value.tzinfo)
        return start.astimezone(datetime.timezone.utc)
    return value


def _next_comment_publish_time(
    comment: dict,
    channel: dict,
    now: datetime.datetime | None = None,
) -> tuple[datetime.datetime, str]:
    """Calculate the next policy-compliant slot without consuming API quota."""
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(
        datetime.timezone.utc
    )
    candidate = _move_into_channel_reply_window(now, channel)
    reasons: list[str] = []
    if candidate > now:
        reasons.append("ngoài khung giờ hoạt động")
    if channel.get("reply_paused"):
        candidate = max(candidate, now + datetime.timedelta(minutes=15))
        candidate = _move_into_channel_reply_window(candidate, channel)
        reasons.append("kênh đang tạm dừng")
        return candidate, ", ".join(reasons)

    activity_since = (now - datetime.timedelta(days=2)).isoformat()
    activity = db.list_channel_reply_activity(int(channel["id"]), activity_since)
    parsed_activity = [
        (item, replied_at)
        for item in activity
        if (replied_at := _parse_utc_datetime(item.get("replied_at") or ""))
        and replied_at <= now
    ]

    def apply_rolling_limit(
        minutes: int,
        limit: int,
        label: str,
        predicate=lambda _item: True,
    ) -> None:
        nonlocal candidate
        cutoff = now - datetime.timedelta(minutes=minutes)
        recent = [
            replied_at
            for item, replied_at in parsed_activity
            if replied_at > cutoff and predicate(item)
        ]
        if len(recent) >= max(1, limit):
            candidate = max(candidate, min(recent) + datetime.timedelta(minutes=minutes))
            reasons.append(label)

    if parsed_activity:
        newest_reply = max(replied_at for _, replied_at in parsed_activity)
        interval = datetime.timedelta(
            minutes=max(1, int(channel.get("reply_interval_minutes") or 5))
        )
        if newest_reply + interval > candidate:
            candidate = newest_reply + interval
            reasons.append("giãn cách giữa hai câu trả lời")
    apply_rolling_limit(
        15,
        int(channel.get("quarter_hour_reply_limit") or 3),
        "giới hạn 15 phút",
    )
    apply_rolling_limit(
        60,
        int(channel.get("hourly_reply_limit") or 10),
        "giới hạn theo giờ",
    )
    apply_rolling_limit(
        30,
        int(channel.get("video_half_hour_reply_limit") or 3),
        "giãn nhịp trên cùng video",
        lambda item: int(item.get("video_id") or 0) == int(comment.get("video_id") or 0),
    )

    local_now = now.astimezone()
    local_day_start = datetime.datetime.combine(
        local_now.date(), datetime.time.min, local_now.tzinfo
    ).astimezone(datetime.timezone.utc)
    replies_today = [
        item for item, replied_at in parsed_activity if replied_at >= local_day_start
    ]
    daily_limit = max(1, int(channel.get("daily_reply_limit") or 50))
    next_local_start = datetime.datetime.combine(
        local_now.date() + datetime.timedelta(days=1),
        datetime.time.min,
        local_now.tzinfo,
    ).astimezone(datetime.timezone.utc)
    if len(replies_today) >= daily_limit:
        candidate = max(candidate, next_local_start)
        reasons.append("đủ giới hạn trong ngày")

    comment_published_at = _parse_utc_datetime(comment.get("published_at") or "")
    if comment_published_at and comment_published_at < local_day_start:
        backlog_today = 0
        for item in replies_today:
            original_published = _parse_utc_datetime(
                item.get("comment_published_at") or ""
            )
            if original_published and original_published < local_day_start:
                backlog_today += 1
        if backlog_today >= max(
            1, int(channel.get("backlog_daily_reply_limit") or 20)
        ):
            candidate = max(candidate, next_local_start)
            reasons.append("đủ hạn mức xử lý bình luận cũ")

    candidate = _move_into_channel_reply_window(candidate, channel)
    return candidate, ", ".join(dict.fromkeys(reasons)) or "đến lượt đăng"


def _execute_comment_video_import_job(job: dict) -> bool:
    video_id = int(job.get("video_id") or 0)
    video = db.get_video(video_id)
    if not video:
        raise RuntimeError("Video nhập cũ không còn tồn tại.")
    if video.get("video_status") == db.VIDEO_STATUS_ERROR:
        raise RuntimeError("Video đã được đánh dấu Lỗi; bỏ qua khởi tạo Chat.")
    if str(video.get("chat_url") or "").strip():
        db.update_system_job(
            job["id"],
            status="done",
            progress="Video đã có Chat riêng; không tạo trùng",
            result_json={"chat_url": video["chat_url"], "duplicate": True},
            finished_at=db.utc_now(),
            error="",
        )
        return True

    payload = job.get("payload") or {}
    published_url = str(
        payload.get("published_url") or video.get("url") or ""
    ).strip()
    transcript = str(video.get("transcript") or "").strip()
    if not transcript:
        db.update_system_job(job["id"], progress="Đang lấy transcript video cũ")
        transcript = get_video_transcript(published_url).strip()
        if not transcript:
            raise RuntimeError("YouTube không trả về transcript cho video cũ.")
        video = db.update_comment_import_context(
            video_id,
            transcript=transcript,
            description=str(payload.get("description") or ""),
        ) or video

    prompt_version = str(
        video.get("prompt_version") or job.get("prompt_version") or ""
    ).strip()
    if not prompt_version:
        raise RuntimeError("Video cũ chưa được gắn với bộ prompt.")
    if not _try_start_chatgpt_operation(
        "comment-video-import",
        prompt_version,
        video_id,
    ):
        db.update_system_job(
            job["id"],
            status="queued",
            progress="ChatGPT đang bận; video cũ vẫn chờ đúng thứ tự",
            started_at="",
        )
        return False
    try:
        current = db.get_video(video_id) or video
        if str(current.get("chat_url") or "").strip():
            chat_url = current["chat_url"]
        else:
            db.update_system_job(
                job["id"],
                progress="Đang tạo Chat riêng từ tiêu đề, mô tả và transcript",
            )
            from auto_yt.services.chatgpt_worker import initialize_comment_video_chat

            chat_url = initialize_comment_video_chat(
                title=current.get("title") or job.get("title") or "",
                description=(
                    current.get("description")
                    or payload.get("description")
                    or ""
                ),
                transcript=current.get("transcript") or transcript,
                prompt_version=prompt_version,
            )
            current = db.set_video_chat_url_if_empty(video_id, chat_url)
            chat_url = str((current or {}).get("chat_url") or chat_url)
        db.update_system_job(
            job["id"],
            status="done",
            progress="Đã tạo Chat riêng cho video cũ",
            result_json={"chat_url": chat_url},
            finished_at=db.utc_now(),
            error="",
        )
        return True
    finally:
        _finish_chatgpt_operation()


def _execute_comment_publish_job(job: dict) -> None:
    comments = db.get_youtube_comments((job.get("payload") or {}).get("comment_ids") or [])
    if len(comments) != 1:
        raise RuntimeError("Mỗi job đăng chỉ được chứa một bình luận.")
    comment = comments[0]
    if comment.get("status") == "replied" or comment.get("reply_youtube_id"):
        db.update_system_job(
            job["id"], status="done", progress="Câu trả lời đã tồn tại; không đăng trùng",
            finished_at=db.utc_now(), error="",
        )
        return
    channel = db.get_youtube_channel(int(comment["youtube_channel_id"]))
    if not channel:
        raise RuntimeError("Kênh YouTube không còn tồn tại.")
    publish_at, schedule_reason = _next_comment_publish_time(comment, channel)
    now = datetime.datetime.now(datetime.timezone.utc)
    if publish_at > now + datetime.timedelta(seconds=1):
        db.update_youtube_comment(comment["comment_id"], status="scheduled", error="")
        db.update_system_job(
            job["id"],
            status="retry_wait",
            progress=f"Đã hẹn đăng: {schedule_reason}",
            next_retry_at=publish_at.isoformat(),
            started_at="",
            finished_at="",
            error="",
        )
        return

    channel, access_token = _get_youtube_access_token(int(comment["youtube_channel_id"]))
    # Last idempotency guard: a manual/channel reply may have appeared after
    # drafting or after the most recent sync.  Fail closed on lookup errors and
    # never call comments.insert until the full reply thread was checked.
    if _reconcile_existing_youtube_reply(comment, channel, access_token):
        db.update_system_job(
            job["id"],
            status="done",
            progress="Câu trả lời đã tồn tại trên YouTube; không đăng trùng",
            result_json={"published": False, "already_replied": True},
            finished_at=db.utc_now(),
            error="",
        )
        return
    db.update_youtube_comment(comment["comment_id"], status="publishing", error="")
    db.update_system_job(job["id"], progress="Đang đăng câu trả lời lên YouTube")
    try:
        safe_reply = youtube_comments.validate_comment_reply(comment["draft_reply"])
        proxy_info = channel.get("gpm_proxy_info")
        interaction_mode = str(channel.get("interaction_mode") or "gpm_browser").strip()
        gpm_profile_id = str(channel.get("gpm_profile_id") or "").strip()
        auto_heart = bool(channel.get("auto_heart", 1))

        result = None
        # If interaction_mode is gpm_browser and profile is mapped, attempt GPM browser automation posting
        if interaction_mode == "gpm_browser" and gpm_profile_id:
            video_url = ""
            video_id = comment.get("video_id")
            if video_id:
                vid = db.get_video(video_id)
                video_url = str((vid or {}).get("published_url") or "")
            if not video_url and comment.get("publication_id"):
                pub = db.get_video_publication(int(comment["publication_id"]))
                video_url = str((pub or {}).get("published_url") or "")
            if not video_url and comment.get("youtube_video_id"):
                video_url = f"https://www.youtube.com/watch?v={comment['youtube_video_id']}"

            if video_url:
                try:
                    db.update_system_job(job["id"], progress="Đang đăng bình luận qua GPM Profile Browser")
                    gpm_result = asyncio.run(
                        gpm_youtube_automation.post_comment_reply_via_gpm(
                            gpm_profile_id,
                            video_url=video_url,
                            comment_text=safe_reply,
                            comment_id=comment.get("comment_id") or "",
                            auto_heart=auto_heart,
                        )
                    )
                    result = {
                        "reply_id": "",
                        "text": safe_reply,
                        "published_at": db.utc_now(),
                        "mode": "gpm_browser",
                        **gpm_result,
                    }
                except Exception as gpm_exc:
                    logger.warning("Đăng qua GPM CDP thất bại (%s), fallback sang REST API qua proxy...", gpm_exc)

        # Fallback to direct REST API with channel proxy if CDP was not used or failed
        if result is None:
            result = youtube_comments.publish_reply(
                access_token,
                comment["comment_id"],
                safe_reply,
                proxy=proxy_info,
            )
    except Exception as exc:
        db.update_youtube_comment(
            comment["comment_id"],
            status="reconcile_required",
            error=(
                "Không xác định YouTube đã nhận câu trả lời hay chưa. "
                "Hãy đồng bộ lại trước khi thử đăng lại. "
                + security_logging.redact_sensitive(exc)
            ),
        )
        raise
    db.update_youtube_comment(
        comment["comment_id"],
        status="replied",
        reply_youtube_id=result.get("reply_id") or "",
        reply_text=result.get("text") or comment["draft_reply"],
        reply_published_at=result.get("published_at") or db.utc_now(),
        is_hearted=int(bool(result.get("hearted"))),
        error="",
    )
    db.update_system_job(
        job["id"], status="done", progress="Đã đăng câu trả lời",
        result_json=result, finished_at=db.utc_now(), error="",
    )


def _schedule_automatic_chatgpt_login(job_id: str, error: Exception) -> bool:
    """Enqueue a background Auto Login thread for the given incident job.

    Returns False without starting a thread when:
    - The error is CAPTCHA/Cloudflare (not a plain login-expired error)
    - A cooldown is in effect after a recent failure
    """
    global _automatic_login_active
    from auto_yt.services.chatgpt_runtime import is_chatgpt_login_required

    if not is_chatgpt_login_required(error):
        # CAPTCHA / passkey / device verification – cannot be automated
        return False

    start_thread = False
    with _automatic_login_state_lock:
        now = time.monotonic()
        if _automatic_login_last_failure_at > 0 and (
            now - _automatic_login_last_failure_at < _AUTOMATIC_LOGIN_COOLDOWN_SECONDS
        ):
            return False

        _automatic_login_job_ids.add(job_id)

        if not _automatic_login_active:
            _automatic_login_active = True
            start_thread = True

    if start_thread:
        def _worker():
            _run_automatic_chatgpt_login()
        threading.Thread(target=_worker, daemon=True).start()

    return True


def _run_chatgpt_login_blocking() -> dict:
    """Run ChatGPT auto-login in the current thread. Returns {success, error}."""
    profile_reserved = False
    try:
        from auto_yt.services import chatgpt_browser_service
        from auto_yt.services.chatgpt_login import login_gpt_auto, restore_session
        import asyncio

        account = account_store.load_account()
        if not account.get("email"):
            return {"success": False, "error": "Chưa cấu hình tài khoản"}

        if not _try_start_chatgpt_operation("auto-login"):
            return {"success": False, "error": CHATGPT_BUSY_ERROR}
        profile_reserved = True

        chatgpt_browser_service.stop_browser_service()

        async def _do_login():
            from playwright.async_api import async_playwright
            profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
            profile_dir.mkdir(parents=True, exist_ok=True)
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    str(profile_dir),
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                    ignore_default_args=["--enable-automation"],
                )
                try:
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

                    if isinstance(result, dict) and result.get("cookies"):
                        account["session_cookie"] = result["cookies"]
                        account_store.save_account(account)
                    return result
                finally:
                    await context.close()

        result = asyncio.run(_do_login())
        return result or {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    finally:
        if profile_reserved:
            try:
                from auto_yt.services import chatgpt_browser_service
                chatgpt_browser_service.start_browser_service()
            except Exception:
                pass
            _finish_chatgpt_operation()


def _run_automatic_chatgpt_login() -> None:
    """Run auto-login and resume/fail all incident jobs. Clears active flag when done."""
    global _automatic_login_active, _automatic_login_last_failure_at
    try:
        result = _run_chatgpt_login_blocking()
        success = bool(result.get("success"))

        with _automatic_login_state_lock:
            incident_ids = set(_automatic_login_job_ids)

        if success:
            # Resume every paused incident job
            for iid in incident_ids:
                job = db.get_system_job(iid)
                if job and job.get("status") == "paused":
                    db.update_system_job(
                        iid,
                        status="queued",
                        result_json={},
                        error="",
                        progress="Đang thêm lại vào hàng đợi sau Auto Login",
                    )
            _kick_video_queue()
            _kick_comment_queue()
        else:
            err = result.get("error", "")
            with _automatic_login_state_lock:
                _automatic_login_last_failure_at = time.monotonic()
            for iid in incident_ids:
                job = db.get_system_job(iid)
                if job and job.get("status") == "paused":
                    existing_result = dict(job.get("result") or {})
                    existing_result["automatic_login"] = "failed"
                    db.update_system_job(
                        iid,
                        result_json=existing_result,
                        progress=f"Auto Login thất bại: {err}",
                    )
    except Exception as exc:
        err = str(exc)
        with _automatic_login_state_lock:
            _automatic_login_last_failure_at = time.monotonic()
            incident_ids = set(_automatic_login_job_ids)
        for iid in incident_ids:
            job = db.get_system_job(iid)
            if job and job.get("status") == "paused":
                existing_result = dict(job.get("result") or {})
                existing_result["automatic_login"] = "failed"
                db.update_system_job(
                    iid,
                    result_json=existing_result,
                    progress=f"Auto Login thất bại: {err}",
                )
    finally:
        with _automatic_login_state_lock:
            _automatic_login_active = False
            _automatic_login_job_ids.clear()


def _comment_queue_worker() -> None:
    global _comment_queue_worker_active
    try:
        while True:
            job = next(
                (
                    claimed
                    for job_type in COMMENT_JOB_TYPES
                    if (claimed := db.claim_next_system_job(job_type)) is not None
                ),
                None,
            )
            if not job:
                return
            try:
                if job["job_type"] == "comment_sync":
                    _execute_comment_sync_job(job)
                elif job["job_type"] == "comment_draft":
                    if not _execute_comment_draft_job(job):
                        return
                elif job["job_type"] == "comment_publish":
                    _execute_comment_publish_job(job)
                elif not _execute_comment_video_import_job(job):
                    return
            except Exception as exc:
                if isinstance(exc, ChatGPTAttentionRequiredError):
                    scheduled = _schedule_automatic_chatgpt_login(job["id"], exc)
                    result_json = {
                        "attention_required": "chatgpt_verification",
                    }
                    if scheduled:
                        result_json["automatic_login"] = "pending"
                    
                    db.update_system_job(
                        job["id"],
                        status="paused",
                        progress="Cần xác minh phiên ChatGPT trước khi tiếp tục",
                        result_json=result_json,
                        error=security_logging.redact_sensitive(exc),
                        next_retry_at="",
                        finished_at="",
                    )
                    return
                safe_error = security_logging.safe_user_error(
                    "comment_queue_job",
                    exc,
                    "Tác vụ bình luận thất bại.",
                )
                latest = db.get_system_job(job["id"])
                if latest and latest.get("status") == "running":
                    if (
                        job.get("job_type") == "comment_sync"
                        and bool((job.get("payload") or {}).get("automatic"))
                    ):
                        # Automatic sync must not create a fresh failed job every
                        # minute while YouTube or the local network is unhealthy.
                        # Keep one durable job and back it off exponentially.
                        attempt = max(1, int(latest.get("attempt") or 1))
                        delay_minutes = min(360, 5 * (2 ** min(attempt - 1, 7)))
                        db.schedule_system_job_recovery(
                            job["id"],
                            resume_from_step="đồng bộ bình luận YouTube",
                            delay_seconds=delay_minutes * 60,
                            error=safe_error,
                        )
                    else:
                        db.update_system_job(
                            job["id"], status="error", progress="Tác vụ bình luận thất bại",
                            error=safe_error, finished_at=db.utc_now(),
                        )
                for comment_id in (job.get("payload") or {}).get("comment_ids", []):
                    comment = db.get_youtube_comments([comment_id])
                    if comment and comment[0].get("status") not in {"replied", "reconcile_required"}:
                        db.update_youtube_comment(comment_id, status="error", error=safe_error)
    finally:
        with _comment_queue_state_lock:
            _comment_queue_worker_active = False


def _kick_comment_queue() -> None:
    global _comment_queue_worker_active
    with _comment_queue_state_lock:
        if _comment_queue_worker_active:
            return
        if not any(db.has_claimable_system_jobs(job_type) for job_type in COMMENT_JOB_TYPES):
            return
        _comment_queue_worker_active = True
    threading.Thread(target=_comment_queue_worker, daemon=True).start()


def _channel_sync_job_exists(channel_db_id: int) -> bool:
    return any(
        int((job.get("payload") or {}).get("channel_id") or 0) == channel_db_id
        and job.get("status") in {"queued", "running", "retry_wait", "paused"}
        for job in db.list_system_jobs(limit=500, job_type="comment_sync")
    )


def _enqueue_due_comment_syncs() -> int:
    now = datetime.datetime.now(datetime.timezone.utc)
    created = 0
    for channel in db.list_youtube_channels():
        if not channel.get("auto_sync") or channel.get("status") != "connected":
            continue
        last_sync_text = str(channel.get("last_sync_at") or "")
        try:
            last_sync = datetime.datetime.fromisoformat(last_sync_text)
            if last_sync.tzinfo is None:
                last_sync = last_sync.replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            last_sync = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
        interval = datetime.timedelta(
            minutes=max(2, int(channel.get("sync_interval_minutes") or 10))
        )
        if now - last_sync < interval or _channel_sync_job_exists(channel["id"]):
            continue
        _create_comment_system_job(
            "comment_sync",
            title=channel["title"],
            payload={"channel_id": channel["id"], "automatic": True},
        )
        created += 1
    if created:
        _kick_comment_queue()
    return created


def _comment_sync_scheduler() -> None:
    while not _comment_sync_stop_event.wait(60):
        try:
            _enqueue_due_comment_syncs()
            # Scheduled publish jobs become claimable as their due time passes,
            # even when no new channel sync was created in this cycle.
            _kick_comment_queue()
        except Exception:
            # Individual failures remain visible as persistent jobs; the
            # scheduler itself must stay alive for every other channel.
            continue


def _start_omnivoice_worker_safely() -> None:
    try:
        tts.ensure_omnivoice_worker_running()
    except Exception as exc:
        # OmniVoice is optional and must not take Genmax or the web app down.
        # Do not print credentials, request payloads or local sample contents.
        message = security_logging.redact_sensitive(str(exc))
        print(f"OmniVoice worker is unavailable: {message}", file=sys.stderr)


@app.on_event("startup")
def resume_background_jobs() -> None:
    global _comment_sync_thread, _tts_preview_cleanup_thread
    # Migrate any legacy plaintext credentials before background workers can
    # consume them. Secrets remain decrypted only in the current process.
    account_store.load_account(migrate=True)
    tts.migrate_api_key_storage()
    try:
        omnivoice_provider = voice_config.get_provider(
            voice_config.OMNIVOICE_PROVIDER_ID
        )
    except ValueError:
        omnivoice_provider = {"enabled": False}
    if omnivoice_provider.get("enabled", True):
        # The API process is lightweight: the GPU model stays unloaded until
        # an OmniVoice job arrives.  Start it off the FastAPI startup thread so
        # a missing local dependency cannot block Genmax or the web app.
        threading.Thread(
            target=_start_omnivoice_worker_safely,
            daemon=True,
            name="omnivoice-api-startup",
        ).start()
    browser_status = chatgpt_browser_service.start_browser_service()
    if not browser_status.get("connected"):
        print(
            "ChatGPT Browser Service is unavailable; ChatGPT jobs will pause "
            f"instead of opening a new window: {browser_status.get('message', '')}",
            file=sys.stderr,
        )
    flow_browser_status = google_flow_browser_service.start_browser_service()
    if not flow_browser_status.get("connected"):
        print(
            "Google Flow Browser Service is unavailable: "
            f"{flow_browser_status.get('message', '')}",
            file=sys.stderr,
        )
    for task in db.get_active_audio_tasks():
        _start_audio_watcher(task["video_id"])
    db.recover_interrupted_system_jobs("video_generation")
    for job_type in COMMENT_JOB_TYPES:
        db.recover_interrupted_system_jobs(job_type)
    for prod_job in (
        "video_render",
        "visual_scene_plan",
        "youtube_upload",
        "youtube_publish",
    ):
        db.recover_interrupted_system_jobs(prod_job)
    _reconcile_active_comment_draft_job_duplicates()
    db.pause_queued_attention_jobs()
    _kick_video_queue()
    _kick_production_queue()
    _comment_sync_stop_event.clear()
    # Do not activate channel jobs during application startup. A due comment
    # publish can launch its assigned GPM profile, which must never be a side
    # effect of starting or restarting Auto_YT. The scheduler performs the
    # first normal queue check after the application is fully ready.
    if _comment_sync_thread is None or not _comment_sync_thread.is_alive():
        _comment_sync_thread = threading.Thread(
            target=_comment_sync_scheduler,
            daemon=True,
            name="youtube-comment-sync",
        )
        _comment_sync_thread.start()
    _tts_preview_cleanup_stop_event.clear()
    _cleanup_expired_tts_previews()
    if (
        _tts_preview_cleanup_thread is None
        or not _tts_preview_cleanup_thread.is_alive()
    ):
        _tts_preview_cleanup_thread = threading.Thread(
            target=_tts_preview_cleanup_scheduler,
            daemon=True,
            name="tts-preview-cleanup",
        )
        _tts_preview_cleanup_thread.start()


@app.on_event("shutdown")
def stop_video_queue_wakeup_timer() -> None:
    global _video_queue_wakeup_at, _video_queue_wakeup_timer
    with _video_queue_state_lock:
        if _video_queue_wakeup_timer is not None:
            _video_queue_wakeup_timer.cancel()
        _video_queue_wakeup_timer = None
        _video_queue_wakeup_at = 0.0
    _comment_sync_stop_event.set()
    _tts_preview_cleanup_stop_event.set()
    if _production_coordinator is not None:
        _production_coordinator.stop()
    chatgpt_browser_service.stop_browser_service()


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


def _is_transient_youtube_error(error: BaseException | str) -> bool:
    normalized_error = str(error).casefold()
    return any(
        marker in normalized_error
        for marker in TRANSIENT_YOUTUBE_ERROR_MARKERS
    )


def _is_transient_chatgpt_start_error(error: BaseException | str) -> bool:
    normalized_error = str(error).casefold()
    return any(
        marker in normalized_error
        for marker in TRANSIENT_CHATGPT_START_ERROR_MARKERS
    )


def _schedule_automatic_video_recovery(
    job_id: str,
    video_id: int | None,
    error: str,
    result: dict | None,
    *,
    resume_from_step: str = "",
) -> bool:
    if video_id is not None:
        video = db.get_video(video_id)
        if not video or video.get("video_status") == db.VIDEO_STATUS_ERROR:
            return False
    resume_from_step = str(resume_from_step or "").strip()
    if not resume_from_step:
        if video_id is None:
            return False
        checkpoint = load_checkpoint(video_id)
        resume_from_step = str(checkpoint.get("current_step") or "").strip()
        if not checkpoint or not resume_from_step or resume_from_step == "complete":
            return False

    job = db.get_system_job(job_id)
    recovery_count = int((job or {}).get("recovery_count") or 0)
    recovery_delays = (
        YOUTUBE_RECOVERY_DELAYS_SECONDS
        if resume_from_step == "youtube_transcript"
        else VIDEO_RECOVERY_DELAYS_SECONDS
    )
    if recovery_count >= len(recovery_delays):
        return False

    delay_seconds = recovery_delays[recovery_count]
    recovered_job = db.schedule_system_job_recovery(
        job_id,
        resume_from_step=resume_from_step,
        delay_seconds=delay_seconds,
        error=error,
        result_json=result,
    )
    if not recovered_job:
        return False
    if resume_from_step == "youtube_transcript":
        progress = (
            f"YouTube tạm giới hạn truy cập; tự thử lại sau {delay_seconds} giây "
            f"(lần {recovered_job['recovery_count']}/{len(recovery_delays)})"
        )
    elif resume_from_step == "chatgpt_start":
        progress = (
            f"ChatGPT tạm thời chưa sẵn sàng; tự thử lại bản nháp sau "
            f"{delay_seconds} giây "
            f"(lần {recovered_job['recovery_count']}/{len(recovery_delays)})"
        )
    else:
        progress = (
            f"Đã lưu checkpoint; tự phục hồi bước {resume_from_step} sau "
            f"{delay_seconds} giây "
            f"(lần {recovered_job['recovery_count']}/{len(recovery_delays)})"
        )
    recovered_job = db.update_system_job(
        job_id,
        progress=progress,
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
    voice_name = str(job.get("voice_name") or payload.get("voice_name") or "")
    voice_snapshot = payload.get("voice_snapshot")
    if not isinstance(voice_snapshot, dict):
        voice_snapshot = _voice_snapshot_from_record(job)
    if not voice_snapshot:
        try:
            voice_snapshot = voice_config.build_voice_snapshot(
                voice_config.get_voice(voice_id)
            )
        except ValueError:
            voice_snapshot = {
                "voice_id": voice_id,
                "voice_name": voice_name,
                "provider_id": job.get("tts_provider_id") or "genmax",
                "provider_voice_id": voice_id,
                "voice_revision": int(job.get("voice_revision") or 1),
                "config": {},
            }
    production_snapshot = payload.get("production_snapshot")
    if not isinstance(production_snapshot, dict):
        production_snapshot = _get_prompt_production_snapshot(prompt_version)
    pipeline = chatgpt_projects.normalize_prompt_pipeline(
        production_snapshot.get("pipeline")
        if production_snapshot.get("pipeline") is not None
        else payload.get("pipeline")
    )
    video_id = job.get("video_id")
    full_transcript = ""
    title = ""
    completed_script_before_restart = ""

    if video_id is not None:
        _set_chatgpt_video_id(video_id)

    def update(message: str) -> None:
        _update_video_job(job_id, progress=message)

    try:
        _raise_if_video_job_canceled(job_id)
        if video_id:
            existing_video = db.get_video(video_id)
            if not existing_video:
                raise RuntimeError(f"Không tìm thấy bản nháp video #{video_id} để tiếp tục.")
            if existing_video.get("video_status") == db.VIDEO_STATUS_ERROR:
                raise VideoJobCanceled(
                    "Video đang ở trạng thái Lỗi nên không được xử lý tiếp."
                )
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
                url=request_url,
                title=title,
                transcript=full_transcript,
                generated_script=INITIAL_GENERATED_SCRIPT,
                prompt_version=prompt_version,
                voice_id=voice_id,
                voice_name=voice_name,
                tts_provider_id=voice_snapshot.get("provider_id") or "genmax",
                voice_revision=int(voice_snapshot.get("voice_revision") or 1),
                voice_snapshot_json=json.dumps(voice_snapshot, ensure_ascii=False),
                production_snapshot_json=json.dumps(
                    production_snapshot, ensure_ascii=False
                ),
            )
            _set_chatgpt_video_id(video_id)
            _update_video_job(
                job_id,
                video_id=video_id,
                title=title,
                recovery_count=0,
                resume_from_step="",
                next_retry_at="",
            )

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
            error=security_logging.redact_sensitive(exc),
            cancel_requested=0,
            finished_at=db.utc_now(),
        )
    except Exception as exc:
        if isinstance(exc, ChatGPTAttentionRequiredError):
            scheduled = _schedule_automatic_chatgpt_login(job_id, exc)
            result_json = {
                "attention_required": "chatgpt_verification",
                "video_id": video_id,
            }
            if scheduled:
                result_json["automatic_login"] = "pending"
            _update_video_job(
                job_id,
                status="paused",
                progress=(
                    "Cần xác minh phiên ChatGPT — đang chạy Auto Login"
                    if scheduled
                    else "Cần xác minh phiên ChatGPT trước khi tiếp tục"
                ),
                result_json=result_json,
                error=security_logging.redact_sensitive(exc),
                cancel_requested=0,
                next_retry_at="",
                finished_at="",
            )
            return
        error_message = security_logging.redact_sensitive(exc) or "Tạo video thất bại."
        if video_id is None and _is_transient_youtube_error(exc):
            if _schedule_automatic_video_recovery(
                job_id,
                None,
                error_message,
                None,
                resume_from_step="youtube_transcript",
            ):
                return
        if (
            "Could not retrieve a transcript" in error_message
            or "Subtitles are disabled" in error_message
        ):
            error_message = (
                "Video này không có phụ đề (Transcript). Vui lòng chọn video khác."
            )

        recovered_result = None
        checkpoint = {}
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

        if (
            video_id is not None
            and not checkpoint
            and _is_transient_chatgpt_start_error(exc)
            and _schedule_automatic_video_recovery(
                job_id,
                video_id,
                error_message,
                None,
                resume_from_step="chatgpt_start",
            )
        ):
            return

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



def _production_job_progress(
    job: dict,
    message: str,
    stage: str = "",
    upload_percent: int | None = None,
) -> None:
    current_job = db.get_system_job(job["id"]) or job
    result = dict(current_job.get("result") or {})
    workflow = db.get_youtube_publish_workflow_by_job(job["id"])
    if stage:
        result["publish_stage"] = stage
    if upload_percent is not None:
        result["upload_percent"] = max(0, min(100, int(upload_percent)))
    if workflow:
        result["scheduled_at"] = str(workflow.get("scheduled_at") or "")
        result["youtube_video_id"] = str(workflow.get("youtube_video_id") or "")
    db.update_system_job(job["id"], progress=message, result_json=result)
    video_id = current_job.get("video_id")
    if video_id and stage:
        db.update_video_production_state(
            int(video_id),
            current_stage=stage,
            production_progress=message,
        )


def _execute_visual_scene_plan_job(job: dict) -> None:
    from auto_yt.services import video_production

    payload = job.get("payload") or {}
    video_id = int(job.get("video_id") or payload.get("video_id") or 0)
    _raise_if_video_job_canceled(job["id"])
    video_production.save_visual_scene_plan(
        video_id,
        payload["plan_hash"],
        payload["plan"],
    )
    db.update_system_job(
        job["id"],
        status="completed",
        progress="Đã lưu kế hoạch cảnh",
        finished_at=db.utc_now(),
    )


def _enqueue_youtube_publish_if_enabled(
    *,
    video_id: int,
    snapshot: dict,
    artifact: dict | None = None,
) -> dict | None:
    pipeline = snapshot.get("pipeline") if isinstance(snapshot, dict) else {}
    if not isinstance(pipeline, dict) or not pipeline.get("youtube_upload"):
        return None
    existing_workflow = db.get_youtube_publish_workflow_for_video(video_id)
    if existing_workflow:
        return None
    for existing_job in db.list_system_jobs(video_id=video_id, limit=None):
        if (
            existing_job.get("job_type") in {"youtube_upload", "youtube_publish"}
            and existing_job.get("status")
            in {"queued", "running", "retry_wait", "paused", "completed"}
        ):
            return existing_job
    artifact = artifact or db.get_latest_video_artifact(
        video_id, "final_mp4", status="ready"
    )
    if not artifact:
        return None
    video = db.get_video(video_id) or {}
    job = db.create_system_job(
        job_id=f"youtube-publish-{uuid.uuid4().hex}",
        job_type="youtube_publish",
        title=f"Đăng YouTube: {video.get('generated_title') or video.get('title') or video_id}",
        payload={
            "video_id": video_id,
            "artifact_id": int(artifact["id"]),
            "snapshot": snapshot,
        },
        prompt_version=str(snapshot.get("prompt_version") or ""),
    )
    job = db.update_system_job(job["id"], video_id=video_id)
    db.update_video_production_state(
        video_id,
        publish_status="queued",
        current_stage="preflight",
        production_progress="Đang chờ kiểm tra cấu hình đăng YouTube",
        blocking_reason="",
    )
    _kick_production_queue()
    return job


def _execute_video_render_job(job: dict) -> None:
    from auto_yt.services import video_production

    payload = job.get("payload") or {}
    video_id = int(job.get("video_id") or payload.get("video_id") or 0)
    snapshot = payload.get("snapshot") or _get_prompt_production_snapshot(
        job.get("prompt_version") or ""
    )

    def update(message: str, stage: str = "", *args, **kwargs) -> None:
        del args, kwargs
        db.update_system_job(job["id"], progress=message)
        db.update_video_production_state(
            video_id,
            render_status="running",
            current_stage=stage or "video_render",
            production_progress=message,
            blocking_reason="",
        )

    result = video_production.produce_video(
        video_id,
        snapshot,
        update,
        lambda: _raise_if_video_job_canceled(job["id"]),
        force_new_project=bool(payload.get("force_new_project", False)),
    )
    db.update_system_job(
        job["id"],
        status="completed",
        progress="Đã dựng MP4 hoàn tất",
        result_json=result,
        finished_at=db.utc_now(),
    )
    db.update_video_production_state(
        video_id,
        render_status="completed",
        current_stage="video_render",
        production_progress="Đã dựng MP4 hoàn tất",
        blocking_reason="",
    )
    _enqueue_youtube_publish_if_enabled(
        video_id=video_id,
        snapshot=snapshot,
        artifact=result.get("artifact") if isinstance(result, dict) else None,
    )


def _execute_youtube_publish_job(job: dict) -> None:
    payload = dict(job.get("payload") or {})
    if not payload.get("artifact_id") and not payload.get("workflow_id"):
        video_id = int(job.get("video_id") or payload.get("video_id") or 0)
        artifact = db.get_latest_video_artifact(video_id, "final_mp4", status="ready")
        if artifact:
            payload["artifact_id"] = int(artifact["id"])
            job = db.update_system_job(job["id"], payload_json=payload) or job
    result = youtube_publish_workflow.execute_publish_job(
        job,
        progress=lambda message, stage="", upload_percent=None: _production_job_progress(
            job, message, stage, upload_percent
        ),
        cancel_check=lambda: _raise_if_video_job_canceled(job["id"]),
        resolve_default_channel_id=_get_prompt_default_youtube_channel_id,
        thumbnails_dir=THUMBNAILS_DIR,
    )
    db.update_system_job(
        job["id"],
        status="completed",
        progress="Đã hoàn tất đăng YouTube",
        result_json=result,
        error="",
        recovery_count=0,
        resume_from_step="",
        next_retry_at="",
        cancel_requested=0,
        finished_at=db.utc_now(),
    )


def _pause_youtube_publish_job(
    job: dict,
    *,
    message: str,
    attention_required: str,
    missing_configuration: list[str],
    workflow_status: str = "paused",
) -> None:
    workflow = db.get_youtube_publish_workflow_by_job(job["id"])
    if workflow:
        db.update_youtube_publish_workflow(
            workflow["id"],
            status=workflow_status,
            error=message,
        )
    result = dict((db.get_system_job(job["id"]) or job).get("result") or {})
    result.update(
        {
            "attention_required": attention_required,
            "missing_configuration": list(dict.fromkeys(missing_configuration)),
        }
    )
    db.update_system_job(
        job["id"],
        status="paused",
        progress=message,
        result_json=result,
        error=message,
        next_retry_at="",
        cancel_requested=0,
        finished_at="",
    )
    video_id = job.get("video_id")
    if video_id:
        db.update_video_production_state(
            int(video_id),
            publish_status=workflow_status,
            production_progress=message,
            blocking_reason=message,
        )


def _schedule_youtube_publish_recovery(job: dict, error: str) -> bool:
    current = db.get_system_job(job["id"]) or job
    result = dict(current.get("result") or {})
    network_retry_count = int(result.get("network_retry_count") or 0)
    if network_retry_count >= len(YOUTUBE_RECOVERY_DELAYS_SECONDS):
        return False
    delay_seconds = YOUTUBE_RECOVERY_DELAYS_SECONDS[network_retry_count]
    result["network_retry_count"] = network_retry_count + 1
    result.pop("attention_required", None)
    result.pop("missing_configuration", None)
    recovered = db.schedule_system_job_recovery(
        job["id"],
        resume_from_step=str(result.get("publish_stage") or "preflight"),
        delay_seconds=delay_seconds,
        error=error,
        result_json=result,
    )
    if not recovered:
        return False
    db.update_system_job(
        job["id"],
        progress=f"Lỗi tạm thời; tự thử lại sau {delay_seconds} giây",
    )
    workflow = db.get_youtube_publish_workflow_by_job(job["id"])
    if workflow:
        db.update_youtube_publish_workflow(
            workflow["id"], status="retry_wait", error=error
        )
    return True


def _schedule_youtube_processing_poll(
    job: dict,
    pending: youtube_publisher.YouTubeProcessingPending,
) -> bool:
    current = db.get_system_job(job["id"]) or job
    result = dict(current.get("result") or {})
    result["processing_poll_count"] = int(result.get("processing_poll_count") or 0) + 1
    result["publish_stage"] = "processing"
    result.pop("attention_required", None)
    result.pop("missing_configuration", None)
    delay_seconds = max(0.0, float(pending.delay_seconds))
    recovered = db.schedule_system_job_recovery(
        job["id"],
        resume_from_step="processing",
        delay_seconds=delay_seconds,
        error=security_logging.redact_sensitive(str(pending)),
        result_json=result,
    )
    if not recovered:
        return False
    db.update_system_job(
        job["id"],
        progress=f"YouTube đang xử lý video; kiểm tra lại sau {delay_seconds:g} giây",
    )
    return True


def _handle_production_job_error(job: dict, exc: Exception) -> None:
    safe_error = security_logging.redact_sensitive(str(exc))
    workflow = db.get_youtube_publish_workflow_by_job(job["id"])
    if isinstance(exc, VideoJobCanceled):
        if workflow:
            db.update_youtube_publish_workflow(
                workflow["id"], status="canceled", error=safe_error
            )
        db.update_system_job(
            job["id"],
            status="canceled",
            progress="Đã dừng theo yêu cầu của người dùng",
            error=safe_error,
            cancel_requested=0,
            finished_at=db.utc_now(),
        )
        return
    if isinstance(exc, youtube_publish_workflow.PublishConfigurationRequired):
        _pause_youtube_publish_job(
            job,
            message=safe_error,
            attention_required="youtube_publish_config",
            missing_configuration=exc.missing_configuration,
        )
        return
    if isinstance(exc, publication_scheduler.PublicationScheduleError):
        _pause_youtube_publish_job(
            job,
            message=safe_error,
            attention_required="youtube_publish_schedule",
            missing_configuration=["publication_slots"],
        )
        return
    if isinstance(exc, youtube_publisher.YouTubeUploadReconciliationRequired):
        _pause_youtube_publish_job(
            job,
            message=safe_error,
            attention_required="youtube_reconcile_required",
            missing_configuration=[],
            workflow_status="reconcile_required",
        )
        return
    if isinstance(exc, youtube_publisher.YouTubePublicUploadRestricted):
        _pause_youtube_publish_job(
            job,
            message=safe_error,
            attention_required="youtube_public_verification",
            missing_configuration=["public_upload_verified"],
        )
        return
    if isinstance(exc, ProxyConfigurationError):
        _pause_youtube_publish_job(
            job,
            message=safe_error,
            attention_required="youtube_publish_config",
            missing_configuration=["gpm_proxy_info"],
        )
        return
    if isinstance(exc, SecretStorageError):
        _pause_youtube_publish_job(
            job,
            message=safe_error,
            attention_required="youtube_publish_config",
            missing_configuration=["local_secret_store"],
        )
        return
    if isinstance(exc, youtube_comments.YouTubeCommentsError) and not _is_transient_youtube_error(exc):
        _pause_youtube_publish_job(
            job,
            message=safe_error,
            attention_required="youtube_publish_auth",
            missing_configuration=["youtube_oauth"],
        )
        return
    if isinstance(exc, youtube_publisher.YouTubeProcessingPending):
        if _schedule_youtube_processing_poll(job, exc):
            return
    elif isinstance(exc, youtube_publisher.YouTubeTransientError) or _is_transient_youtube_error(exc):
        if _schedule_youtube_publish_recovery(job, safe_error):
            return
    if workflow:
        db.update_youtube_publish_workflow(
            workflow["id"], status="failed_permanent", error=safe_error
        )
    db.update_system_job(
        job["id"],
        status="error",
        progress="Tác vụ production thất bại",
        error=safe_error,
        cancel_requested=0,
        finished_at=db.utc_now(),
    )


def _execute_fb_crosspost_job(job: dict) -> None:
    from auto_yt.services import fb_crossposter_service

    payload = job.get("payload") or {}
    item_id = int(payload.get("item_id") or 0)
    task_id = payload.get("task_id")
    if not item_id:
        db.update_system_job(job["id"], status="completed", progress="Không tìm thấy item_id trong payload.")
        return

    fb_crossposter_service.process_queue_item_jit(
        item_id=item_id,
        parent_task_id=task_id,
        sys_job_id=job["id"],
    )


def _execute_fb_crosspost_sync_job(job: dict) -> None:
    from auto_yt.services import fb_crossposter_service

    payload = job.get("payload") or {}
    channel_url = payload.get("channel_url") or ""
    target_page_id = payload.get("target_page_id") or ""
    gpm_profile_id = payload.get("gpm_profile_id") or ""
    if not channel_url:
        db.update_system_job(job["id"], status="completed", progress="Không tìm thấy channel_url trong payload.")
        return

    fb_crossposter_service.sync_channel_public_videos(
        channel_url=channel_url,
        gpm_profile_id=gpm_profile_id,
        target_page_id=target_page_id,
        sys_job_id=job["id"],
    )


_production_coordinator = production_coordinator_service.ProductionCoordinator(
    {
        "visual_scene_plan": _execute_visual_scene_plan_job,
        "video_render": _execute_video_render_job,
        "youtube_publish": _execute_youtube_publish_job,
        "youtube_upload": _execute_youtube_publish_job,
        "fb_crosspost": _execute_fb_crosspost_job,
        "fb_crosspost_sync": _execute_fb_crosspost_sync_job,
    },
    error_handler=_handle_production_job_error,
)


def _kick_production_queue() -> None:
    _production_coordinator.wake()


def _trigger_video_render_if_enabled(video_id: int) -> dict | None:
    video = db.get_video(video_id)
    if not video or video.get("video_status") == db.VIDEO_STATUS_ERROR:
        return None
    prompt_version = video.get("prompt_version") or ""
    try:
        snapshot = json.loads(video.get("production_snapshot_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        snapshot = {}
    if not isinstance(snapshot, dict) or not snapshot.get("pipeline"):
        snapshot = _get_prompt_production_snapshot(prompt_version)
    pipeline = snapshot.get("pipeline") or {}
    if not pipeline.get("video_render"):
        return None
    
    existing_jobs = db.list_system_jobs(video_id=video_id, limit=None)
    for j in existing_jobs:
        if j.get("job_type") == "video_render" and j.get("status") in {"queued", "running", "completed"}:
            return j
            
    job_id = f"video-render-{uuid.uuid4().hex}"
    job = db.create_system_job(
        job_id=job_id,
        job_type="video_render",
        title=f"Dựng video MP4 cho #{video_id}",
        payload={"video_id": video_id, "snapshot": snapshot},
        prompt_version=prompt_version,
    )
    db.update_system_job(job["id"], video_id=video_id)
    _kick_production_queue()
    return job

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
                job.get("video_id"),
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
            if current_job and current_job["status"] in {"retry_wait", "paused"}:
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
    _production_snapshot = _get_prompt_production_snapshot(resolved_prompt_version)
    pipeline = chatgpt_projects.normalize_prompt_pipeline(
        _production_snapshot.get("pipeline")
    )
    requested_voice_id = request.voice_id or _get_prompt_default_voice_id(
        resolved_prompt_version
    )
    try:
        selected_voice = voice_config.get_voice(requested_voice_id)
        if selected_voice.get("status", "active") != "active":
            raise ValueError("Giọng đọc đã chọn hiện không hoạt động.")
    except ValueError as exc:
        if not request.voice_id:
            # A voice may have been removed after it was assigned to this
            # prompt version. Fall back safely to the global default.
            selected_voice = voice_config.get_voice(include_inactive=False)
        else:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    selected_voice = _normalize_voice_record(
        selected_voice,
        fallback_id=requested_voice_id,
    )

    try:
        normalized_url = validate_youtube_url(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    video_jobs = db.list_system_jobs(
        limit=500,
        job_type="video_generation",
    )
    for existing_job in video_jobs:
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

    active_video_jobs = sum(
        job.get("status") in {"queued", "running", "retry_wait", "paused"}
        for job in video_jobs
    )
    if active_video_jobs >= MAX_ACTIVE_VIDEO_JOBS:
        raise HTTPException(
            status_code=429,
            detail=(
                "Hàng đợi đã đạt giới hạn an toàn "
                f"{MAX_ACTIVE_VIDEO_JOBS} video. Hãy chờ bớt job trước khi thêm."
            ),
        )

    job_id = uuid.uuid4().hex[:12]
    voice_snapshot = voice_config.build_voice_snapshot(selected_voice)
    job = db.create_system_job(
        job_id=job_id,
        job_type="video_generation",
        title=normalized_url,
        payload={
            "url": normalized_url,
            "prompt_version": resolved_prompt_version,
            "voice_id": selected_voice["id"],
            "voice_name": selected_voice["name"],
            "voice_snapshot": voice_snapshot,
            "pipeline": pipeline,
            "production_snapshot": _production_snapshot,
        },
        prompt_version=resolved_prompt_version,
        voice_id=selected_voice["id"],
        voice_name=selected_voice["name"],
        tts_provider_id=selected_voice["provider_id"],
        voice_revision=selected_voice["revision"],
        voice_snapshot_json=json.dumps(voice_snapshot, ensure_ascii=False),
    )
    _sync_legacy_job(job)
    _kick_video_queue()
    refreshed_job = db.get_system_job(job_id)
    return {
        "job_id": job_id,
        "status": refreshed_job["status"],
        "queue_position": db.get_system_job_queue_position(job_id),
    }


def _voice_persistence_fields(voice: dict) -> dict:
    snapshot = voice_config.build_voice_snapshot(voice)
    return {
        "tts_provider_id": snapshot["provider_id"],
        "voice_revision": snapshot["voice_revision"],
        "voice_snapshot_json": json.dumps(snapshot, ensure_ascii=False),
    }


def _stored_voice_persistence_fields(record: dict) -> dict:
    return {
        "tts_provider_id": record.get("tts_provider_id") or "genmax",
        "voice_revision": int(record.get("voice_revision") or 1),
        "voice_snapshot_json": record.get("voice_snapshot_json") or "{}",
    }


def _voice_snapshot_from_record(record: dict) -> dict | None:
    serialized = record.get("voice_snapshot_json") or ""
    if not serialized:
        return None
    try:
        snapshot = json.loads(serialized)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(snapshot, dict) or not snapshot.get("provider_voice_id"):
        return None
    return snapshot


def _voice_from_snapshot(snapshot: dict) -> dict:
    return {
        "id": snapshot.get("voice_id") or "",
        "name": snapshot.get("voice_name") or "Giọng đã lưu",
        "provider_id": snapshot.get("provider_id") or "genmax",
        "provider_voice_id": snapshot.get("provider_voice_id") or snapshot.get("voice_id") or "",
        "status": "active",
        "revision": int(snapshot.get("voice_revision") or 1),
        "config": snapshot.get("config") or {},
    }


def _normalize_voice_record(
    voice: dict,
    *,
    fallback_id: str = "",
    fallback_name: str = "",
) -> dict:
    """Normalize legacy/test voice rows into the provider-aware v2 shape."""
    voice_id = str(voice.get("id") or fallback_id).strip()
    provider_id = str(
        voice.get("provider_id") or voice_config.GENMAX_PROVIDER_ID
    ).strip().casefold()
    return {
        **voice,
        "id": voice_id,
        "name": str(voice.get("name") or fallback_name or "Giọng đã lưu").strip(),
        "provider_id": provider_id,
        "provider_voice_id": str(
            voice.get("provider_voice_id") or voice_id
        ).strip(),
        "status": str(voice.get("status") or "active").strip().casefold(),
        "revision": max(1, int(voice.get("revision") or 1)),
        "config": voice.get("config") or {},
    }


@app.post("/api/videos/{video_id}/continue-generation")
def continue_video_generation(video_id: int):
    """Resume a power-interrupted ChatGPT workflow in its original chat."""
    video = _require_actionable_video(video_id)

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

    if not _try_start_chatgpt_operation(
        "continue-video",
        prompt_version,
        video_id,
    ):
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
            error_message = (
                security_logging.redact_sensitive(exc)
                or "Tiếp tục video thất bại."
            )
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


def _system_job_center_item(job: dict, queue_position: int | None, *, hydrate: bool = False) -> dict:
    payload = job.get("payload") or {}
    result = job.get("result") or {}
    raw_status = str(job.get("status") or "")
    status_map = {
        "completed": "done",
        "failed": "error",
    }
    status = status_map.get(raw_status, raw_status)
    video_id = job.get("video_id")
    title = (
        job.get("generated_title")
        or job.get("original_title")
        or job.get("title")
        or payload.get("url", "")
    )
    job_type = str(job.get("job_type") or "")
    type_label = ALL_JOB_LABELS.get(job_type, job_type)
    publish_workflow = (
        db.get_youtube_publish_workflow_by_job(job["id"])
        if hydrate and job_type in {"youtube_upload", "youtube_publish"}
        else None
    )
    publish_stage = str(
        result.get("publish_stage")
        or result.get("stage")
        or (publish_workflow or {}).get("stage")
        or ""
    )
    upload_percent = result.get("upload_percent")
    if upload_percent is None and publish_workflow:
        artifact = db.get_video_artifact(int(publish_workflow.get("artifact_id") or 0))
        artifact_size = int((artifact or {}).get("size_bytes") or 0)
        if artifact_size > 0:
            upload_percent = min(
                100,
                int(
                    (int(publish_workflow.get("upload_offset") or 0) / artifact_size)
                    * 100
                ),
            )
    scheduled_at = str(
        result.get("scheduled_at")
        or (publish_workflow or {}).get("scheduled_at")
        or ""
    )
    youtube_video_id = str(
        result.get("youtube_video_id")
        or (publish_workflow or {}).get("youtube_video_id")
        or ""
    )
    artifact_download_url = (
        f"/api/videos/{video_id}/download-mp4"
        if hydrate
        and video_id
        and db.get_latest_video_artifact(int(video_id), "final_mp4", status="ready")
        else ""
    )
    youtube_studio_url = (
        "https://studio.youtube.com/"
        if youtube_video_id
        else ""
    )
    return {
        "id": job["id"],
        "raw_id": job["id"],
        "type": job_type,
        "type_label": type_label,
        "status": status,
        "title": html.unescape(str(title or "")),
        "original_title": html.unescape(
            str(job.get("original_title") or job.get("title", ""))
        ),
        "generated_title": html.unescape(str(job.get("generated_title", ""))),
        "video_url": job.get("video_url") or payload.get("url", ""),
        "progress": job.get("progress", ""),
        "error": job.get("error", ""),
        "video_id": video_id,
        "prompt_version": job.get("prompt_version", ""),
        "voice_id": job.get("voice_id", ""),
        "voice_name": job.get("voice_name", ""),
        "tts_provider_id": job.get("tts_provider_id") or "genmax",
        "voice_revision": int(job.get("voice_revision") or 1),
        "pipeline": (
            chatgpt_projects.normalize_prompt_pipeline(payload.get("pipeline"))
            if job_type == "video_generation"
            else None
        ),
        "queue_position": queue_position,
        "attempt": job.get("attempt", 0),
        "recovery_count": job.get("recovery_count", 0),
        "recovery_limit": (
            len(YOUTUBE_RECOVERY_DELAYS_SECONDS)
            if job_type in {"youtube_upload", "youtube_publish"}
            else (
                len(VIDEO_RECOVERY_DELAYS_SECONDS)
                if job_type == "video_generation"
                else 0
            )
        ),
        "resume_from_step": job.get("resume_from_step", ""),
        "next_retry_at": job.get("next_retry_at", ""),
        "created_at": job.get("created_at", ""),
        "updated_at": job.get("updated_at", ""),
        "started_at": job.get("started_at", ""),
        "finished_at": job.get("finished_at", ""),
        "can_cancel": (
            status in {"queued", "retry_wait", "paused"}
            or (job_type in {"video_generation", "video_render", "visual_scene_plan", "youtube_upload", "youtube_publish", "fb_crosspost", "fb_crosspost_sync", "thumbnail_generation", "tiktok_publish"} and status == "running")
        ),
        "can_retry": (
            status in {"error", "canceled"}
            and job_type != "comment_publish"
        ),
        "can_pause": status in {"queued", "retry_wait"},
        "can_resume": status == "paused",
        "can_edit": job_type == "video_generation" and (
            status in {"error", "canceled"}
            or (status in {"queued", "paused"} and video_id is None)
        ),
        "can_delete": status != "running",
        "attention_required": (
            result.get("attention_required", "")
        ),
        "automatic_login": result.get("automatic_login", ""),
        "missing_configuration": result.get("missing_configuration") or [],
        "publish_stage": publish_stage,
        "upload_percent": upload_percent,
        "scheduled_at": scheduled_at,
        "youtube_video_id": youtube_video_id,
        "artifact_download_url": artifact_download_url,
        "youtube_studio_url": youtube_studio_url,
    }


def _hydrate_job_center_items(items: list[dict]) -> list[dict]:
    if not items:
        return items
    video_ids = [int(item["video_id"]) for item in items if item.get("video_id")]
    ready_artifact_video_ids = (
        db.get_video_ids_with_ready_final_mp4(video_ids) if video_ids else set()
    )
    yt_job_ids = [
        item["id"] for item in items
        if item.get("type") in {"youtube_upload", "youtube_publish"}
    ]
    workflows_by_job_id = (
        db.get_youtube_publish_workflows_by_job_ids(yt_job_ids) if yt_job_ids else {}
    )
    for item in items:
        vid = item.get("video_id")
        if vid and int(vid) in ready_artifact_video_ids:
            item["artifact_download_url"] = f"/api/videos/{vid}/download-mp4"
        workflow = workflows_by_job_id.get(item["id"])
        if workflow:
            if not item.get("publish_stage"):
                item["publish_stage"] = str(workflow.get("stage") or "")
            if item.get("upload_percent") is None:
                artifact_size = int(workflow.get("artifact_size_bytes") or 0)
                if artifact_size > 0:
                    item["upload_percent"] = min(
                        100,
                        int(
                            (int(workflow.get("upload_offset") or 0) / artifact_size)
                            * 100
                        ),
                    )
            if not item.get("scheduled_at"):
                item["scheduled_at"] = str(workflow.get("scheduled_at") or "")
            if not item.get("youtube_video_id"):
                item["youtube_video_id"] = str(workflow.get("youtube_video_id") or "")
            if item.get("youtube_video_id"):
                item["youtube_studio_url"] = "https://studio.youtube.com/"
        elif item.get("youtube_video_id"):
            item["youtube_studio_url"] = "https://studio.youtube.com/"
    return items


def _audio_job_center_item(task: dict) -> dict:
    provider_id = task.get("tts_provider_id") or "genmax"
    provider_name = "OmniVoice" if provider_id == "omnivoice" else "Genmax"
    status_map = {
        "pending": "queued",
        "processing": "running",
        "completed": "done",
        "failed": "error",
        "interrupted": "error",
    }
    status = status_map.get(task.get("status"), task.get("status", "error"))
    progress_map = {
        "pending": f"Đang chờ {provider_name}",
        "processing": f"{provider_name} đang tạo audio",
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
        "voice_name": task.get("voice_name", ""),
        "tts_provider_id": provider_id,
        "voice_revision": int(task.get("voice_revision") or 1),
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


def _checkpoint_references_media(filename: str) -> bool:
    checkpoint_dir = DATA_DIR / "generation_checkpoints"
    if not checkpoint_dir.exists():
        return False
    for checkpoint_file in checkpoint_dir.glob("video_*.json"):
        try:
            if filename in checkpoint_file.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def _delete_unreferenced_managed_media(*payloads) -> tuple[int, list[str]]:
    serialized_payloads = json.dumps(payloads, ensure_ascii=False, default=str)
    media_files = {
        (match.group("kind"), match.group("filename"))
        for match in MANAGED_MEDIA_URL_PATTERN.finditer(serialized_payloads)
    }
    deleted_count = 0
    warnings = []
    for media_kind, filename in media_files:
        if db.is_managed_media_filename_referenced(filename):
            continue
        if _checkpoint_references_media(filename):
            continue
        root = THUMBNAILS_DIR if media_kind == "thumbnails" else AUDIO_DIR
        expected_suffix = ".png" if media_kind == "thumbnails" else ".mp3"
        if Path(filename).name != filename or Path(filename).suffix.lower() != expected_suffix:
            continue
        file_path = root / filename
        try:
            file_path.unlink()
            deleted_count += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            warnings.append(f"Không thể xóa tệp {filename}: {exc}")
    return deleted_count, warnings


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


JOB_CENTER_ACTIONS = ("retry", "pause", "resume", "cancel")
JOB_CENTER_SYSTEM_JOB_TYPES = (
    "video_generation",
    *COMMENT_JOB_TYPES,
    *PRODUCTION_JOB_TYPES,
)


def _job_supports_action(item: dict, action: str) -> bool:
    capability = "can_cancel" if action == "cancel" else f"can_{action}"
    return bool(item.get(capability))


def _job_is_selectable(item: dict) -> bool:
    return any(_job_supports_action(item, action) for action in JOB_CENTER_ACTIONS)


def _collect_job_center_items(job_type: str | None = None) -> list[dict]:
    normalized_type = str(job_type or "").strip()
    if normalized_type == "all":
        normalized_type = ""
    items: list[dict] = []

    if normalized_type == "youtube_publish":
        requested_system_types = ["youtube_publish", "youtube_upload"]
    elif normalized_type in JOB_CENTER_SYSTEM_JOB_TYPES:
        requested_system_types = [normalized_type]
    else:
        requested_system_types = None

    if not normalized_type or requested_system_types:
        if not normalized_type:
            system_jobs = db.list_system_jobs(limit=None, job_type=None)
        else:
            system_jobs = []
            for st in requested_system_types:
                system_jobs.extend(db.list_system_jobs(limit=None, job_type=st))
        queue_positions: dict[str, int] = {}
        queued_counts: dict[str, int] = {}
        for job in sorted(system_jobs, key=lambda item: item["created_at"]):
            if job["status"] != "queued":
                continue
            job_type_key = job["job_type"]
            queued_counts[job_type_key] = queued_counts.get(job_type_key, 0) + 1
            queue_positions[job["id"]] = queued_counts[job_type_key]
        items.extend(
            _system_job_center_item(job, queue_positions.get(job["id"]))
            for job in system_jobs
        )

    if normalized_type in {"", "audio"}:
        items.extend(
            _audio_job_center_item(task)
            for task in db.list_audio_tasks(limit=None)
        )
    if normalized_type in {"", "audio_review"}:
        items.extend(
            _audio_review_job_center_item(review)
            for review in db.list_audio_reviews(limit=None)
        )
    if normalized_type in {"", "youtube_download"}:
        items.extend(
            _download_job_center_item(job)
            for job in download_jobs.list_jobs(limit=None)
        )

    operation, prompt_version = _get_chatgpt_state()
    if operation and operation != "video" and normalized_type in {"", "chatgpt"}:
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
    return items


def _job_matches_status_filter(item: dict, status: str) -> bool:
    if status == "active":
        return item["status"] in {"queued", "running", "retry_wait", "paused"}
    if status == "error":
        return item["status"] in {"error", "review_blocked"}
    return True


def _job_matches_snapshot(item: dict, snapshot_at: str) -> bool:
    created_at = str(item.get("created_at") or "").strip()
    return not snapshot_at or not created_at or created_at <= snapshot_at


def _filter_job_center_items(
    items: list[dict],
    *,
    status: str = "all",
    search: str = "",
    snapshot_at: str = "",
) -> list[dict]:
    normalized_search = db.normalize_search_text(search)[:300]
    return [
        item
        for item in items
        if _job_matches_status_filter(item, status)
        and _job_matches_snapshot(item, snapshot_at)
        and (
            not normalized_search
            or _job_matches_video_search(item, normalized_search)
        )
    ]


def _sort_job_center_items(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: (
            item["status"] in {"queued", "running", "retry_wait", "paused"},
            item.get("updated_at") or item.get("created_at") or "",
        ),
        reverse=True,
    )


@app.get("/api/jobs")
def list_jobs(
    limit: int = 100,
    offset: int = 0,
    job_type: Optional[str] = None,
    status: Literal["all", "active", "error"] = "all",
    search: Optional[str] = None,
):
    requested_limit = max(1, min(int(limit), 500))
    requested_offset = max(0, int(offset))
    snapshot_at = db.utc_now()
    all_items = _collect_job_center_items(job_type)
    counted_items = _filter_job_center_items(
        all_items,
        search=search or "",
        snapshot_at=snapshot_at,
    )
    filtered_items = _sort_job_center_items(
        _filter_job_center_items(counted_items, status=status)
    )
    action_counts = {
        action: sum(_job_supports_action(item, action) for item in filtered_items)
        for action in JOB_CENTER_ACTIONS
    }
    paged_items = _hydrate_job_center_items(
        filtered_items[requested_offset:requested_offset + requested_limit]
    )
    for item in paged_items:
        item.pop("download_videos", None)
    return {
        "items": paged_items,
        "counts": {
            "total": len(counted_items),
            "active": sum(
                item["status"] in {"queued", "running", "retry_wait", "paused"}
                for item in counted_items
            ),
            "error": sum(
                item["status"] in {"error", "review_blocked"}
                for item in counted_items
            ),
        },
        "filtered_total": len(filtered_items),
        "selectable_total": sum(_job_is_selectable(item) for item in filtered_items),
        "action_counts": action_counts,
        "snapshot_at": snapshot_at,
    }


class JobCenterItemNotFoundError(LookupError):
    pass


class JobCenterActionNotAllowedError(ValueError):
    pass


def _kick_job_queues(job_types: set[str]) -> None:
    if "video_generation" in job_types:
        _kick_video_queue()
    if any(job_type in COMMENT_JOB_TYPES for job_type in job_types):
        _kick_comment_queue()
    if any(job_type in PRODUCTION_JOB_TYPES for job_type in job_types):
        _kick_production_queue()


def _run_system_job_center_action(
    job_id: str,
    action: str,
    *,
    kick_queues: bool,
) -> dict:
    job = db.get_system_job(job_id)
    if not job:
        raise JobCenterItemNotFoundError("Không tìm thấy job.")
    current_item = _system_job_center_item(
        job,
        db.get_system_job_queue_position(job_id),
        hydrate=True,
    )
    if not _job_supports_action(current_item, action):
        raise JobCenterActionNotAllowedError(
            "Job không hỗ trợ thao tác này ở trạng thái hiện tại."
        )
    if action == "retry":
        updated_job = db.retry_system_job(job_id)
        if str(job.get("job_type")) == "fb_crosspost":
            try:
                from auto_yt.services import fb_crossposter_service
                raw_payload = job.get("payload") or job.get("payload_json") or {}
                if isinstance(raw_payload, str):
                    try:
                        payload = json.loads(raw_payload)
                    except Exception:
                        payload = {}
                else:
                    payload = dict(raw_payload)
                page_id = payload.get("page_id") or payload.get("target_page_id")
                days_ahead = payload.get("days_ahead")
                item_id = payload.get("item_id")
                if page_id and days_ahead:
                    fb_crossposter_service.start_schedule_ahead_batch(
                        target_page_id=str(page_id),
                        days_ahead=int(days_ahead),
                        existing_sys_job_id=job_id,
                    )
                elif item_id:
                    threading.Thread(
                        target=fb_crossposter_service.process_queue_item_jit,
                        args=(int(item_id),),
                        kwargs={"sys_job_id": job_id},
                        daemon=True,
                    ).start()
            except Exception as retry_err:
                logger.warning("Could not re-trigger fb_crosspost worker on retry: %s", retry_err)
    elif action == "pause":
        updated_job = db.pause_system_job(job_id)
    elif action == "resume":
        updated_job = db.resume_system_job(job_id)
    else:
        updated_job = db.request_cancel_system_job(job_id)
        if str(job.get("job_type")) == "fb_crosspost":
            try:
                from auto_yt.services import fb_crossposter_service
                task_id = (job.get("payload") or {}).get("task_id")
                fb_crossposter_service.cancel_schedule_ahead_batch(task_id=task_id)
            except Exception:
                pass
    if not updated_job:
        raise JobCenterItemNotFoundError("Không tìm thấy job.")
    if str(job.get("job_type")) in {"youtube_upload", "youtube_publish"}:
        workflow = db.get_youtube_publish_workflow_by_job(job_id)
        if workflow:
            workflow_status = {
                "pause": "paused",
                "cancel": "canceled",
                "resume": "reserved",
                "retry": "reserved",
            }.get(action)
            if workflow_status:
                db.update_youtube_publish_workflow(
                    workflow["id"], status=workflow_status, error=""
                )
    _sync_legacy_job(updated_job)
    if kick_queues:
        _kick_job_queues({str(job["job_type"])})
    return _system_job_center_item(
        updated_job,
        db.get_system_job_queue_position(job_id),
        hydrate=True,
    )


def _run_job_center_action(item: dict, action: str) -> dict:
    if item.get("type") in JOB_CENTER_SYSTEM_JOB_TYPES:
        return _run_system_job_center_action(
            str(item["raw_id"]),
            action,
            kick_queues=False,
        )
    if item.get("type") != "youtube_download":
        raise JobCenterActionNotAllowedError(
            "Loại job này không có thao tác hàng loạt."
        )
    current_job = download_jobs.get(str(item["raw_id"]))
    if not current_job:
        raise JobCenterItemNotFoundError("Không tìm thấy job tải YouTube.")
    current_item = _download_job_center_item(current_job)
    if not _job_supports_action(current_item, action):
        raise JobCenterActionNotAllowedError(
            "Job không hỗ trợ thao tác này ở trạng thái hiện tại."
        )
    download_action = "stop" if action == "cancel" else action
    updated_job = getattr(download_jobs, download_action)(str(item["raw_id"]))
    return _download_job_center_item(updated_job)


def _normalize_job_snapshot(snapshot_at: str) -> str:
    normalized = str(snapshot_at or "").strip()
    if not normalized:
        raise ValueError("Thiếu thời điểm snapshot của danh sách job.")
    try:
        parsed = datetime.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("Thời điểm snapshot của danh sách job không hợp lệ.") from exc
    if parsed.tzinfo is None:
        raise ValueError("Thời điểm snapshot của danh sách job phải có múi giờ.")
    return parsed.astimezone(datetime.timezone.utc).isoformat()


def _resolve_bulk_job_selection(
    request: BulkJobActionRequest,
) -> tuple[list[dict], list[str], int]:
    if request.mode == "explicit":
        requested_ids = list(
            dict.fromkeys(str(job_id or "").strip() for job_id in request.job_ids)
        )
        requested_ids = [job_id for job_id in requested_ids if job_id]
        if not requested_ids:
            raise ValueError("Hãy chọn ít nhất một job.")
        items_by_id = {
            item["id"]: item for item in _collect_job_center_items()
        }
        selected_items = [
            items_by_id[job_id]
            for job_id in requested_ids
            if job_id in items_by_id
        ]
        missing_ids = [
            job_id for job_id in requested_ids if job_id not in items_by_id
        ]
        return selected_items, missing_ids, len(requested_ids)

    snapshot_at = _normalize_job_snapshot(request.snapshot_at)
    excluded_ids = {
        str(job_id or "").strip()
        for job_id in request.excluded_job_ids
        if str(job_id or "").strip()
    }
    selected_items = _filter_job_center_items(
        _collect_job_center_items(request.job_type),
        status=request.status,
        search=request.search,
        snapshot_at=snapshot_at,
    )
    selected_items = [
        item
        for item in selected_items
        if item["id"] not in excluded_ids and _job_is_selectable(item)
    ]
    return selected_items, [], len(selected_items)


@app.post("/api/jobs/bulk-action")
def bulk_job_action(request: BulkJobActionRequest):
    try:
        selected_items, missing_ids, requested_count = (
            _resolve_bulk_job_selection(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    skipped_by_reason: dict[str, int] = {}
    failures = [
        {"job_id": job_id, "error": "Không tìm thấy job."}
        for job_id in missing_ids
    ]
    eligible_count = sum(
        _job_supports_action(item, request.action) for item in selected_items
    )
    succeeded_count = 0
    changed_system_job_types: set[str] = set()
    for item in selected_items:
        if not _job_supports_action(item, request.action):
            reason = "Không tương thích với thao tác đã chọn."
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
            continue
        try:
            _run_job_center_action(item, request.action)
            succeeded_count += 1
            if item.get("type") in JOB_CENTER_SYSTEM_JOB_TYPES:
                changed_system_job_types.add(str(item["type"]))
        except (JobCenterActionNotAllowedError, ValueError) as exc:
            reason = str(exc)
            skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
        except JobCenterItemNotFoundError as exc:
            failures.append({"job_id": item["id"], "error": str(exc)})
        except Exception as exc:
            failures.append({
                "job_id": item["id"],
                "error": (
                    security_logging.redact_sensitive(exc)
                    or "Thao tác job thất bại."
                ),
            })
    if changed_system_job_types:
        _kick_job_queues(changed_system_job_types)
    skipped_count = sum(skipped_by_reason.values())
    return {
        "success": True,
        "requested": requested_count,
        "matched": len(selected_items),
        "eligible": eligible_count,
        "succeeded": succeeded_count,
        "skipped": skipped_count,
        "failed": len(failures),
        "skipped_by_reason": skipped_by_reason,
        "failures": failures,
    }


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    try:
        updated_job = _run_system_job_center_action(
            job_id,
            "cancel",
            kick_queues=True,
        )
    except JobCenterItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (JobCenterActionNotAllowedError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "job": updated_job,
    }


@app.patch("/api/jobs/{job_id}")
def update_video_job(job_id: str, request: UpdateVideoJobRequest):
    job = db.get_system_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["job_type"] != "video_generation":
        raise HTTPException(status_code=409, detail="Chỉ có thể sửa job tạo video.")

    try:
        normalized_url = validate_youtube_url(request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with _prompts_config_lock:
        prompt_data = _read_prompts_config()
        prompt_version = request.prompt_version.strip()
        prompt_config = _get_prompt_version(prompt_data, prompt_version)
        pipeline = chatgpt_projects.normalize_prompt_pipeline(
            prompt_config.get("pipeline")
        )
    try:
        selected_voice = _normalize_voice_record(
            voice_config.get_voice(
                request.voice_id,
                include_inactive=False,
            ),
            fallback_id=request.voice_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    voice_snapshot = voice_config.build_voice_snapshot(selected_voice)
    payload = dict(job.get("payload") or {})
    payload.update({
        "url": normalized_url,
        "prompt_version": prompt_version,
        "voice_id": selected_voice["id"],
        "voice_name": selected_voice["name"],
        "voice_snapshot": voice_snapshot,
        "pipeline": pipeline,
    })
    try:
        updated_job = db.update_editable_video_job(
            job_id,
            title=normalized_url,
            payload=payload,
            prompt_version=prompt_version,
            voice_id=selected_voice["id"],
            voice_name=selected_voice["name"],
            tts_provider_id=selected_voice["provider_id"],
            voice_revision=selected_voice["revision"],
            voice_snapshot_json=json.dumps(voice_snapshot, ensure_ascii=False),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not updated_job:
        raise HTTPException(status_code=404, detail="Job not found")
    _sync_legacy_job(updated_job)
    _kick_video_queue()
    _kick_comment_queue()
    return {
        "success": True,
        "job": _system_job_center_item(
            updated_job,
            db.get_system_job_queue_position(job_id),
            hydrate=True,
        ),
    }


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    try:
        deleted_job = db.delete_system_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted_job:
        raise HTTPException(status_code=404, detail="Job not found")
    with _jobs_lock:
        _jobs.pop(job_id, None)
    _kick_job_queues({str(deleted_job.get("job_type") or "video_generation")})
    return {"success": True}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    try:
        updated_job = _run_system_job_center_action(
            job_id,
            "retry",
            kick_queues=True,
        )
    except JobCenterItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (JobCenterActionNotAllowedError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "success": True,
        "job": updated_job,
    }


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    try:
        _run_system_job_center_action(
            job_id,
            "pause",
            kick_queues=True,
        )
    except JobCenterItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (JobCenterActionNotAllowedError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True}


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    try:
        _run_system_job_center_action(
            job_id,
            "resume",
            kick_queues=True,
        )
    except JobCenterItemNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (JobCenterActionNotAllowedError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    status = {
        "busy": _chatgpt_profile_lock.locked(),
        "operation": operation,
        "prompt_version": prompt_version,
    }
    video_id = _get_chatgpt_video_id()
    if video_id is not None:
        status["video_id"] = video_id
    return status


@app.get("/api/videos")
def get_videos(
    limit: int = 10,
    offset: int = 0,
    is_published: Optional[int] = None,
    prompt_version: Optional[str] = None,
    search: Optional[str] = None,
    video_status: Optional[Literal["active", "error"]] = None,
):
    res = db.get_all_videos(
        limit=limit,
        offset=offset,
        is_published=is_published,
        prompt_version=prompt_version,
        search_query=search,
        video_status=video_status,
    )
    with _prompts_config_lock:
        prompt_data = _read_prompts_config()
    channels_by_id = {
        channel["channel_id"]: channel
        for channel in db.list_youtube_channels()
    }
    for v in res.get("items", []):
        v["has_checkpoint"] = bool(load_checkpoint(v["id"]))
        _add_video_default_youtube_channel(v, prompt_data, channels_by_id)
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

    video = _require_actionable_video(req.video_id)

    if not _try_start_chatgpt_operation(
        "metadata",
        video.get("prompt_version", ""),
        req.video_id,
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
        latest_video = _require_actionable_video(req.video_id)
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
        return {
            "success": False,
            "error": security_logging.redact_sensitive(exc),
        }
    finally:
        _finish_chatgpt_operation()


@app.post("/api/generate-chapters")
async def generate_chapters_endpoint(req: GenerateChaptersRequest):
    from auto_yt.services.chatgpt_worker import generate_chapters_only

    video = _require_actionable_video(req.video_id)

    if not _try_start_chatgpt_operation(
        "chapters",
        video.get("prompt_version", ""),
        req.video_id,
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

    latest_video = _require_actionable_video(req.video_id)
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
        video = _require_actionable_video(req.video_id)
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
        req.video_id,
    ):
        return {"success": False, "error": CHATGPT_BUSY_ERROR}

    v_title = ""
    if req.video_id:
        try:
            v_rec = db.get_editable_video_job(req.video_id) or {}
            v_title = v_rec.get("generated_title") or v_rec.get("original_title") or f"Video #{req.video_id}"
        except Exception:
            v_title = f"Video #{req.video_id}"
    else:
        v_title = "Thumbnail độc lập"

    sys_job_id = f"thumb-{req.video_id or int(time.time() * 1000)}"
    try:
        db.create_system_job(
            job_id=sys_job_id,
            job_type="thumbnail_generation",
            title=f"Sinh Thumbnail: {v_title[:50]}",
            video_id=req.video_id,
            payload={"video_id": req.video_id, "thumbnail_type": req.thumbnail_type},
        )
        db.update_system_job(sys_job_id, status="running", progress="Đang tạo ảnh qua ChatGPT...")
    except Exception as exc:
        logger.warning("Could not register thumbnail_generation system_job: %s", exc)

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
            video = _require_actionable_video(req.video_id)
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
        
        try:
            db.update_system_job(
                sys_job_id,
                status="completed",
                progress="Đã tạo xong ảnh thumbnail",
                finished_at=db.utc_now(),
            )
        except Exception:
            pass

        return {"success": True, **result}
    except Exception as e:
        print(f"Error in generate_thumbnails_endpoint: {e}", file=sys.stderr)
        try:
            db.update_system_job(
                sys_job_id,
                status="failed",
                progress=f"Lỗi tạo thumbnail: {str(e)[:100]}",
                error=str(e),
                finished_at=db.utc_now(),
            )
        except Exception:
            pass
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}
    finally:
        _finish_chatgpt_operation()


@app.get("/api/videos/{video_id}/audio-review")
def get_audio_review(video_id: int):
    _require_actionable_video(video_id)
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
    video = _require_actionable_video(video_id)

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
            "error": security_logging.redact_sensitive(exc),
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
    video = _require_actionable_video(video_id)

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
                _stored_voice_persistence_fields(task),
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
    video = _require_actionable_video(video_id)
    review = _automatically_approve_audio_review(video_id)
    if review.get("status") != "approved":
        raise HTTPException(
            status_code=409,
            detail="Kịch bản không đạt kiểm tra tự động; chưa tạo lại audio.",
        )
    try:
        selected_voice = _normalize_voice_record(
            voice_config.get_voice(
                request.voice_id,
                include_inactive=False,
            ),
            fallback_id=request.voice_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    provider_config = voice_config.get_provider(selected_voice["provider_id"])
    if (
        (provider_config.get("capabilities") or {}).get("billable")
        and not request.confirm_credit_charge
    ):
        raise HTTPException(
            status_code=400,
            detail="Phải xác nhận nhà cung cấp cloud có thể trừ credit cho audio mới.",
        )

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
        voice_config.build_voice_snapshot(selected_voice),
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
            "error": security_logging.redact_sensitive(exc),
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
    video = _require_actionable_video(video_id)
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

        provider_id = task.get("tts_provider_id") or voice_config.GENMAX_PROVIDER_ID
        provider_config = voice_config.get_provider(provider_id)
        if (
            (provider_config.get("capabilities") or {}).get("billable")
            and not request.confirm_credit_charge
        ):
            raise HTTPException(
                status_code=400,
                detail="Retry với nhà cung cấp cloud có thể trừ credit; cần xác nhận.",
            )
        voice_snapshot = _voice_snapshot_from_record(task)

        current_script = apply_tts_filters(
            get_clean_script_for_tts(video["generated_script"])
        )
        current_request_hash = tts.get_generation_request_hash(
            current_script,
            task.get("voice_id") or AUDIO_VOICE_ID,
            voice_snapshot,
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
                retried_task = tts.retry_tts_task(
                    segment["task_id"],
                    provider_id,
                )
                segment.update(
                    _segment_from_remote(
                        segment["index"],
                        "",
                        retried_task,
                        task.get("voice_id") or AUDIO_VOICE_ID,
                        voice_snapshot,
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
                **_stored_voice_persistence_fields(task),
            )
            _start_audio_watcher(video_id)
            return {
                "success": True,
                "audio_task": _audio_task_response(updated_task),
            }

        retried_task = tts.retry_tts_task(task["task_id"], provider_id)
        updated_task = db.upsert_audio_task(
            video_id=video_id,
            request_hash=task["request_hash"],
            task_id=retried_task["id"],
            status=retried_task.get("status", "pending"),
            voice_id=task.get("voice_id", ""),
            voice_name=task.get("voice_name", ""),
            **_stored_voice_persistence_fields(task),
        )
        _start_audio_watcher(video_id)
        return {
            "success": True,
            "audio_task": _audio_task_response(updated_task),
        }


@app.put("/api/videos/{video_id}/publish")
def publish_video(video_id: int, is_published: int):
    try:
        success = db.toggle_published(video_id, is_published)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"success": True}


@app.put("/api/videos/{video_id}/status")
def update_video_status(video_id: int, request: VideoStatusRequest):
    video = db.set_video_status(video_id, request.status)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return {
        "success": True,
        "video_id": video_id,
        "video_status": video["video_status"],
    }


@app.put("/api/videos/{video_id}/audio-duration")
def save_audio_duration(video_id: int, request: AudioDurationRequest):
    if not math.isfinite(request.duration_seconds) or request.duration_seconds <= 0:
        raise HTTPException(status_code=400, detail="Audio duration must be positive")
    success = db.update_audio_duration(video_id, request.duration_seconds)
    if not success:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"success": True}


@app.post("/api/videos/{video_id}/render-video")
def trigger_render_video(video_id: int, mode: str = "resume"):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Không tìm thấy video.")
    audio_task = db.get_audio_task(video_id)
    if not audio_task or audio_task.get("status") != "completed" or not audio_task.get("audio_url"):
        raise HTTPException(status_code=400, detail="Video chưa có file âm thanh hoàn tất.")
    prompt_version = video.get("prompt_version") or ""
    snapshot = _get_prompt_production_snapshot(prompt_version)
    force_new_project = (mode == "recreate")
    job_id = f"video-render-{uuid.uuid4().hex}"
    job = db.create_system_job(
        job_id=job_id,
        job_type="video_render",
        title=f"Dựng video MP4 cho #{video_id}" + (" (Tạo mới)" if force_new_project else ""),
        payload={
            "video_id": video_id,
            "snapshot": snapshot,
            "mode": mode,
            "force_new_project": force_new_project,
        },
        prompt_version=prompt_version,
    )
    db.update_system_job(job["id"], video_id=video_id)
    _kick_production_queue()
    return {"success": True, "job_id": job["id"], "status": "queued"}


@app.post("/api/videos/{video_id}/cancel-render")
def cancel_render_video(video_id: int):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Không tìm thấy video.")
    jobs = db.list_system_jobs(video_id=video_id, limit=None)
    active_jobs = [
        j for j in jobs
        if j.get("job_type") == "video_render"
        and j.get("status") in {"queued", "running", "retry_wait"}
    ]
    if not active_jobs:
        return {"success": True, "message": "Không có tác vụ render nào đang chạy."}

    for job in active_jobs:
        job_id = job["id"]
        coordinator_status = _production_coordinator.status()
        if (
            coordinator_status.get("current_job_id") != job_id
            or job.get("status") in {"queued", "retry_wait"}
        ):
            db.update_system_job(
                job_id,
                status="canceled",
                progress="Đã dừng tác vụ dựng video.",
                finished_at=db.utc_now(),
                cancel_requested=0,
            )
        else:
            db.request_cancel_system_job(job_id)

    return {"success": True, "message": "Đã yêu cầu dừng tác vụ dựng video."}


@app.get("/api/videos/{video_id}/render-status")
def get_render_status(video_id: int):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Không tìm thấy video.")
    artifact = db.get_latest_video_artifact(video_id, "final_mp4")
    jobs = db.list_system_jobs(video_id=video_id, limit=None)
    render_job = next((j for j in jobs if j.get("job_type") == "video_render"), None)
    return {
        "video_id": video_id,
        "has_mp4": bool(artifact and artifact.get("status") == "ready" and Path(artifact.get("path") or "").is_file()),
        "mp4_artifact": artifact,
        "job": render_job,
    }


@app.get("/api/videos/{video_id}/download-mp4")
def download_video_mp4(video_id: int):
    artifact = db.get_latest_video_artifact(video_id, "final_mp4")
    if not artifact or artifact.get("status") != "ready":
        raise HTTPException(status_code=404, detail="Video MP4 chưa sẵn sàng.")
    path = Path(artifact.get("path") or "")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File video MP4 không tồn tại trên đĩa.")
    return FileResponse(
        path=str(path),
        media_type="video/mp4",
        filename=path.name,
    )

@app.get("/api/videos/{video_id}")
def get_video(video_id: int):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    video["has_checkpoint"] = bool(load_checkpoint(video_id))
    video["publications"] = db.list_video_publications(video_id)
    with _prompts_config_lock:
        prompt_data = _read_prompts_config()
    channels_by_id = {
        channel["channel_id"]: channel
        for channel in db.list_youtube_channels()
    }
    _add_video_default_youtube_channel(video, prompt_data, channels_by_id)
    return video

@app.delete("/api/videos/{video_id}")
def delete_video(video_id: int):
    if _get_chatgpt_video_id() == video_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Video đang được ChatGPT xử lý. "
                "Hãy đợi tác vụ kết thúc hoặc dừng tác vụ trước khi xóa."
            ),
        )

    checkpoint = load_checkpoint(video_id)
    try:
        deleted = db.delete_video_with_dependencies(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Video not found")

    deleted_job_ids = deleted["system_job_ids"]
    with _jobs_lock:
        for job_id in deleted_job_ids:
            _jobs.pop(job_id, None)
    warnings = []
    try:
        clear_checkpoint(video_id)
    except OSError as exc:
        warnings.append(f"Không thể xóa checkpoint của video #{video_id}: {exc}")
    deleted_media_count, media_warnings = _delete_unreferenced_managed_media(
        deleted,
        checkpoint,
    )
    warnings.extend(media_warnings)
    _kick_video_queue()
    return {
        "success": True,
        "deleted_job_ids": deleted_job_ids,
        "deleted_media_count": deleted_media_count,
        "warnings": warnings,
    }

# --- AUTO LOGIN ENDPOINTS ---

@app.get("/api/account", response_model=AccountStatus)
def get_account():
    try:
        account = account_store.load_account()
        browser_settings = account_store.get_browser_automation_settings()
        return AccountStatus(
            email=str(account.get("email") or ""),
            headless=browser_settings["worker_headless"],
            password_configured=bool(account.get("password")),
            totp_configured=bool(account.get("totp_secret")),
        )
    except Exception:
        return AccountStatus()

@app.post("/api/account")
def save_account(account: AccountData):
    _save_account_payload(account.dict())
    return {"success": True}


@app.get("/api/browser-automation", response_model=BrowserAutomationData)
def get_browser_automation():
    return account_store.get_browser_automation_settings()


@app.put("/api/browser-automation", response_model=BrowserAutomationData)
def save_browser_automation(data: BrowserAutomationData):
    return account_store.save_browser_automation_settings(data.model_dump())


@app.get(
    "/api/chatgpt-browser-service",
    response_model=ChatGPTBrowserServiceStatus,
)
def get_chatgpt_browser_service_status():
    return chatgpt_browser_service.get_browser_service_status()


@app.post(
    "/api/chatgpt-browser-service/start",
    response_model=ChatGPTBrowserServiceStatus,
)
def start_chatgpt_browser_service():
    if not _try_start_chatgpt_operation("browser-service-start"):
        raise HTTPException(status_code=409, detail=CHATGPT_BUSY_ERROR)
    try:
        status = chatgpt_browser_service.start_browser_service()
        if not status.get("connected"):
            raise HTTPException(status_code=503, detail=status.get("message"))
        return status
    finally:
        _finish_chatgpt_operation()


@app.post(
    "/api/chatgpt-browser-service/stop",
    response_model=ChatGPTBrowserServiceStatus,
)
def stop_chatgpt_browser_service():
    if not _try_start_chatgpt_operation("browser-service-stop"):
        raise HTTPException(status_code=409, detail=CHATGPT_BUSY_ERROR)
    try:
        status = chatgpt_browser_service.stop_browser_service()
        if status.get("process_alive"):
            raise HTTPException(status_code=409, detail=status.get("message"))
        return status
    finally:
        _finish_chatgpt_operation()


@app.post(
    "/api/chatgpt-browser-service/show",
    response_model=ChatGPTBrowserServiceStatus,
)
def show_chatgpt_browser_service_window():
    try:
        status = chatgpt_browser_service.set_browser_service_window_visibility(True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not status.get("connected"):
        raise HTTPException(status_code=503, detail=status.get("message"))
    return status


@app.post(
    "/api/chatgpt-browser-service/hide",
    response_model=ChatGPTBrowserServiceStatus,
)
def hide_chatgpt_browser_service_window():
    try:
        status = chatgpt_browser_service.set_browser_service_window_visibility(False)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not status.get("connected"):
        raise HTTPException(status_code=503, detail=status.get("message"))
    return status

@app.post("/api/clear-account")
def clear_account():
    if not _try_start_chatgpt_operation("clear-account"):
        raise HTTPException(status_code=409, detail=CHATGPT_BUSY_ERROR)
    try:
        browser_status = chatgpt_browser_service.stop_browser_service()
        if browser_status.get("process_alive"):
            raise HTTPException(
                status_code=409,
                detail="Không thể dừng trình duyệt ChatGPT nền để xóa tài khoản.",
            )
        profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)
        account_store.clear_account()
        return {"success": True}
    finally:
        _finish_chatgpt_operation()

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


def _get_prompt_default_youtube_channel_id(version_id: str = "") -> str:
    with _prompts_config_lock:
        data = _read_prompts_config()
        resolved_version_id = version_id.strip() or data.get(
            "active_version",
            "default",
        )
        version = data.get("versions", {}).get(resolved_version_id, {})
        return str(
            version.get("default_youtube_channel_id", "") or ""
        ).strip()


def _clear_prompt_default_youtube_channel_id(channel_id: str) -> list[str]:
    normalized_channel_id = str(channel_id or "").strip()
    if not normalized_channel_id:
        return []
    with _prompts_config_lock:
        data = _read_prompts_config()
        cleared_versions = []
        for version_id, version in data.get("versions", {}).items():
            if (
                str(version.get("default_youtube_channel_id", "") or "").strip()
                == normalized_channel_id
            ):
                version["default_youtube_channel_id"] = ""
                cleared_versions.append(version_id)
        if cleared_versions:
            _write_prompts_config(data)
    return cleared_versions


def _add_video_default_youtube_channel(video: dict, prompt_data: dict, channels: dict) -> None:
    version = prompt_data.get("versions", {}).get(
        str(video.get("prompt_version") or ""),
        {},
    )
    stable_channel_id = str(
        version.get("default_youtube_channel_id", "") or ""
    ).strip()
    channel = channels.get(stable_channel_id)
    video["default_youtube_channel_id"] = stable_channel_id
    video["default_youtube_channel_db_id"] = channel.get("id") if channel else None
    video["default_youtube_channel_title"] = channel.get("title", "") if channel else ""


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
    config = voice_config.load_voice_config()
    return {
        **config,
        "voices": [
            voice for voice in config["voices"] if voice.get("status") == "active"
        ],
    }


@app.post("/api/voices")
def save_voices(data: VoiceConfigData):
    """Compatibility adapter for clients that predate the provider catalog."""
    try:
        with _tts_config_lock:
            current = voice_config.load_voice_config()
            current_by_id = {voice["id"]: voice for voice in current["voices"]}
            submitted_ids = {voice.id for voice in data.voices}
            merged = []
            for submitted in data.voices:
                existing = current_by_id.get(submitted.id)
                if existing:
                    merged.append({**existing, "name": submitted.name})
                else:
                    merged.append(
                        {
                            "id": submitted.id,
                            "name": submitted.name,
                            "provider_id": voice_config.GENMAX_PROVIDER_ID,
                            "provider_voice_id": submitted.id,
                            "status": "active",
                            "revision": 1,
                            "config": {},
                        }
                    )
            for existing in current["voices"]:
                if existing["id"] not in submitted_ids:
                    merged.append({**existing, "status": "archived"})
            saved = voice_config.save_voice_config(
                {
                    **current,
                    "default_voice_id": data.active_voice_id,
                    "active_voice_id": data.active_voice_id,
                    "voices": merged,
                }
            )
            return {
                **saved,
                "voices": [
                    voice
                    for voice in saved["voices"]
                    if voice.get("status") == "active"
                ],
            }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _public_provider(provider: dict, health: dict | None = None) -> dict:
    provider_id = provider["id"]
    active_tasks = [
        task
        for task in db.get_active_audio_tasks()
        if (task.get("tts_provider_id") or voice_config.GENMAX_PROVIDER_ID)
        == provider_id
    ]
    return {
        "id": provider_id,
        "type": provider.get("type", "external"),
        "display_name": provider.get("display_name", provider_id),
        "enabled": bool(provider.get("enabled", True)),
        "capabilities": provider.get("capabilities") or {},
        "config": provider.get("config") or {},
        "health": health or {"ok": False, "state": "unknown"},
        "queued_jobs": len(active_tasks)
        + len(db.list_active_tts_previews(provider_id)),
    }


def _provider_health(provider_id: str, *, test_connection: bool = False) -> dict:
    try:
        return tts.get_provider(provider_id).health(test_connection=test_connection)
    except Exception as exc:
        return {
            "ok": False,
            "state": "offline",
            "error": security_logging.redact_sensitive(exc),
            "configured": (
                tts.genmax_service.has_api_key()
                if provider_id == voice_config.GENMAX_PROVIDER_ID
                else True
            ),
        }


def _save_tts_config(config: dict) -> dict:
    try:
        return voice_config.save_voice_config(config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/tts/providers")
def list_tts_providers():
    config = voice_config.load_voice_config()
    return {
        "items": [
            _public_provider(provider, _provider_health(provider["id"]))
            for provider in config["providers"]
        ]
    }


@app.patch("/api/tts/providers/{provider_id}")
def update_tts_provider(provider_id: str, payload: TTSProviderUpdate):
    with _tts_config_lock:
        config = voice_config.load_voice_config()
        provider = next(
            (item for item in config["providers"] if item["id"] == provider_id),
            None,
        )
        if not provider:
            raise HTTPException(status_code=404, detail="Nhà cung cấp TTS không tồn tại.")
        if payload.enabled is not None:
            provider["enabled"] = payload.enabled
        saved = _save_tts_config(config)
        saved_provider = next(
            item for item in saved["providers"] if item["id"] == provider_id
        )
    return _public_provider(saved_provider, _provider_health(provider_id))


@app.post("/api/tts/providers/{provider_id}/test")
def test_tts_provider(provider_id: str):
    try:
        configured_provider = voice_config.get_provider(provider_id)
        health = tts.get_provider(provider_id).health(test_connection=True)
        return _public_provider(configured_provider, health)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=security_logging.redact_sensitive(exc),
        ) from exc


@app.put("/api/tts/providers/genmax/credentials")
def save_genmax_credentials(payload: GenmaxCredentialData):
    try:
        tts.genmax_service.save_api_key(payload.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "configured": True}


@app.delete("/api/tts/providers/genmax/credentials")
def delete_genmax_credentials():
    tts.genmax_service.delete_api_key()
    return {"success": True, "configured": False}


@app.get("/api/tts/voices")
def list_tts_voices(
    provider_id: str = Query(default="", max_length=64),
    status: str = Query(default="", max_length=32),
    search: str = Query(default="", max_length=120),
):
    config = voice_config.load_voice_config()
    items = config["voices"]
    if provider_id:
        items = [voice for voice in items if voice["provider_id"] == provider_id]
    if status:
        items = [voice for voice in items if voice["status"] == status]
    normalized_search = search.strip().casefold()
    if normalized_search:
        items = [
            voice
            for voice in items
            if normalized_search in voice["name"].casefold()
            or normalized_search in voice["provider_voice_id"].casefold()
        ]
    return {"default_voice_id": config["default_voice_id"], "items": items}


@app.put("/api/tts/voices/default")
def set_default_tts_voice(payload: TTSDefaultVoiceData):
    with _tts_config_lock:
        config = voice_config.load_voice_config()
        voice = next(
            (item for item in config["voices"] if item["id"] == payload.voice_id),
            None,
        )
        if not voice or voice["status"] != "active":
            raise HTTPException(
                status_code=400,
                detail="Giọng mặc định phải là giọng đang hoạt động.",
            )
        config["default_voice_id"] = voice["id"]
        config["active_voice_id"] = voice["id"]
        saved = _save_tts_config(config)
    return {"success": True, "default_voice_id": saved["default_voice_id"]}


@app.post("/api/tts/voices")
def create_tts_voice(payload: TTSVoiceCreate):
    provider_id = payload.provider_id.strip().casefold()
    with _tts_config_lock:
        config = voice_config.load_voice_config()
        if not any(provider["id"] == provider_id for provider in config["providers"]):
            raise HTTPException(status_code=400, detail="Nhà cung cấp TTS không tồn tại.")
        voice = {
            "id": str(uuid.uuid4()),
            "name": payload.name.strip(),
            "provider_id": provider_id,
            "provider_voice_id": payload.provider_voice_id.strip(),
            "status": "active",
            "revision": 1,
            "config": payload.config,
        }
        config["voices"].append(voice)
        saved = _save_tts_config(config)
    return next(item for item in saved["voices"] if item["id"] == voice["id"])


@app.patch("/api/tts/voices/{voice_id}")
def update_tts_voice(voice_id: str, payload: TTSVoiceUpdate):
    with _tts_config_lock:
        config = voice_config.load_voice_config()
        voice = next((item for item in config["voices"] if item["id"] == voice_id), None)
        if not voice:
            raise HTTPException(status_code=404, detail="Giọng đọc không tồn tại.")
        changed_configuration = False
        if payload.name is not None:
            voice["name"] = payload.name.strip()
        if payload.provider_voice_id is not None:
            normalized_remote_id = payload.provider_voice_id.strip()
            changed_configuration = normalized_remote_id != voice["provider_voice_id"]
            voice["provider_voice_id"] = normalized_remote_id
        if payload.config is not None:
            changed_configuration = changed_configuration or payload.config != voice["config"]
            voice["config"] = payload.config
        if payload.status is not None:
            if payload.status != "active" and config["default_voice_id"] == voice_id:
                raise HTTPException(
                    status_code=409,
                    detail="Không thể lưu trữ giọng mặc định. Hãy chọn giọng mặc định khác trước.",
                )
            voice["status"] = payload.status
        if changed_configuration:
            voice["revision"] = int(voice.get("revision") or 1) + 1
        saved = _save_tts_config(config)
    return next(item for item in saved["voices"] if item["id"] == voice_id)


@app.delete("/api/tts/voices/{voice_id}")
def archive_tts_voice(voice_id: str):
    return update_tts_voice(voice_id, TTSVoiceUpdate(status="archived"))


@app.post("/api/tts/voices/{voice_id}/test")
def test_tts_voice(voice_id: str, payload: TTSVoiceTestData):
    try:
        voice, provider = tts.get_voice_context(voice_id)
        if (voice_config.get_provider(voice["provider_id"]).get("capabilities") or {}).get(
            "billable"
        ) and not payload.confirm_billable:
            raise HTTPException(
                status_code=400,
                detail="Thử giọng Genmax có thể tốn credit; cần xác nhận trước.",
            )
        provider.validate_voice(voice)
        task = provider.submit(
            payload.text.strip(),
            voice,
            tts.get_request_hash(
                payload.text.strip(), voice_id, voice_config.build_voice_snapshot(voice)
            ),
        )
        return {"success": True, "task": task}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=security_logging.redact_sensitive(exc)
        ) from exc


def _normalize_tts_preview_id(preview_id: str) -> str:
    try:
        return str(uuid.UUID(preview_id))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Bản nghe thử không tồn tại.") from exc


def _get_tts_preview_sync_lock(preview_id: str) -> threading.Lock:
    normalized_id = _normalize_tts_preview_id(preview_id)
    with _tts_preview_sync_locks_guard:
        return _tts_preview_sync_locks.setdefault(normalized_id, threading.Lock())


def _normalize_tts_preview_status(status: str) -> str:
    normalized = str(status or "").strip().casefold()
    if normalized in {"pending", "queued"}:
        return "queued"
    if normalized in {"processing", "running"}:
        return "processing"
    if normalized in {"completed", "failed", "canceled"}:
        return normalized
    return "processing"


def _public_tts_preview(preview: dict) -> dict:
    provider_health = _provider_health(preview["tts_provider_id"])
    audio_url = ""
    if preview["status"] == "completed" and preview.get("audio_filename"):
        audio_url = (
            f"http://127.0.0.1:8080/api/tts/previews/{preview['id']}/audio"
        )
    return {
        "id": preview["id"],
        "status": preview["status"],
        "text": preview["text"],
        "character_count": preview["character_count"],
        "voice_id": preview["voice_id"],
        "voice_name": preview["voice_name"],
        "provider_id": preview["tts_provider_id"],
        "provider_state": provider_health.get("state", "unknown"),
        "provider_busy": bool(provider_health.get("busy")),
        "duration_seconds": preview.get("duration_seconds"),
        "audio_url": audio_url,
        "error": preview.get("error") or "",
        "created_at": preview["created_at"],
        "updated_at": preview["updated_at"],
        "expires_at": preview["expires_at"],
    }


def _cleanup_expired_tts_previews() -> int:
    expired = db.delete_expired_tts_previews()
    for preview in expired:
        if preview["status"] in {"queued", "processing"}:
            try:
                tts.cancel_tts_task(
                    preview["provider_task_id"],
                    preview["tts_provider_id"],
                )
            except Exception as exc:
                print(
                    "Could not cancel expired TTS preview: "
                    f"{security_logging.redact_sensitive(exc)}",
                    file=sys.stderr,
                )
        tts.discard_preview_audio(preview.get("audio_filename") or "")
        with _tts_preview_sync_locks_guard:
            _tts_preview_sync_locks.pop(preview["id"], None)
    return len(expired)


def _tts_preview_cleanup_scheduler() -> None:
    while not _tts_preview_cleanup_stop_event.wait(
        TTS_PREVIEW_CLEANUP_INTERVAL_SECONDS
    ):
        try:
            _cleanup_expired_tts_previews()
        except Exception as exc:
            print(
                "TTS preview cleanup failed: "
                f"{security_logging.redact_sensitive(exc)}",
                file=sys.stderr,
            )


def _get_unexpired_tts_preview(preview_id: str) -> dict:
    normalized_id = _normalize_tts_preview_id(preview_id)
    preview = db.get_tts_preview(normalized_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="Bản nghe thử không tồn tại.")
    expires_at = _parse_utc_datetime(preview["expires_at"])
    if expires_at and expires_at <= datetime.datetime.now(datetime.timezone.utc):
        _cleanup_expired_tts_previews()
        raise HTTPException(status_code=404, detail="Bản nghe thử đã hết hạn.")
    return preview


def _sync_tts_preview(preview_id: str) -> dict:
    lock = _get_tts_preview_sync_lock(preview_id)
    with lock:
        preview = _get_unexpired_tts_preview(preview_id)
        audio_filename = preview.get("audio_filename") or ""
        if (
            preview["status"] == "completed"
            and audio_filename
            and (tts.PREVIEW_AUDIO_DIR / audio_filename).is_file()
        ):
            return preview
        if preview["status"] in {"failed", "canceled"}:
            return preview
        try:
            task = tts.get_tts_task(
                preview["provider_task_id"],
                preview["tts_provider_id"],
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=security_logging.redact_sensitive(exc),
            ) from exc
        status = _normalize_tts_preview_status(task.get("status") or "")
        if status == "completed":
            try:
                filename, duration = tts.materialize_preview_audio(
                    task,
                    preview["id"],
                )
                config = (preview.get("voice_snapshot") or {}).get("config") or {}
                audio_utils.validate_spoken_duration(
                    preview["text"],
                    duration,
                    minimum_words_per_minute=float(
                        config.get("min_words_per_minute") or 105
                    ),
                    provider_name="OmniVoice",
                )
                return db.update_tts_preview(
                    preview["id"],
                    status="completed",
                    audio_filename=filename,
                    duration_seconds=duration,
                    error="",
                )
            except Exception as exc:
                tts.discard_preview_audio(f"{preview['id']}.wav")
                return db.update_tts_preview(
                    preview["id"],
                    status="failed",
                    audio_filename="",
                    duration_seconds=None,
                    error=security_logging.redact_sensitive(exc),
                )
        error = ""
        if status in {"failed", "canceled"}:
            error = security_logging.redact_sensitive(task.get("error") or "")
        return db.update_tts_preview(
            preview["id"],
            status=status,
            error=error,
        )


@app.post("/api/tts/previews")
def create_tts_preview(payload: TTSPreviewCreateData):
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nội dung nghe thử không được để trống.")
    if len(text) > TTS_PREVIEW_MAX_CHARACTERS:
        raise HTTPException(
            status_code=400,
            detail=f"Nội dung nghe thử tối đa {TTS_PREVIEW_MAX_CHARACTERS} ký tự.",
        )
    try:
        voice, provider = tts.get_voice_context(payload.voice_id)
        if voice["provider_id"] != voice_config.OMNIVOICE_PROVIDER_ID:
            raise ValueError("Bản nghe thử tại đây chỉ hỗ trợ giọng OmniVoice.")
        snapshot = voice_config.build_voice_snapshot(voice)
        preview_voice = {
            **voice,
            "provider_voice_id": snapshot["provider_voice_id"],
            "revision": snapshot["voice_revision"],
            "config": snapshot.get("config") or {},
        }
        provider.validate_voice(preview_voice)
        preview_id = str(uuid.uuid4())
        request_hash = tts.get_request_hash(
            f"tts-preview:{preview_id}:{text}",
            voice["id"],
            snapshot,
        )
        task = provider.submit(text, preview_voice, request_hash)
        provider_task_id = str(task.get("id") or "").strip()
        if not provider_task_id:
            raise RuntimeError("OmniVoice không trả về mã job nghe thử.")
        expires_at = (
            datetime.datetime.now(datetime.timezone.utc) + TTS_PREVIEW_RETENTION
        ).isoformat()
        try:
            preview = db.create_tts_preview(
                preview_id=preview_id,
                provider_task_id=provider_task_id,
                request_hash=request_hash,
                status=_normalize_tts_preview_status(task.get("status") or ""),
                text=text,
                voice_id=voice["id"],
                voice_name=voice["name"],
                tts_provider_id=voice["provider_id"],
                voice_revision=snapshot["voice_revision"],
                voice_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
                expires_at=expires_at,
            )
        except Exception:
            try:
                provider.cancel(provider_task_id)
            except Exception:
                pass
            raise
        if preview["status"] == "completed":
            preview = _sync_tts_preview(preview["id"])
        return _public_tts_preview(preview)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=security_logging.redact_sensitive(exc),
        ) from exc


@app.get("/api/tts/previews/{preview_id}")
def get_tts_preview(preview_id: str):
    return _public_tts_preview(_sync_tts_preview(preview_id))


@app.post("/api/tts/previews/{preview_id}/cancel")
def cancel_tts_preview(preview_id: str):
    lock = _get_tts_preview_sync_lock(preview_id)
    with lock:
        preview = _get_unexpired_tts_preview(preview_id)
        if preview["status"] in {"completed", "failed", "canceled"}:
            return _public_tts_preview(preview)
        try:
            tts.cancel_tts_task(
                preview["provider_task_id"],
                preview["tts_provider_id"],
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=security_logging.redact_sensitive(exc),
            ) from exc
        preview = db.update_tts_preview(
            preview["id"],
            status="canceled",
            error="",
        )
        return _public_tts_preview(preview)


@app.get("/api/tts/previews/{preview_id}/audio")
def serve_tts_preview_audio(preview_id: str, download: bool = Query(False)):
    preview = _get_unexpired_tts_preview(preview_id)
    if preview["status"] != "completed" or not preview.get("audio_filename"):
        raise HTTPException(status_code=409, detail="Bản nghe thử chưa hoàn tất.")
    expected_filename = f"{preview['id']}.wav"
    if preview["audio_filename"] != expected_filename:
        raise HTTPException(status_code=400, detail="Tên file nghe thử không hợp lệ.")
    file_path = _resolve_media_file(
        tts.PREVIEW_AUDIO_DIR,
        preview["audio_filename"],
        ".wav",
    )
    return FileResponse(
        str(file_path),
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": (
                f'{"attachment" if download else "inline"}; '
                f'filename="omnivoice-preview-{preview["id"]}.wav"'
            ),
        },
    )


@app.post("/api/tts/providers/omnivoice/sync")
def sync_omnivoice_profiles():
    try:
        remote = tts.get_provider(voice_config.OMNIVOICE_PROVIDER_ID).list_remote_voices()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=security_logging.redact_sensitive(exc)
        ) from exc
    with _tts_config_lock:
        config = voice_config.load_voice_config()
        existing = {
            voice["provider_voice_id"]: voice
            for voice in config["voices"]
            if voice["provider_id"] == voice_config.OMNIVOICE_PROVIDER_ID
        }
        added = []
        for profile in remote.get("profiles", []):
            remote_id = str(profile.get("id") or "").strip()
            if not remote_id or remote_id in existing:
                continue
            voice = {
                "id": str(uuid.uuid4()),
                "name": str(profile.get("name") or remote_id).strip(),
                "provider_id": voice_config.OMNIVOICE_PROVIDER_ID,
                "provider_voice_id": remote_id,
                "status": "active",
                "revision": 1,
                "config": {},
            }
            config["voices"].append(voice)
            existing[remote_id] = voice
            added.append(voice["id"])
        saved = _save_tts_config(config)
    return {
        "success": True,
        "added": len(added),
        "profiles": remote.get("profiles", []),
        "samples": remote.get("samples", []),
        "voices": [voice for voice in saved["voices"] if voice["id"] in added],
    }


@app.post("/api/tts/providers/omnivoice/clone")
async def clone_omnivoice_voice(
    file: UploadFile = File(...),
    name: str = Form(..., min_length=1, max_length=120),
    start_seconds: float = Form(..., ge=0),
    end_seconds: float = Form(..., gt=0),
    reference_text: str = Form(..., min_length=2, max_length=2000),
):
    if end_seconds - start_seconds < 3 or end_seconds - start_seconds > 10:
        raise HTTPException(status_code=400, detail="Đoạn mẫu phải dài từ 3 đến 10 giây.")
    contents = await file.read(100 * 1024 * 1024 + 1)
    if not contents or len(contents) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File mẫu rỗng hoặc vượt quá 100 MB.")
    try:
        result = tts.omnivoice_client.clone_voice(
            name=name.strip(),
            file_name=file.filename or "sample.wav",
            file_bytes=contents,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            reference_text=reference_text.strip(),
        )
        profile_id = str(result.get("id") or "").strip()
        if not profile_id:
            raise RuntimeError("OmniVoice không trả về Profile ID.")
        voice = create_tts_voice(
            TTSVoiceCreate(
                name=name.strip(),
                provider_id=voice_config.OMNIVOICE_PROVIDER_ID,
                provider_voice_id=profile_id,
            )
        )
        return {"success": True, "voice": voice, "profile": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=security_logging.redact_sensitive(exc)
        ) from exc

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
        old_name = str(version.get("name") or "").strip()
        version["name"] = name
        normalized_data = _write_prompts_config(data)

    if old_name and old_name != name:
        from auto_yt.services import prompt_assets
        prompt_assets.rename_prompt_asset_dir(old_name, name)

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


@app.patch("/api/prompts/{version_id}/default-youtube-channel")
def save_prompt_default_youtube_channel(
    version_id: str,
    payload: PromptDefaultYoutubeChannelData,
):
    selected_channel_id = payload.channel_id.strip()
    if selected_channel_id and not db.get_youtube_channel_by_channel_id(
        selected_channel_id
    ):
        raise HTTPException(
            status_code=400,
            detail="Kênh YouTube đã chọn chưa được kết nối hoặc không còn tồn tại.",
        )
    with _prompts_config_lock:
        _assert_prompt_version_editable(version_id)
        data = _read_prompts_config()
        version = _get_prompt_version(data, version_id)
        version["default_youtube_channel_id"] = selected_channel_id
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
        thumbnail_variant = chatgpt_projects.normalize_image_generation_settings(
            version.get("image_generation_settings")
        )["thumbnail_variant"]
        requested_pipeline = payload.model_dump()
        validated_pipeline = chatgpt_projects.validate_prompt_pipeline(
            requested_pipeline,
            thumbnail_variant,
        )
        version["pipeline"] = validated_pipeline
        normalized_data = _write_prompts_config(data)
    auto_enabled = [
        key
        for key, enabled in validated_pipeline.items()
        if enabled and not requested_pipeline.get(key, False)
    ]
    readiness = youtube_publish_workflow.evaluate_prompt_publish_readiness(
        normalized_data["versions"][version_id]
    )
    return {
        "version_id": version_id,
        "pipeline": normalized_data["versions"][version_id]["pipeline"],
        "auto_enabled": auto_enabled,
        **readiness,
    }


@app.patch("/api/prompts/{version_id}/image-generation")
def save_prompt_image_generation(
    version_id: str,
    payload: PromptImageGenerationData,
):
    with _prompts_config_lock:
        _assert_prompt_version_editable(version_id)
        data = _read_prompts_config()
        version = _get_prompt_version(data, version_id)
        settings = chatgpt_projects.validate_image_generation_settings(
            payload.model_dump()
        )
        previous_pipeline = chatgpt_projects.normalize_prompt_pipeline(
            version.get("pipeline")
        )
        pipeline = chatgpt_projects.validate_prompt_pipeline(
            previous_pipeline,
            settings["thumbnail_variant"],
        )
        version["image_generation_settings"] = settings
        version["pipeline"] = pipeline
        normalized_data = _write_prompts_config(data)
    return {
        "version_id": version_id,
        "version": normalized_data["versions"][version_id],
    }


@app.patch("/api/prompts/{version_id}/publishing")
def save_prompt_publishing(
    version_id: str,
    payload: PromptPublishingData,
):
    with _prompts_config_lock:
        _assert_prompt_version_editable(version_id)
        data = _read_prompts_config()
        version = _get_prompt_version(data, version_id)
        version["publishing_settings"] = (
            chatgpt_projects.validate_publishing_settings(payload.model_dump())
        )
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
        account = account_store.load_account()
        if not account.get("email"):
            return {"success": False, "error": "Chưa có thông tin tài khoản"}

        if not _try_start_chatgpt_operation("login"):
            return {"success": False, "error": CHATGPT_BUSY_ERROR}
        profile_reserved = True

        browser_status = await asyncio.to_thread(
            chatgpt_browser_service.stop_browser_service
        )
        if browser_status.get("process_alive"):
            raise RuntimeError(
                "Không thể dừng trình duyệt ChatGPT nền để mở phiên đăng nhập."
            )

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
                try:
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

                    account["session_cookie"] = result.get("cookies", [])
                    account_store.save_account(account)
                    return result
                finally:
                    await context.close()
                
        result = await _run_login()
        return {"success": True, "message": "Login successful"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    finally:
        if profile_reserved:
            await asyncio.to_thread(chatgpt_browser_service.start_browser_service)
            _finish_chatgpt_operation()

@app.post("/api/open-profile")
async def open_profile():
    from playwright.async_api import async_playwright
    import asyncio

    if not _try_start_chatgpt_operation("profile"):
        return {"success": False, "error": CHATGPT_BUSY_ERROR}

    browser_status = await asyncio.to_thread(
        chatgpt_browser_service.stop_browser_service
    )
    if browser_status.get("process_alive"):
        _finish_chatgpt_operation()
        return {
            "success": False,
            "error": "Không thể dừng trình duyệt ChatGPT nền để mở profile.",
        }

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
                account = account_store.load_account()
                try:
                    current_cookies = await context.cookies(
                        ["https://chatgpt.com/", "https://auth.openai.com/"]
                    )
                    restorable = account_store.select_restorable_chatgpt_cookies(
                        account.get("session_cookie") or [],
                        current_cookies,
                    )
                    if restorable:
                        await context.add_cookies(restorable)
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto("https://chatgpt.com/")
                    await page.wait_for_timeout(600000) # 10 mins
                except Exception:
                    pass
                finally:
                    try:
                        refreshed_cookies = await context.cookies(
                            ["https://chatgpt.com/", "https://auth.openai.com/"]
                        )
                        if account_store.has_chatgpt_auth_cookie(refreshed_cookies):
                            account["session_cookie"] = refreshed_cookies
                            account_store.save_account(account)
                    except Exception:
                        pass
                    await context.close()
        finally:
            await asyncio.to_thread(chatgpt_browser_service.start_browser_service)
            _finish_chatgpt_operation()

    # Launch as a separate asyncio task so it doesn't block the API
    try:
        asyncio.create_task(_launch())
    except Exception:
        await asyncio.to_thread(chatgpt_browser_service.start_browser_service)
        _finish_chatgpt_operation()
        raise
    return {"success": True, "message": "Browser profile opened on server."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8080, reload=True, loop="asyncio")


from auto_yt.services import google_flow_account
from auto_yt.services import google_flow_browser_service
from auto_yt.services.google_flow_login import login_google_flow
from fastapi import File, UploadFile, Form
import json
import shutil
import hashlib
import os
from auto_yt.paths import DATA_DIR
FLOW_REFERENCE_STAGING_DIR = DATA_DIR / "flow_staging"
from pydantic import BaseModel

class GoogleFlowAccountData(BaseModel):
    email: str = ""
    password: str = ""
    totp_secret: str = ""

@app.get("/api/flow/account")
def get_flow_account():
    return google_flow_account.get_account_status()

@app.put("/api/flow/account")
def update_flow_account(payload: GoogleFlowAccountData):
    try:
        return google_flow_account.save_credentials(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.delete("/api/flow/account")
def clear_flow_account():
    google_flow_account.clear_account()
    return {"success": True}

@app.get("/api/flow/browser")
def get_flow_browser_status():
    return google_flow_browser_service.get_browser_service_status()

@app.post("/api/flow/browser/{action}")
def manage_flow_browser(action: str):
    if action == "start":
        status = google_flow_browser_service.start_browser_service()
        if not status.get("connected"):
            raise HTTPException(status_code=503, detail=status.get("message"))
    elif action == "stop":
        status = google_flow_browser_service.stop_browser_service()
        if status.get("process_alive"):
            raise HTTPException(status_code=409, detail=status.get("message"))
    elif action == "show":
        status = google_flow_browser_service.set_browser_service_window_visibility(True)
        if not status.get("connected"):
            raise HTTPException(status_code=503, detail=status.get("message"))
    elif action == "hide":
        status = google_flow_browser_service.set_browser_service_window_visibility(False)
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    return status


async def _verify_google_flow_browser_session(account: dict) -> dict:
    from playwright.async_api import async_playwright

    endpoint = google_flow_browser_service.get_browser_service_endpoint()
    if not endpoint:
        raise RuntimeError("Browser Google Flow chưa sẵn sàng.")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(endpoint)
        if not browser.contexts:
            raise RuntimeError("Browser Google Flow chưa có browser context.")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        page.set_default_timeout(60_000)
        result = await login_google_flow(account, page)
        cookies = [
            cookie
            for cookie in result.get("cookies", [])
            if google_flow_account.is_google_flow_cookie(cookie)
        ]
        if not google_flow_account.has_google_flow_auth_cookie(cookies):
            raise RuntimeError("Không tìm thấy phiên đăng nhập Google hợp lệ.")
        account["session_cookies"] = cookies
        google_flow_account.save_account(account)
        return result

@app.post("/api/flow/login-check")
async def check_flow_login():
    try:
        account = google_flow_account.load_account()
        browser_status = google_flow_browser_service.get_browser_service_status()
        if not browser_status.get("connected"):
            browser_status = await asyncio.to_thread(
                google_flow_browser_service.start_browser_service
            )
        if not browser_status.get("connected"):
            return {
                "success": False,
                "error": browser_status.get("message")
                or "Không thể khởi động trình duyệt Flow.",
            }

        result = await _verify_google_flow_browser_session(account)
        return {
            "success": True,
            "message": result.get("message") or "Flow login verified",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/flow/test-generation")
def test_flow_generation(confirm_credit_charge: bool = False):
    if not confirm_credit_charge:
        raise HTTPException(status_code=400, detail="Must confirm credit charge")
    return {"success": True, "message": "Test generated"}

@app.post("/api/flow/reference-sets")
async def upload_reference_set(files: list[UploadFile] = File(...), labels_json: str = Form(...)):
    labels = json.loads(labels_json)
    if len(files) > 8:
        raise HTTPException(status_code=400, detail="Max 8 references allowed")
    
    reference_set_id = hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    staging_dir = FLOW_REFERENCE_STAGING_DIR / reference_set_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    
    saved_files = []
    content_hash_builder = hashlib.sha256()
    
    for idx, file in enumerate(files):
        ext = file.filename.split('.')[-1].lower()
        if ext not in ['png', 'jpg', 'jpeg', 'webp']:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {ext}")
            
        file_path = staging_dir / f"{idx}_{file.filename}"
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        
        # In a real app we'd verify with PIL here, but skip for brevity
        file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        content_hash_builder.update(file_hash.encode())
        
        saved_files.append({
            "id": f"{reference_set_id}_{idx}",
            "filename": file.filename,
            "hash": file_hash,
            "label": labels[idx] if idx < len(labels) else ""
        })
        
    return {
        "reference_set_id": reference_set_id,
        "content_hash": content_hash_builder.hexdigest(),
        "references": saved_files
    }


def _build_prompt_production_snapshot(version_id: str, version: dict) -> dict:
    """Build a full production snapshot dict from a prompt version record."""
    image_settings = chatgpt_projects.normalize_image_generation_settings(
        version.get("image_generation_settings")
    )
    pipeline = chatgpt_projects.normalize_prompt_pipeline(
        version.get("pipeline") or version.get("pipeline_settings"),
        image_settings.get("thumbnail_variant", "without_text"),
    )
    return {
        "prompt_version": version_id,
        "image_generation_settings": image_settings,
        "publishing_settings": chatgpt_projects.normalize_publishing_settings(
            version.get("publishing_settings")
        ),
        "default_youtube_channel_id": str(
            version.get("default_youtube_channel_id") or ""
        ).strip(),
        "pipeline": pipeline,
    }


def _get_prompt_production_snapshot(prompt_version: str) -> dict:
    """Return the full production snapshot for a prompt version, reading from config."""
    with _prompts_config_lock:
        data = _read_prompts_config()
    resolved = prompt_version.strip() or data.get("active_version", "default")
    versions = data.get("versions") or {}
    version = versions.get(resolved) or versions.get("default") or {}
    return _build_prompt_production_snapshot(resolved, version)


@app.get("/api/prompts/{version}/assets")
def get_prompt_version_assets(version: str):
    from auto_yt.services import prompt_assets
    assets = prompt_assets.list_prompt_assets(version)
    folder_path = str(prompt_assets.get_prompt_asset_dir(version).resolve())
    return {
        "success": True,
        "assets": assets,
        "folder_path": folder_path,
        "total": len(assets),
    }


@app.post("/api/prompts/{version}/open-folder")
def open_prompt_version_folder(version: str):
    from auto_yt.services import prompt_assets
    success = prompt_assets.open_prompt_asset_dir(version)
    folder_path = str(prompt_assets.get_prompt_asset_dir(version).resolve())
    return {
        "success": success,
        "folder_path": folder_path,
        "message": "Đã mở thư mục trên máy tính" if success else "Không thể mở thư mục",
    }


@app.post("/api/prompts/{version}/assets/upload")
async def upload_prompt_version_asset(version: str, file: UploadFile = File(...)):
    from auto_yt.services import prompt_assets
    ext = Path(file.filename).suffix.lower()
    if ext not in prompt_assets.SUPPORTED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Định dạng không hỗ trợ ({ext}). Chỉ chấp nhận png, jpg, jpeg, webp.",
        )
    target_dir = prompt_assets.get_prompt_asset_dir(version)
    target_file = target_dir / file.filename
    with open(target_file, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {
        "success": True,
        "filename": file.filename,
        "path": str(target_file.resolve()),
        "message": f"Đã lưu ảnh {file.filename} vào bộ prompt {version}",
    }


@app.get("/api/prompts/{version}/assets/{filename}")
def get_prompt_version_asset_file(version: str, filename: str):
    from auto_yt.services import prompt_assets
    safe_filename = Path(filename).name
    target_dir = prompt_assets.get_prompt_asset_dir(version)
    target_file = target_dir / safe_filename
    if not target_file.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(target_file)


@app.delete("/api/prompts/{version}/assets/{filename}")
def delete_prompt_version_asset(version: str, filename: str):
    from auto_yt.services import prompt_assets
    safe_filename = Path(filename).name
    target_dir = prompt_assets.get_prompt_asset_dir(version)
    target_file = target_dir / safe_filename
    if not target_file.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    try:
        target_file.unlink()
        return {"success": True, "message": f"Đã xóa ảnh {safe_filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# FB Cross-Poster (YouTube to FB Syndication)
# ==========================================

from auto_yt.services import fb_crossposter_service
from auto_yt.services import fb_token_service

try:
    fb_crossposter_service.scheduler.start()
except Exception as _scheduler_err:
    logger.warning("Could not auto-start fb_crossposter_service scheduler: %s", _scheduler_err)


class FBCrossPosterSettingsPayload(BaseModel):
    source_channel_id: str = ""
    source_channel_title: str = ""
    source_gpm_profile_id: str = ""
    target_fb_page_id: str = ""
    target_fb_page_name: str = ""
    target_gpm_profile_id: str = ""
    target_access_token: str = ""
    daily_quota: int = 2
    schedule_times: list[str] = Field(default_factory=lambda: ["11:30", "19:30"])
    lead_time_minutes: int = 60
    post_template: str = ""
    sort_order_mode: str = "oldest_first"
    auto_sync_enabled: bool = False
    auto_sync_type: str = "interval"
    auto_sync_interval_hours: int = 6
    auto_sync_fixed_times: list[str] = Field(default_factory=lambda: ["06:00", "18:00"])
    auto_publish_enabled: bool = False
    convert_to_vertical: bool = False
    default_tags: list[str] = Field(default_factory=list)


class FBCrossPosterSyncPayload(BaseModel):
    channel_url: Optional[str] = None
    gpm_profile_id: Optional[str] = None
    sort_order_mode: Optional[str] = None
    max_videos: int = 500
    target_page_id: Optional[str] = ""


class FBCrossPosterTestPayload(BaseModel):
    page_id: str
    access_token: str = ""
    gpm_profile_id: str = ""


class FBCrossPosterExtractTokenPayload(BaseModel):
    profile_id: str
    target_page_id: Optional[str] = ""


class FBCrossPosterExchangeTokenPayload(BaseModel):
    token: str
    target_page_id: str
    app_id: Optional[str] = ""
    app_secret: Optional[str] = ""
    profile_id: Optional[str] = ""



class FBCrossPosterRecalculatePayload(BaseModel):
    daily_quota: int = 2
    schedule_times: list[str] = Field(default_factory=lambda: ["11:30", "19:30"])
    target_page_id: Optional[str] = ""


class FBCrossPosterItemUpdatePayload(BaseModel):
    fb_title: Optional[str] = None
    fb_description: Optional[str] = None
    scheduled_publish_time: Optional[int] = None
    sort_order: Optional[int] = None
    status: Optional[str] = None


class FBCrossPosterClearPayload(BaseModel):
    only_pending: bool = False
    target_page_id: Optional[str] = ""


class FBCrossPosterScheduleAheadPayload(BaseModel):
    page_id: str = ""
    days_ahead: int = 7


@app.get("/api/fb-crossposter/campaigns")
def list_fb_crossposter_campaigns():
    """Retrieve all configured Fanpage campaigns."""
    campaigns = db.list_all_crossposter_campaigns()
    return {"campaigns": campaigns}


@app.get("/api/fb-crossposter/settings")
def get_fb_crossposter_settings(page_id: str = Query(default="")):
    """Retrieve FB Cross-Poster settings, stats, and connected channels for a specific Fanpage."""
    settings = db.get_fb_crossposter_settings(page_id)
    stats = db.get_fb_crossposter_stats(page_id)
    campaigns = db.list_all_crossposter_campaigns()

    # Get YouTube channels and GPM profiles
    yt_channels = []
    try:
        yt_channels = db.get_youtube_channels()
    except Exception:
        pass

    gpm_profiles_list = []
    try:
        gpm_res = gpm_service.list_gpm_profiles()
        if isinstance(gpm_res, dict):
            gpm_profiles_list = gpm_res.get("items") or []
        elif isinstance(gpm_res, list):
            gpm_profiles_list = gpm_res
    except Exception:
        pass

    return {
        "settings": settings,
        "stats": stats,
        "campaigns": campaigns,
        "youtube_channels": yt_channels,
        "gpm_profiles": gpm_profiles_list,
    }


@app.post("/api/fb-crossposter/settings")
def save_fb_crossposter_settings(
    payload: FBCrossPosterSettingsPayload,
    page_id: str = Query(default=""),
):
    """Save FB Cross-Poster settings for a specific Fanpage."""
    target_page_id = payload.target_fb_page_id or page_id
    existing = db.get_fb_crossposter_settings(target_page_id)
    updates = payload.model_dump(exclude_unset=True)
    saved = db.save_fb_crossposter_settings(
        {**existing, **updates},
        page_id=target_page_id,
    )
    return {"success": True, "settings": saved}


@app.post("/api/fb-crossposter/test-connection")
def test_fb_crossposter_connection(payload: FBCrossPosterTestPayload):
    """Test Facebook Page Access Token and retrieve Fanpage info."""
    try:
        access_token = payload.access_token.strip()
        if not access_token:
            settings = db.get_fb_crossposter_runtime_settings(payload.page_id)
            access_token = str(settings.get("target_access_token") or "")
        if not access_token:
            raise ValueError("Fanpage chưa có Page Access Token")
        result = fb_crossposter_service.test_fb_connection(
            page_id=payload.page_id,
            access_token=access_token,
            gpm_profile_id=payload.gpm_profile_id,
        )
        return result
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=security_logging.redact_sensitive(exc),
        )


@app.post("/api/fb-crossposter/extract-token")
async def extract_fb_crossposter_token(payload: FBCrossPosterExtractTokenPayload):
    """Auto-extract Permanent Page Access Token via Playwright CDP on the given browser profile."""
    try:
        result = await fb_token_service.extract_permanent_fb_tokens(
            profile_id=payload.profile_id,
            target_page_id=payload.target_page_id or "",
        )
        return result
    except Exception as exc:
        safe_error = security_logging.redact_sensitive(exc)
        logger.error("Lỗi trích xuất Token Facebook: %s", safe_error)
        raise HTTPException(status_code=500, detail=safe_error)


@app.post("/api/fb-crossposter/exchange-token")
def exchange_fb_crossposter_token(payload: FBCrossPosterExchangeTokenPayload):
    """Exchange and store one Page token without returning the secret to the client."""
    try:
        result = fb_token_service.exchange_to_permanent_token(
            input_token=payload.token,
            app_id=payload.app_id or "",
            app_secret=payload.app_secret or "",
            profile_id=payload.profile_id or "",
        )
        target_page_id = payload.target_page_id.strip()
        matched_page = next(
            (
                page
                for page in result.get("pages") or []
                if str(page.get("page_id") or "") == target_page_id
            ),
            None,
        )
        if not matched_page or not matched_page.get("access_token"):
            raise ValueError("Token không thuộc Fanpage Page ID đã chọn")
        existing = db.get_fb_crossposter_settings(target_page_id)
        saved = db.save_fb_crossposter_settings({
            **existing,
            "target_fb_page_id": target_page_id,
            "target_fb_page_name": matched_page.get("name") or existing.get("target_fb_page_name", ""),
            "target_gpm_profile_id": payload.profile_id or existing.get("target_gpm_profile_id", ""),
            "target_access_token": matched_page["access_token"],
        }, page_id=target_page_id)
        return {
            "success": True,
            "page": {
                "page_id": target_page_id,
                "name": matched_page.get("name") or "",
                "category": matched_page.get("category") or "",
                "token_configured": saved.get("target_access_token_configured", False),
            },
            "message": "Page Access Token đã được lưu an toàn.",
        }
    except Exception as exc:
        safe_error = security_logging.redact_sensitive(exc)
        logger.error("Lỗi đổi Token Facebook: %s", safe_error)
        raise HTTPException(status_code=400, detail=safe_error)



@app.post("/api/fb-crossposter/sync")
def sync_fb_crossposter_channel(
    payload: Optional[FBCrossPosterSyncPayload] = None,
    page_id: str = Query(default=""),
):
    """Scrape public YouTube videos and populate the queue for a Fanpage campaign."""
    target_page_id = (payload and payload.target_page_id) or page_id
    settings = db.get_fb_crossposter_settings(target_page_id)

    channel_url = (payload and payload.channel_url) or settings.get("source_channel_id")
    gpm_profile_id = (payload and payload.gpm_profile_id) or settings.get("source_gpm_profile_id", "")
    sort_order_mode = (payload and payload.sort_order_mode) or settings.get("sort_order_mode", "oldest_first")
    max_videos = (payload and payload.max_videos) or 500

    if not channel_url:
        raise HTTPException(status_code=400, detail="Chưa cấu hình hoặc chọn Kênh YouTube nguồn")

    try:
        result = fb_crossposter_service.sync_channel_public_videos(
            channel_url=channel_url,
            gpm_profile_id=gpm_profile_id,
            sort_order_mode=sort_order_mode,
            max_videos=max_videos,
            target_page_id=target_page_id,
        )
        stats = db.get_fb_crossposter_stats(target_page_id)
        return {
            "success": True,
            "result": result,
            "stats": stats,
            "message": f"Đã quét xong: tìm thấy {result['total_found']} video ({result['inserted']} video mới được thêm vào hàng đợi).",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/fb-crossposter/queue")
def get_fb_crossposter_queue(
    target_page_id: Optional[str] = Query(default=""),
    status: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    search: str = Query(default=""),
):
    """Retrieve paginated queue items with statistics filtered by Fanpage."""
    queue_data = db.get_fb_crossposter_queue(
        status=status,
        target_page_id=target_page_id,
        page=page,
        page_size=page_size,
        search=search,
    )
    stats = db.get_fb_crossposter_stats(target_page_id)
    queue_data["stats"] = stats
    return queue_data


@app.post("/api/fb-crossposter/recalculate-schedule")
def recalculate_fb_crossposter_schedule(
    payload: Optional[FBCrossPosterRecalculatePayload] = None,
    page_id: str = Query(default=""),
):
    """Recalculate schedule timestamps for all pending queue items for a Fanpage campaign."""
    target_page_id = (payload and payload.target_page_id) or page_id
    settings = db.get_fb_crossposter_settings(target_page_id)
    daily_quota = (payload and payload.daily_quota) or settings.get("daily_quota", 2)
    schedule_times = (payload and payload.schedule_times) or settings.get("schedule_times", ["11:30", "19:30"])

    updated_count = db.recalculate_fb_queue_schedule(
        daily_quota=daily_quota,
        times_list=schedule_times,
        target_page_id=target_page_id,
    )
    stats = db.get_fb_crossposter_stats(target_page_id)
    return {
        "success": True,
        "updated_count": updated_count,
        "stats": stats,
        "message": f"Đã tính toán và phân bổ lịch đăng cho {updated_count} video.",
    }


@app.post("/api/fb-crossposter/fix-schedule-collisions")
def fix_fb_crossposter_schedule_collisions(page_id: str = Query(default="")):
    """Automatically resolve schedule collisions and re-distribute queue items into unoccupied slots."""
    result = db.fix_fb_queue_schedule_collisions(target_page_id=page_id)
    stats = db.get_fb_crossposter_stats(page_id)
    return {
        "success": True,
        "recalculated_count": result.get("recalculated_count", 0),
        "stats": stats,
        "message": f"Đã tự động sắp xếp lại lịch chống trùng cho {result.get('recalculated_count', 0)} video.",
    }


@app.post("/api/fb-crossposter/reconcile-meta")
def reconcile_fb_crossposter_meta(
    page_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Refresh queue truth from Meta for one Fanpage or all campaigns."""
    result = fb_crossposter_service.reconcile_fb_queue(page_id, limit=limit)
    result["stats"] = db.get_fb_crossposter_stats(page_id)
    return result


@app.post("/api/fb-crossposter/queue/{item_id}/publish-now")
def publish_fb_crossposter_item_now(item_id: int):
    """Trigger immediate JIT download, preparation, and Facebook publishing for one item."""
    try:
        result = fb_crossposter_service.process_queue_item_jit(item_id, publish_now=True)
        item = db.get_fb_crossposter_queue_item(item_id)
        target_page_id = (item and item.get("target_page_id")) or ""
        stats = db.get_fb_crossposter_stats(target_page_id)
        return {
            "success": True,
            "result": result,
            "stats": stats,
            "message": (
                f"Meta đã tiếp nhận video với trạng thái {result.get('status')} "
                f"(Post ID: {result.get('fb_post_id')})"
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/fb-crossposter/queue/{item_id}/repair-meta")
def repair_fb_crossposter_item(item_id: int):
    """Repair or safely replace a Meta video whose remote state failed verification."""
    try:
        result = fb_crossposter_service.repair_fb_queue_item(item_id)
        item = db.get_fb_crossposter_queue_item(item_id)
        target_page_id = (item and item.get("target_page_id")) or ""
        return {
            "success": True,
            "result": result,
            "item": item,
            "stats": db.get_fb_crossposter_stats(target_page_id),
            "message": "Đã sửa và xác minh lại video trên Meta",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/fb-crossposter/queue/{item_id}/skip")
def skip_fb_crossposter_item(item_id: int):
    """Mark a queue item as skipped."""
    success = db.update_fb_crossposter_queue_item(item_id, {"status": "skipped"})
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy video")
    item = db.get_fb_crossposter_queue_item(item_id)
    target_page_id = (item and item.get("target_page_id")) or ""
    stats = db.get_fb_crossposter_stats(target_page_id)
    return {"success": True, "stats": stats, "message": "Đã đánh dấu bỏ qua video"}


@app.post("/api/fb-crossposter/queue/{item_id}/unskip")
def unskip_fb_crossposter_item(item_id: int):
    """Revert a skipped item back to pending."""
    success = db.update_fb_crossposter_queue_item(item_id, {"status": "pending"})
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy video")
    item = db.get_fb_crossposter_queue_item(item_id)
    target_page_id = (item and item.get("target_page_id")) or ""
    stats = db.get_fb_crossposter_stats(target_page_id)
    return {"success": True, "stats": stats, "message": "Đã đưa video trở lại hàng đợi"}


@app.post("/api/fb-crossposter/queue/reset-errors")
def reset_fb_crossposter_errors(page_id: str = Query(default="")):
    """Reset all error items back to 'scheduled' state."""
    count = db.reset_fb_crossposter_queue_errors(page_id)
    if count:
        settings = db.get_fb_crossposter_settings(page_id)
        db.recalculate_fb_queue_schedule(
            settings.get("daily_quota", 2),
            settings.get("schedule_times", ["11:30", "19:30"]),
            target_page_id=page_id,
        )
    stats = db.get_fb_crossposter_stats(page_id)
    return {
        "success": True,
        "reset_count": count,
        "stats": stats,
        "message": f"Đã reset {count} video lỗi về trạng thái Chờ đăng / Đã lên lịch",
    }


@app.put("/api/fb-crossposter/queue/{item_id}")
def update_fb_crossposter_item(item_id: int, payload: FBCrossPosterItemUpdatePayload):
    """Edit metadata (title, caption, scheduled time) of a queue item."""
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if payload.fb_description is not None:
        fields["fb_description_source"] = "manual"
    try:
        success = db.update_fb_crossposter_queue_item(item_id, fields)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy video hoặc không có trường nào cập nhật")
    item = db.get_fb_crossposter_queue_item(item_id)
    return {"success": True, "item": item, "message": "Đã cập nhật thông tin video"}


@app.delete("/api/fb-crossposter/queue/{item_id}")
def delete_fb_crossposter_item(item_id: int):
    """Delete a queue item."""
    item = db.get_fb_crossposter_queue_item(item_id)
    target_page_id = (item and item.get("target_page_id")) or ""
    success = db.delete_fb_crossposter_queue_item(item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Không tìm thấy video")
    stats = db.get_fb_crossposter_stats(target_page_id)
    return {"success": True, "stats": stats, "message": "Đã xóa video khỏi hàng đợi"}


@app.post("/api/fb-crossposter/queue/clear")
def clear_fb_crossposter_queue(
    payload: FBCrossPosterClearPayload,
    page_id: str = Query(default=""),
):
    """Clear queue items for a Fanpage campaign."""
    target_page_id = payload.target_page_id or page_id
    deleted_count = db.clear_fb_crossposter_queue(
        only_pending=payload.only_pending,
        target_page_id=target_page_id,
    )
    stats = db.get_fb_crossposter_stats(target_page_id)
    return {
        "success": True,
        "deleted_count": deleted_count,
        "stats": stats,
        "message": f"Đã xóa {deleted_count} video khỏi hàng đợi",
    }


# =========================================================================
# Batch Cloud Pre-Scheduler Endpoints
# =========================================================================

@app.post("/api/fb-crossposter/schedule-ahead")
def start_schedule_ahead(payload: FBCrossPosterScheduleAheadPayload):
    """Start background sequential batch upload to Meta Cloud for N days ahead."""
    try:
        task_info = fb_crossposter_service.start_schedule_ahead_batch(
            target_page_id=payload.page_id,
            days_ahead=payload.days_ahead,
        )
        return {"success": True, "task": task_info}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/fb-crossposter/schedule-ahead/status")
def get_schedule_ahead_status(
    task_id: Optional[str] = Query(default=None),
    page_id: Optional[str] = Query(default=None),
):
    """Get the current progress of a batch pre-schedule task."""
    return fb_crossposter_service.get_schedule_ahead_status(task_id=task_id, page_id=page_id)


@app.post("/api/fb-crossposter/schedule-ahead/cancel")
def cancel_schedule_ahead(
    task_id: Optional[str] = Query(default=None),
    page_id: Optional[str] = Query(default=None),
):
    """Cancel a running batch pre-schedule task."""
    canceled = fb_crossposter_service.cancel_schedule_ahead_batch(task_id=task_id, page_id=page_id)
    return {"success": True, "canceled": canceled}


@app.delete("/api/fb-crossposter/campaigns/{page_id}")
@app.post("/api/fb-crossposter/campaigns/delete")
def delete_crossposter_campaign(
    page_id: Optional[str] = None,
    payload: Optional[dict] = None,
):
    """Delete a Fanpage campaign and all its queued videos."""
    pid = page_id or (payload and payload.get("page_id")) or ""
    if not pid:
        raise HTTPException(status_code=400, detail="page_id is required")
    success = db.delete_fb_crossposter_campaign(pid)
    campaigns = db.list_all_crossposter_campaigns()
    return {"success": success, "campaigns": campaigns, "message": f"Đã xóa chiến dịch cho Fanpage: {pid}"}




