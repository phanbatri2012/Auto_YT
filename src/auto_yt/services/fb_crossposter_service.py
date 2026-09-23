"""Facebook Cross-Poster Service for YouTube to Facebook Syndication.

Handles:
- YouTube public video scanning (oldest/newest first) via GPM proxy.
- Metadata sanitization (removing YouTube links, formatting hashtags, applying templates).
- Meta Graph API video uploads with independent network routing (GPM Proxy or Direct Local IP).
- Just-in-Time (JIT) downloads and automatic cleanup.
- Background scheduler for auto-sync and auto-publishing.
"""

from __future__ import annotations

import asyncio
import datetime
import io
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError
from yt_dlp import YoutubeDL

from auto_yt.services.youtube_downloader import ensure_ffmpeg_directory
import auto_yt.services.database as db
from auto_yt.paths import DATA_DIR
from auto_yt.services import gpm_service, proxy_utils, security_logging

logger = logging.getLogger(__name__)

TEMP_DOWNLOAD_DIR = DATA_DIR / "fb_crossposter_temp"
GRAPH_API_VERSION = "v26.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
GRAPH_VIDEO_API_BASE = f"https://graph-video.facebook.com/{GRAPH_API_VERSION}"
META_THUMBNAIL_MAX_BYTES = 10 * 1024 * 1024
VERTICAL_VIDEO_WIDTH = 1080
VERTICAL_VIDEO_HEIGHT = 1920
MAX_CAPTION_HASHTAGS = 5
MAX_CUSTOM_LABELS = 8
MAX_META_CONTENT_TAGS = 10

# Concurrency guards for sync
_sync_lock = threading.Lock()
_active_sync_targets: set[str] = set()

# Pattern to clean YouTube links
YT_LINKS_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)\S+",
    flags=re.IGNORECASE,
)
# Pattern to clean timestamps like "01:23 - Intro", "[04:50] Topic"
TIMESTAMPS_PATTERN = re.compile(
    r"^\s*[\[\(]?\d{1,2}:\d{2}(?::\d{2})?[\]\)]?\s*[-–—:]?.*$",
    flags=re.MULTILINE,
)
# Pattern for subscribe CTA text
SUBSCRIBE_CTA_PATTERN = re.compile(
    r"^\s*(?:đăng ký|subscribe|sub|nhấn chuông|đăng kí|kênh youtube|like & share|theo dõi|follow)\s*.*$",
    flags=re.MULTILINE | re.IGNORECASE,
)
DESCRIPTION_HEADER_PATTERN = re.compile(
    r"^\s*(?:📄\s*)?(?:nội dung\s+)?mô tả(?:\s+video)?\s*:\s*$",
    flags=re.MULTILINE | re.IGNORECASE,
)
# Pattern for disclaimer blocks (e.g. "Lưu Ý: Các nội dung trong video...")
DISCLAIMER_PATTERN = re.compile(
    r"(?:\n\s*[-=_~*]{3,}\s*\n|\n|^)\s*(?:lưu\s*ý|disclaimer|chú\s*thích|miễn\s*trừ\s*trách\s*nhiệm)\s*:\s*.*",
    flags=re.DOTALL | re.IGNORECASE,
)
# Pattern for channel introduction boilerplate enclosed between separator lines
CHANNEL_INTRO_PATTERN = re.compile(
    r"(?:^|\n)\s*[-=_~*]{3,}\s*\n.*?(?:là nơi|chào mừng|kênh mang đến|nơi để|đây là kênh|chúng tôi mang đến).*?(?=(?:\n\s*[-=_~*]{3,}\s*(?:\n|$)|$))",
    flags=re.DOTALL | re.IGNORECASE,
)
# Generic separator lines
SEPARATOR_PATTERN = re.compile(
    r"^\s*[-=_~*]{3,}\s*$",
    flags=re.MULTILINE,
)
# Hashtag pattern
HASHTAG_PATTERN = re.compile(r"#([A-Za-z0-9_À-ỹ]+)", flags=re.UNICODE)

# Blacklist of generic/spam hashtags to exclude
GENERIC_HASHTAG_BLACKLIST = {
    "trietlycuocsong",
    "trietlytinhhoa",
    "chinhsachxahoi",
    "tintucmoinhat",
    "vendehomnay",
    "tintuc24h",
    "tintucmoinhathomnay",
    "chinhtrivietnam",
    "phantichchinhtrivietnam",
    "trending",
    "xuhuong",
    "fyp",
    "shorts",
    "reels",
    "viral",
    "tiktok",
    "youtube",
    "video",
}


def _get_proxy_for_gpm_profile(gpm_profile_id: str) -> str | None:
    """Retrieve normalized proxy URL for a GPM profile if configured."""
    clean_profile_id = str(gpm_profile_id or "").strip()
    if not clean_profile_id or clean_profile_id.startswith("local_"):
        return None
    try:
        profile_data = gpm_service.get_gpm_profile_detail(clean_profile_id)
        if not profile_data:
            raise proxy_utils.ProxyConfigurationError(
                f"Không tìm thấy GPM Profile {clean_profile_id}"
            )
        raw_proxy = profile_data.get("raw_proxy") or profile_data.get("proxy") or ""
        proxy_url = proxy_utils.parse_proxy_url(raw_proxy)
        if not proxy_url:
            raise proxy_utils.ProxyConfigurationError(
                f"GPM Profile {clean_profile_id} chưa có proxy hợp lệ"
            )
        return proxy_url
    except proxy_utils.ProxyConfigurationError:
        raise
    except Exception as exc:
        raise proxy_utils.ProxyConfigurationError(
            f"Không đọc được proxy cho GPM Profile {clean_profile_id}: {exc}"
        ) from exc


def sanitize_fb_token(raw_token: str | None) -> str:
    """Sanitize Facebook Access Token by removing quotes, whitespace, and extracting clean token."""
    if not raw_token:
        return ""
    token = str(raw_token).strip().strip("\"' *")
    # If concatenated with multiple tokens or malformed, extract the first valid EAA token
    if "EAAP" in token or "EAAG" in token or "EAA" in token:
        match = re.search(r"(EAA[A-Za-z0-9]+)", token)
        if match:
            extracted = match.group(1)
            # If length is abnormal and multiple EAAP/EAAG are concatenated
            if len(extracted) > 350 and ("EAAP" in extracted[10:] or "EAAG" in extracted[10:] or "EAA" in extracted[10:]):
                second_match = re.search(r"(EAAP|EAAG|EAA)", extracted[10:])
                if second_match:
                    extracted = extracted[: 10 + second_match.start()]
            return extracted.strip()
    return token


def format_facebook_api_error(err_body: str, code: int = 400) -> str:
    """Format Facebook Graph API error into user-friendly Vietnamese explanation."""
    err_body_clean = str(err_body or "").strip()
    if (
        "Session has expired" in err_body_clean
        or "Error validating access token" in err_body_clean
        or '"code":190' in err_body_clean
        or '"code": 190' in err_body_clean
    ):
        return (
            "Token Facebook của Fanpage đã hết hạn hoặc phiên đăng nhập đã kết thúc. "
            "Vui lòng vào tab Cross-Poster -> Bấm [🤖 1-Click Lấy Token Vĩnh Viễn] hoặc dán Token mới rồi bấm [Lưu Cấu Hình]."
        )
    if (
        "No permission to publish" in err_body_clean
        or "pages_manage_posts" in err_body_clean
        or "permission" in err_body_clean.lower()
    ):
        return (
            "Token Fanpage thiếu quyền đăng bài ('pages_manage_posts' hoặc 'pages_read_engagement'). "
            "Vui lòng vào Graph API Explorer -> Thêm quyền 'pages_manage_posts' -> Tạo lại Token."
        )
    return (
        f"Lỗi Meta Graph API ({code}): "
        f"{security_logging.redact_sensitive(err_body_clean)}"
    )


def is_transient_meta_error(error_str_or_exc: Any, code: int = 400) -> bool:
    """Determine if a Meta Graph API error is a transient/temporary cluster glitch or rate limit that can be retried."""
    if code in {500, 502, 503, 504, 429, 408}:
        return True

    err_text = str(error_str_or_exc or "").strip()
    if not err_text:
        return False

    # Try JSON parsing first for exact structured matching
    try:
        data = json.loads(err_text)
        if isinstance(data, dict):
            err_dict = data.get("error", {})
            if isinstance(err_dict, dict):
                if err_dict.get("is_transient") is True:
                    return True
                if err_dict.get("error_subcode") in {1363047, 1363030, 1363019}:
                    return True
                if err_dict.get("code") in {1, 2, 4, 17, 341}:
                    return True
    except Exception:
        pass

    err_lower = err_text.lower()
    if '"is_transient": true' in err_lower or '"is_transient":true' in err_lower:
        return True
    if any(subcode in err_lower for subcode in ("1363047", "1363030", "1363019")):
        return True
    if re.search(r'"code"\s*:\s*(?:1|2|4|17|341)\b', err_lower):
        return True
    if "service temporarily unavailable" in err_lower or "temporarily unavailable" in err_lower:
        return True
    if "please try again" in err_lower or "vui lòng thử lại" in err_lower:
        return True
    if "timed out" in err_lower or "timeout" in err_lower:
        return True

    return False


def _normalized_text(value: str) -> str:
    return re.sub(r"[^\w]+", "", str(value or ""), flags=re.UNICODE).casefold()


def extract_hashtags_from_description(raw_description: str) -> list[str]:
    """Extract hashtags directly from a YouTube video description, filtering out generic spam."""
    if not raw_description:
        return []
    # Only consider text before the disclaimer to avoid picking up footer spam hashtags
    content = DISCLAIMER_PATTERN.sub("", raw_description)
    matches = HASHTAG_PATTERN.findall(content)

    extracted: list[str] = []
    seen: set[str] = set()
    for tag in matches:
        clean_tag = tag.strip().lstrip("#")
        normalized = _normalized_text(clean_tag)
        if not clean_tag or normalized in seen or normalized in GENERIC_HASHTAG_BLACKLIST:
            continue
        seen.add(normalized)
        extracted.append(f"#{clean_tag}")
    return extracted


def get_effective_tags(
    tags: list[str] | str = None,
    raw_description: str = "",
    max_count: int = MAX_CAPTION_HASHTAGS,
    default_tags: list[str] | str = None,
) -> list[str]:
    """Combine default and YouTube tags in priority order, capped at max_count."""
    effective: list[str] = []
    seen: set[str] = set()

    def add_tag(raw_tag: object, *, allow_blacklisted: bool = False) -> bool:
        clean_name = re.sub(
            r"[^\w\sÀ-ỹ]",
            "",
            str(raw_tag or "").strip().lstrip("#").strip(),
        ).strip()
        norm = _normalized_text(clean_name)
        if (
            not norm
            or norm in seen
            or (not allow_blacklisted and norm in GENERIC_HASHTAG_BLACKLIST)
        ):
            return False
        seen.add(norm)
        effective.append(clean_name)
        return len(effective) >= max_count

    if isinstance(default_tags, str):
        try:
            parsed_default_tags = json.loads(default_tags)
            default_tags = (
                parsed_default_tags
                if isinstance(parsed_default_tags, list)
                else re.split(r"[,\n]", default_tags)
            )
        except Exception:
            default_tags = re.split(r"[,\n]", default_tags)
    if isinstance(default_tags, list):
        for raw_tag in default_tags:
            if add_tag(raw_tag, allow_blacklisted=True):
                return effective

    # 2. Hashtags extracted directly from the source description
    desc_tags = extract_hashtags_from_description(raw_description)
    for tag_str in desc_tags:
        if add_tag(tag_str):
            return effective

    # 3. YouTube metadata tags
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = [t.strip() for t in tags.split(",") if t.strip()]

    if isinstance(tags, list):
        for raw_tag in tags:
            if not raw_tag or not isinstance(raw_tag, str):
                continue
            if add_tag(raw_tag):
                break

    return effective


def sanitize_description(raw_description: str, title: str = "") -> str:
    """Remove YouTube-specific links, timestamps, disclaimers, boilerplate, and excessive blank lines."""
    if not raw_description:
        return ""
    text = YT_LINKS_PATTERN.sub("", raw_description)
    text = TIMESTAMPS_PATTERN.sub("", text)
    text = SUBSCRIBE_CTA_PATTERN.sub("", text)
    text = DESCRIPTION_HEADER_PATTERN.sub("", text)
    text = CHANNEL_INTRO_PATTERN.sub("", text)
    text = DISCLAIMER_PATTERN.sub("", text)
    text = SEPARATOR_PATTERN.sub("", text)

    # Remove standalone hashtag lines so they don't duplicate when formatted at the bottom
    text = re.sub(r"^\s*(?:#[A-Za-z0-9_À-ỹ]+\s*)+$", "", text, flags=re.MULTILINE)

    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and title and _normalized_text(lines[0]) == _normalized_text(title):
        lines.pop(0)
    text = "\n".join(lines)

    # Collapse multiple consecutive empty lines into two
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def format_hashtags(
    tags: list[str] | str = None,
    max_count: int = MAX_CAPTION_HASHTAGS,
    raw_description: str = "",
    default_tags: list[str] | str = None,
) -> str:
    """Convert a list of raw tag keywords or description hashtags into at most max_count hashtag strings."""
    effective_tags = get_effective_tags(
        tags,
        raw_description=raw_description,
        max_count=max_count,
        default_tags=default_tags,
    )
    if not effective_tags:
        return ""

    hashtags = []
    for raw_tag in effective_tags:
        words = raw_tag.split()
        if not words:
            continue
        if len(words) == 1 and raw_tag.startswith("#"):
            tag_str = raw_tag[1:]
        elif len(words) == 1 and (raw_tag[0].isupper() or "_" in raw_tag):
            tag_str = raw_tag
        else:
            tag_str = "".join(word.capitalize() for word in words)
        hashtags.append(f"#{tag_str}")

    return " ".join(hashtags)


def build_fb_caption(
    title: str,
    raw_description: str,
    tags: list[str] | str = None,
    template: str | None = None,
    youtube_url: str = "",
    *,
    include_youtube_url: bool = False,
    max_hashtags: int = MAX_CAPTION_HASHTAGS,
    default_tags: list[str] | str = None,
) -> str:
    """Combine sanitized description, at most 5 relevant hashtags, and title according to user template."""
    clean_desc = sanitize_description(raw_description, title=title)
    hashtags_str = format_hashtags(
        tags,
        max_count=max_hashtags,
        raw_description=raw_description,
        default_tags=default_tags,
    )

    if not template or not template.strip():
        template = "{title}\n\n{clean_description}\n\n---\n📌 Like & Follow Fanpage để xem thêm nhiều video hay nhé!\n{hashtags}"

    caption = template.replace("{title}", title or "")
    caption = caption.replace("{clean_description}", clean_desc)
    caption = caption.replace("{hashtags}", hashtags_str)
    caption = caption.replace(
        "{youtube_url}",
        youtube_url if include_youtube_url else "",
    )
    caption = caption.replace("{original_title}", title or "")

    return re.sub(r"\n{3,}", "\n\n", caption).strip()


def append_missing_default_hashtags(
    caption: str,
    default_tags: list[str] | str = None,
) -> str:
    prioritized_defaults = get_effective_tags(
        [],
        max_count=MAX_CAPTION_HASHTAGS,
        default_tags=default_tags,
    )
    if not prioritized_defaults:
        return str(caption or "").strip()

    existing = {
        _normalized_text(tag)
        for tag in HASHTAG_PATTERN.findall(str(caption or ""))
        if _normalized_text(tag)
    }
    missing = [
        tag for tag in prioritized_defaults if _normalized_text(tag) not in existing
    ]
    missing_hashtags = format_hashtags(
        [],
        max_count=MAX_CAPTION_HASHTAGS,
        default_tags=missing,
    )
    if not missing_hashtags:
        return str(caption or "").strip()
    return f"{str(caption or '').strip()}\n\n{missing_hashtags}".strip()


def _caption_is_automatic(
    item: dict[str, Any],
    template: str,
    default_tags: list[str] | str = None,
) -> bool:
    source = str(item.get("fb_description_source") or "").strip().casefold()
    if source == "manual":
        return False
    if source == "auto":
        return True

    current_caption = str(item.get("fb_description") or "").strip()
    if not current_caption:
        return True
    legacy_caption = build_fb_caption(
        item.get("fb_title") or item.get("original_title", ""),
        item.get("original_description", ""),
        item.get("original_tags", []),
        template,
        item.get("youtube_url", ""),
        include_youtube_url=True,
        default_tags=default_tags,
    )
    normalize = lambda value: re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return normalize(current_caption) == normalize(legacy_caption)


def _unique_tag_keywords(
    tags: list[str] | str = None,
    raw_description: str = "",
    default_tags: list[str] | str = None,
) -> list[str]:
    effective = get_effective_tags(
        tags,
        raw_description=raw_description,
        max_count=MAX_META_CONTENT_TAGS,
        default_tags=default_tags,
    )
    if not effective and tags:
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = [part.strip() for part in tags.split(",")]
        if isinstance(tags, list):
            effective = [str(t).strip().lstrip("#").strip() for t in tags if str(t).strip()]

    unique: list[str] = []
    seen: set[str] = set()
    for raw_tag in effective:
        tag = str(raw_tag or "").strip().lstrip("#").strip()
        key = tag.casefold()
        if not tag or key in seen:
            continue
        seen.add(key)
        unique.append(tag)
    return unique


def get_custom_labels(
    tags: list[str] | str = None,
    raw_description: str = "",
    max_count: int = MAX_CUSTOM_LABELS,
    default_tags: list[str] | str = None,
) -> list[str]:
    """Extract clean, human-readable Vietnamese tag strings for Meta custom_labels."""
    candidates = get_effective_tags(
        tags,
        raw_description=raw_description,
        max_count=max_count,
        default_tags=default_tags,
    )
    labels: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        cleaned = str(raw or "").strip().lstrip("#").strip()
        norm = _normalized_text(cleaned)
        if not cleaned or norm in seen or norm in GENERIC_HASHTAG_BLACKLIST:
            continue
        seen.add(norm)
        labels.append(cleaned)
        if len(labels) >= max_count:
            break
    return labels


# Map common domain/niche keywords to verified Meta Graph API Interest IDs
TOPIC_INTEREST_MAP: dict[str, str] = {
    "lịch sử": "6003249578667",           # History
    "history": "6003249578667",
    "quân sự": "6003209794830",           # Military history
    "military": "6003209794830",
    "chiến tranh": "6003209794830",       # Military history / War
    "war": "6003209794830",
    "việt nam": "6003395486343",          # Vietnam
    "vietnam": "6003395486343",
    "campuchia": "6003342233987",         # Cambodia
    "cambodia": "6003342233987",
    "chính trị": "6003186835282",         # Politics and social issues
    "politics": "6003186835282",
    "tin tức": "6003186835282",           # News and entertainment
    "news": "6003186835282",
    "khám phá": "6003332344237",          # Hobbies and activities / Discovery
    "văn hóa": "6003225930699",           # Culture
    "tâm lý": "6003249578667",
}


def resolve_content_tag_ids(
    tags: list[str] | str = None,
    access_token: str = "",
    target_gpm_profile_id: str = "",
    raw_description: str = "",
    default_tags: list[str] | str = None,
) -> tuple[list[str], list[str]]:
    """Resolve YouTube tag text and domain keywords to Meta interest IDs without blocking publication."""
    candidates = _unique_tag_keywords(
        tags,
        raw_description=raw_description,
        default_tags=default_tags,
    )
    clean_token = sanitize_fb_token(access_token)
    if not candidates:
        return [], []

    resolved_ids: list[str] = []
    seen_ids: set[str] = set()
    skipped: list[str] = []

    # 1. Map against verified interest catalog
    for tag in candidates:
        norm = _normalized_text(tag)
        matched_id = None
        for key, int_id in TOPIC_INTEREST_MAP.items():
            if _normalized_text(key) in norm or norm in _normalized_text(key):
                matched_id = int_id
                break
        if matched_id:
            if matched_id not in seen_ids:
                seen_ids.add(matched_id)
                resolved_ids.append(matched_id)
        else:
            skipped.append(tag)

    # 2. Try validating any remaining exact English/catalog candidates via API if token is present
    if clean_token and skipped:
        query = urllib.parse.urlencode({
            "type": "adinterestvalid",
            "interest_list": json.dumps(skipped[:10], ensure_ascii=False),
        })
        request = urllib.request.Request(
            f"{GRAPH_API_BASE}/search?{query}",
            headers={
                "User-Agent": "NexusStudio/1.0",
                "Authorization": f"Bearer {clean_token}",
            },
        )
        proxy_url = _get_proxy_for_gpm_profile(target_gpm_profile_id)
        opener = _build_urllib_opener(proxy_url)
        try:
            with opener.open(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            api_valid_names = set()
            for item in payload.get("data") or []:
                if isinstance(item, dict) and item.get("valid"):
                    tag_id = str(item.get("id") or "").strip()
                    if tag_id and tag_id not in seen_ids:
                        seen_ids.add(tag_id)
                        resolved_ids.append(tag_id)
                    name = str(item.get("name") or "")
                    if name:
                        api_valid_names.add(_normalized_text(name))
            if api_valid_names:
                skipped = [t for t in skipped if _normalized_text(t) not in api_valid_names]
        except Exception as exc:
            logger.debug("Adinterest validation skipped: %s", exc)

    return resolved_ids, skipped


def _build_multipart_body(
    fields: dict[str, Any],
    files: list[tuple[str, Path, str]],
) -> tuple[bytes, str]:
    boundary = f"----NexusStudioBoundary{time.time_ns()}"
    body = bytearray()
    for name, value in fields.items():
        if value is None:
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        body.extend(f"{value}\r\n".encode("utf-8"))
    for name, file_path, content_type in files:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            f'Content-Disposition: form-data; name="{name}"; filename="{file_path.name}"\r\n'.encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        with open(file_path, "rb") as file_handle:
            body.extend(file_handle.read())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), boundary


def sync_channel_public_videos(
    channel_url: str,
    gpm_profile_id: str = "",
    sort_order_mode: str = "oldest_first",
    max_videos: int = 500,
    target_page_id: str = "",
    sys_job_id: str | None = None,
) -> dict[str, Any]:
    """Scrape public YouTube videos from a channel using yt-dlp and store in queue.
    
    Uses GPM proxy if configured to prevent YouTube bot rate-limiting.
    """
    clean_url = channel_url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        if clean_url.startswith("UC") or clean_url.startswith("HC"):
            clean_url = f"https://www.youtube.com/channel/{clean_url}"
        elif clean_url.startswith("@"):
            clean_url = f"https://www.youtube.com/{clean_url}"
        else:
            clean_url = f"https://www.youtube.com/@{clean_url}"

    if not clean_url.endswith("/videos") and "/playlist" not in clean_url and "/watch" not in clean_url:
        clean_url = f"{clean_url.rstrip('/')}/videos"

    sync_key = f"{target_page_id}_{clean_url}"
    with _sync_lock:
        if sync_key in _active_sync_targets:
            logger.info("Channel sync already running for %s, skipping concurrent request.", sync_key)
            return {"inserted": 0, "existing": 0, "total_found": 0, "skipped": True}
        _active_sync_targets.add(sync_key)

    proxy_url = _get_proxy_for_gpm_profile(gpm_profile_id)
    logger.info("Scanning YouTube channel: %s (Proxy: %s, Target Page: %s)", clean_url, proxy_url or "Direct", target_page_id or "Default")

    channel_display = clean_url.replace("https://www.youtube.com/", "").replace("http://www.youtube.com/", "")
    if not sys_job_id:
        sys_job_id = f"fb-sync-{int(time.time() * 1000)}"
        try:
            db.create_system_job(
                job_id=sys_job_id,
                job_type="fb_crosspost_sync",
                title=f"Đồng bộ Kênh YouTube -> FB ({channel_display})",
                payload={"channel_url": clean_url, "target_page_id": target_page_id, "gpm_profile_id": gpm_profile_id},
            )
        except Exception as sys_exc:
            logger.warning("Could not register fb_crosspost_sync system_job: %s", sys_exc)

    try:
        db.update_system_job(sys_job_id, status="running", progress="Đang quét video từ YouTube qua GPM Proxy...")
    except Exception:
        pass

    try:
        ydl_opts: dict[str, Any] = {
            "extract_flat": "in_playlist",
            "dump_single_json": True,
            "playlistend": max_videos,
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            "http_headers": {
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            },
            "extractor_args": {
                "youtubetab": {"lang": ["vi"]},
                "youtube": {"lang": ["vi"]},
            },
        }
        if proxy_url:
            ydl_opts["proxy"] = proxy_url

        try:
            with YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(clean_url, download=False)
        except Exception as exc:
            logger.error("Failed to extract YouTube channel info: %s", exc)
            try:
                db.update_system_job(sys_job_id, status="failed", progress=f"Lỗi quét kênh: {str(exc)[:100]}")
            except Exception:
                pass
            raise RuntimeError(f"Không thể quét kênh YouTube: {exc}") from exc

        if not result:
            try:
                db.update_system_job(sys_job_id, status="completed", progress="Quét xong: không tìm thấy video nào.")
            except Exception:
                pass
            return {"inserted": 0, "existing": 0, "total_found": 0}

        entries = result.get("entries") or []
        valid_entries = [e for e in entries if e and e.get("id")]

        # Sort entries
        def get_upload_date_key(entry: dict) -> str:
            # Format: 'YYYYMMDD'
            d = entry.get("upload_date") or entry.get("release_date") or ""
            if not d and entry.get("timestamp"):
                try:
                    d = datetime.datetime.fromtimestamp(entry["timestamp"]).strftime("%Y%m%d")
                except Exception:
                    d = ""
            return str(d)

        has_dates = any(bool(get_upload_date_key(e)) for e in valid_entries)
        if has_dates:
            if sort_order_mode == "newest_first":
                valid_entries.sort(key=get_upload_date_key, reverse=True)
            else:
                valid_entries.sort(key=get_upload_date_key, reverse=False)
        else:
            # YouTube /videos tab naturally returns newest first ([0] = newest, [-1] = oldest)
            if sort_order_mode == "oldest_first":
                valid_entries.reverse()  # Oldest is now at index 0
            else:
                pass  # Already newest first

        settings = db.get_fb_crossposter_settings(target_page_id)
        post_template = settings.get("post_template", "")
        default_tags = settings.get("default_tags", [])

        items_to_upsert = []
        for idx, entry in enumerate(valid_entries):
            yt_id = entry.get("id")
            title = entry.get("title") or f"Video {yt_id}"
            description = entry.get("description") or ""
            tags = entry.get("tags") or []
            upload_date = get_upload_date_key(entry)
            yt_url = f"https://www.youtube.com/watch?v={yt_id}"

            thumbnail_url = entry.get("thumbnail") or f"https://i.ytimg.com/vi/{yt_id}/maxresdefault.jpg"
            fb_caption = build_fb_caption(
                title,
                description,
                tags,
                post_template,
                yt_url,
                default_tags=default_tags,
            )

            items_to_upsert.append({
                "youtube_id": yt_id,
                "youtube_url": yt_url,
                "original_title": title,
                "original_description": description,
                "original_tags": tags,
                "thumbnail_url": thumbnail_url,
                "youtube_upload_date": upload_date,
                "fb_title": title,
                "fb_description": fb_caption,
                "fb_description_source": "auto",
                "sort_order": idx + 1,
                "target_page_id": target_page_id,
            })

        upsert_res = db.upsert_fb_crossposter_queue_items(items_to_upsert, target_page_id=target_page_id)

        # Automatically recalculate schedule for pending items if daily quota is set
        daily_quota = settings.get("daily_quota", 2)
        schedule_times = settings.get("schedule_times", ["11:30", "19:30"])
        db.recalculate_fb_queue_schedule(daily_quota, schedule_times, target_page_id=target_page_id)

        # Save last synced timestamp
        db.save_fb_crossposter_settings({
            **settings,
            "last_synced_at": datetime.datetime.now().isoformat(),
        }, page_id=target_page_id)

        try:
            db.update_system_job(
                sys_job_id,
                status="completed",
                progress=f"Đã quét xong: tìm thấy {len(valid_entries)} video, thêm {upsert_res['inserted']} video mới",
            )
        except Exception:
            pass

        return {
            "inserted": upsert_res["inserted"],
            "existing": upsert_res["existing"],
            "total_found": len(valid_entries),
        }
    finally:
        with _sync_lock:
            _active_sync_targets.discard(sync_key)


def _build_urllib_opener(proxy_url: str | None = None) -> urllib.request.OpenerDirector:
    """Build an urllib opener with optional proxy support."""
    handlers: list[Any] = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({
            "http": proxy_url,
            "https": proxy_url,
        }))
    return urllib.request.build_opener(*handlers)


def test_fb_connection(page_id: str, access_token: str, gpm_profile_id: str = "") -> dict[str, Any]:
    """Verify Facebook Page Access Token and retrieve Page information."""
    clean_page_id = str(page_id or "").strip()
    clean_token = sanitize_fb_token(access_token)
    if not clean_page_id or not clean_token:
        raise ValueError("Page ID và Access Token không được để trống")

    proxy_url = _get_proxy_for_gpm_profile(gpm_profile_id)
    url = f"{GRAPH_API_BASE}/{clean_page_id}?fields=id,name,category,link,picture"

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NexusStudio/1.0",
            "Authorization": f"Bearer {clean_token}",
        },
    )
    opener = _build_urllib_opener(proxy_url)

    try:
        with opener.open(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            returned_page_id = str(data.get("id") or "").strip()
            if returned_page_id != clean_page_id:
                raise RuntimeError("Page Access Token không thuộc Fanpage đã chọn")
            return {
                "success": True,
                "page_id": data.get("id"),
                "page_name": data.get("name"),
                "category": data.get("category"),
                "link": data.get("link"),
                "picture": data.get("picture", {}).get("data", {}).get("url"),
                "proxy_used": proxy_utils.proxy_display_value(proxy_url) or "Direct Local IP",
            }
    except urllib.error.HTTPError as err:
        err_msg = err.read().decode("utf-8", errors="ignore")
        safe_error = format_facebook_api_error(err_msg, err.code)
        logger.error("Facebook API Test Error %s: %s", err.code, safe_error)
        raise RuntimeError(safe_error) from err
    except Exception as exc:
        safe_error = security_logging.redact_sensitive(exc)
        logger.error("Facebook API Connection Failed: %s", safe_error)
        raise RuntimeError(f"Không thể kết nối Facebook: {safe_error}") from exc


def upload_large_video_resumable(
    page_id: str,
    access_token: str,
    video_path: Path,
    title: str,
    description: str,
    scheduled_publish_time: int | None = None,
    thumb_path: Path | None = None,
    content_tag_ids: list[str] | None = None,
    custom_labels: list[str] | None = None,
    target_gpm_profile_id: str = "",
    chunk_size_bytes: int = 25 * 1024 * 1024,  # 25 MB chunks
) -> dict[str, Any]:
    """Upload large video (>1 hour / 1.5GB-3.5GB) to Facebook using Meta Graph API Resumable Upload protocol.
    
    Phases:
    1. Start: Initialize session, get upload_session_id.
    2. Transfer: Loop sending 25MB chunks with auto-retry.
    3. Finish: Finalize upload with metadata and schedule timestamp.
    """
    clean_page_id = str(page_id or "").strip()
    clean_token = sanitize_fb_token(access_token)
    if not clean_page_id or not clean_token:
        raise ValueError("Page ID hoặc Access Token không hợp lệ")

    if not video_path.is_file():
        raise FileNotFoundError(f"File video không tồn tại: {video_path}")

    file_size = video_path.stat().st_size
    proxy_url = _get_proxy_for_gpm_profile(target_gpm_profile_id)
    opener = _build_urllib_opener(proxy_url)

    logger.info(
        "Starting Resumable Facebook Upload for Page %s (File: %s, Size: %.2f MB, Proxy: %s)",
        clean_page_id,
        video_path.name,
        file_size / (1024 * 1024),
        proxy_utils.proxy_display_value(proxy_url) or "Direct Local IP",
    )

    url = f"{GRAPH_VIDEO_API_BASE}/{clean_page_id}/videos"

    # --- Phase 1: Start ---
    start_payload = urllib.parse.urlencode({
        "upload_phase": "start",
        "file_size": str(file_size),
        "access_token": clean_token,
    }).encode("utf-8")

    start_req = urllib.request.Request(
        url,
        data=start_payload,
        headers={"User-Agent": "NexusStudio/1.0", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with opener.open(start_req, timeout=30) as resp:
            start_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        err_msg = err.read().decode("utf-8", errors="ignore")
        formatted = format_facebook_api_error(err_msg, err.code)
        logger.error("Resumable Start Phase HTTP Error %s: %s", err.code, formatted)
        raise RuntimeError(f"Lỗi khởi tạo upload video: {formatted}") from err
    except Exception as exc:
        logger.error("Resumable Start Phase Failed: %s", exc)
        raise RuntimeError(f"Không thể khởi tạo phiên upload video: {exc}") from exc

    session_id = start_data.get("upload_session_id")
    video_id = start_data.get("video_id")
    if not session_id:
        raise RuntimeError(f"Meta Graph API không trả về upload_session_id: {start_data}")

    start_offset = int(start_data.get("start_offset", 0))
    end_offset = int(start_data.get("end_offset", min(file_size, start_offset + chunk_size_bytes)))

    # --- Phase 2: Transfer Chunks ---
    with open(video_path, "rb") as vf:
        while start_offset < file_size:
            chunk_length = min(end_offset - start_offset, file_size - start_offset)
            if chunk_length <= 0:
                chunk_length = min(chunk_size_bytes, file_size - start_offset)

            vf.seek(start_offset)
            chunk_data = vf.read(chunk_length)

            boundary = f"----ResumableChunkBoundary{int(time.time() * 1000)}"
            body = bytearray()

            def add_chunk_field(name: str, value: str):
                body.extend(f"--{boundary}\r\n".encode("utf-8"))
                body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
                body.extend(f"{value}\r\n".encode("utf-8"))

            add_chunk_field("access_token", clean_token)
            add_chunk_field("upload_phase", "transfer")
            add_chunk_field("upload_session_id", session_id)
            add_chunk_field("start_offset", str(start_offset))

            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                b'Content-Disposition: form-data; name="video_file_chunk"; filename="chunk.bin"\r\n'
            )
            body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
            body.extend(chunk_data)
            body.extend(b"\r\n")
            body.extend(f"--{boundary}--\r\n".encode("utf-8"))

            chunk_req = urllib.request.Request(
                url,
                data=bytes(body),
                headers={
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": "NexusStudio/1.0",
                },
                method="POST",
            )

            # Retry loop for each chunk (up to 3 attempts with backoff)
            max_retries = 3
            transfer_success = False
            last_transfer_error = None

            for attempt in range(1, max_retries + 1):
                try:
                    with opener.open(chunk_req, timeout=180) as resp:
                        chunk_res = json.loads(resp.read().decode("utf-8"))
                        transfer_success = True
                        start_offset = int(chunk_res.get("start_offset", start_offset + chunk_length))
                        end_offset = int(chunk_res.get("end_offset", min(file_size, start_offset + chunk_size_bytes)))
                        logger.info(
                            "Chunk transferred: offset %d / %d (%.1f%%)",
                            start_offset,
                            file_size,
                            (start_offset / file_size) * 100.0,
                        )
                        break
                except Exception as c_err:
                    last_transfer_error = c_err
                    logger.warning(
                        "Chunk transfer attempt %d failed (offset %d): %s. Retrying in %ds...",
                        attempt,
                        start_offset,
                        c_err,
                        attempt * 3,
                    )
                    time.sleep(attempt * 3)

            if not transfer_success:
                raise RuntimeError(
                    f"Truyền dữ liệu video thất bại tại offset {start_offset}/{file_size} sau {max_retries} lần thử: {last_transfer_error}"
                )

    # --- Phase 3: Finish (with clean urlencoded form and auto-retry for transient cluster errors) ---
    finish_params: dict[str, str] = {
        "access_token": clean_token,
        "upload_phase": "finish",
        "upload_session_id": session_id,
        "title": title[:255] if title else "",
        "description": description or "",
    }
    if content_tag_ids:
        finish_params["content_tags"] = json.dumps(content_tag_ids)
    if custom_labels:
        finish_params["custom_labels"] = json.dumps(custom_labels, ensure_ascii=False)

    now_ts = int(time.time())
    if scheduled_publish_time and scheduled_publish_time > now_ts + 600:
        finish_params["published"] = "false"
        finish_params["scheduled_publish_time"] = str(scheduled_publish_time)
    else:
        finish_params["published"] = "true"

    # Initial grace period: Allow Meta's backend cluster time to assemble and verify chunks
    logger.info(
        "All chunks transferred for %s (%.2f MB). Waiting 10s grace period before finalizing post...",
        video_path.name,
        file_size / (1024 * 1024),
    )
    time.sleep(10)

    max_finish_retries = 10
    finish_delays = [10, 15, 20, 30, 45, 60, 60, 90, 90, 120]
    last_finish_error = None
    finish_data: dict[str, Any] | None = None

    finish_body = urllib.parse.urlencode(finish_params).encode("utf-8")
    finish_req = urllib.request.Request(
        url,
        data=finish_body,
        headers={
            "User-Agent": "NexusStudio/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    for attempt in range(1, max_finish_retries + 1):
        try:
            with opener.open(finish_req, timeout=60) as resp:
                finish_data = json.loads(resp.read().decode("utf-8"))
                fb_id = finish_data.get("id") or video_id or session_id
                finish_data["id"] = fb_id
                logger.info(
                    "Resumable Facebook Upload Completed Successfully! FB ID: %s (attempt %d/%d)",
                    fb_id,
                    attempt,
                    max_finish_retries,
                )
                break
        except urllib.error.HTTPError as err:
            err_msg = err.read().decode("utf-8", errors="ignore")
            formatted = format_facebook_api_error(err_msg, err.code)
            last_finish_error = RuntimeError(f"Lỗi hoàn tất upload video: {formatted}")
            is_transient = is_transient_meta_error(err_msg, err.code)

            # Check if Meta backend actually processed the video despite transient response
            check_id = video_id or session_id
            if check_id:
                try:
                    check_url = f"{GRAPH_API_BASE}/{check_id}?fields=id,status&access_token={clean_token}"
                    check_req = urllib.request.Request(
                        check_url,
                        headers={"User-Agent": "NexusStudio/1.0"},
                    )
                    with opener.open(check_req, timeout=15) as c_resp:
                        c_data = json.loads(c_resp.read().decode("utf-8"))
                        if c_data.get("id"):
                            logger.info(
                                "Video %s verified existing on Meta cluster despite transient finish response!",
                                c_data["id"],
                            )
                            finish_data = {"id": c_data["id"], "success": True}
                            break
                except Exception:
                    pass

            if attempt < max_finish_retries and is_transient:
                delay = finish_delays[min(attempt - 1, len(finish_delays) - 1)]
                logger.warning(
                    "Resumable Finish Phase attempt %d/%d failed with transient Meta error: %s. Retrying in %ds (waiting for Meta cluster to assemble video)...",
                    attempt,
                    max_finish_retries,
                    formatted,
                    delay,
                )
                time.sleep(delay)
                continue
            logger.error("Resumable Finish Phase HTTP Error %s: %s", err.code, formatted)
            raise last_finish_error from err
        except Exception as exc:
            last_finish_error = exc
            if attempt < max_finish_retries:
                delay = finish_delays[min(attempt - 1, len(finish_delays) - 1)]
                logger.warning(
                    "Resumable Finish Phase attempt %d/%d failed: %s. Retrying in %ds...",
                    attempt,
                    max_finish_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
                continue
            logger.error("Resumable Finish Phase Failed: %s", exc)
            raise RuntimeError(f"Không thể hoàn tất upload video: {exc}") from exc

    if not finish_data:
        if last_finish_error:
            raise last_finish_error
        raise RuntimeError("Không thể hoàn tất phiên upload video lên Facebook")

    fb_id = str(finish_data.get("id") or "").strip()

    # Upload preferred thumbnail separately if provided
    if thumb_path and thumb_path.is_file() and fb_id:
        try:
            logger.info("Uploading preferred thumbnail for resumable video %s...", fb_id)
            thumb_body, thumb_boundary = _build_multipart_body(
                {"access_token": clean_token, "is_preferred": "true"},
                [("source", thumb_path, "image/jpeg")],
            )
            thumb_req = urllib.request.Request(
                f"{GRAPH_API_BASE}/{fb_id}/thumbnails",
                data=thumb_body,
                headers={
                    "User-Agent": "NexusStudio/1.0",
                    "Content-Type": f"multipart/form-data; boundary={thumb_boundary}",
                },
                method="POST",
            )
            with opener.open(thumb_req, timeout=60) as t_resp:
                t_data = json.loads(t_resp.read().decode("utf-8"))
                logger.info("Preferred thumbnail attached to video %s successfully: %s", fb_id, t_data)
        except Exception as t_err:
            logger.warning("Could not attach thumbnail to video %s (video upload succeeded): %s", fb_id, t_err)

    return finish_data


def upload_video_to_facebook(
    page_id: str,
    access_token: str,
    video_path: Path,
    title: str,
    description: str,
    scheduled_publish_time: int | None = None,
    thumb_path: Path | None = None,
    content_tag_ids: list[str] | None = None,
    custom_labels: list[str] | None = None,
    target_gpm_profile_id: str = "",
) -> dict[str, Any]:
    """Upload video to Facebook Page via Meta Graph API.
    
    Automatically routes files > 25MB through Resumable Chunked Upload to handle
    large videos (>1 hour / 1.5GB-3.5GB) without memory exhaustion or socket timeouts.
    """
    clean_page_id = str(page_id or "").strip()
    clean_token = sanitize_fb_token(access_token)
    if not clean_page_id or not clean_token:
        raise ValueError("Page ID hoặc Access Token không hợp lệ")

    if not video_path.is_file():
        raise FileNotFoundError(f"File video không tồn tại: {video_path}")

    file_size = video_path.stat().st_size
    # Use resumable chunked upload for videos larger than 25MB
    if file_size > 25 * 1024 * 1024:
        return upload_large_video_resumable(
            page_id=clean_page_id,
            access_token=clean_token,
            video_path=video_path,
            title=title,
            description=description,
            scheduled_publish_time=scheduled_publish_time,
            thumb_path=thumb_path,
            content_tag_ids=content_tag_ids,
            custom_labels=custom_labels,
            target_gpm_profile_id=target_gpm_profile_id,
        )

    proxy_url = _get_proxy_for_gpm_profile(target_gpm_profile_id)
    logger.info(
        "Uploading small video to Facebook Page %s (File: %s, Size: %d MB, Proxy: %s)",
        clean_page_id,
        video_path.name,
        file_size // (1024 * 1024),
        proxy_utils.proxy_display_value(proxy_url) or "Direct Local IP",
    )

    url = f"{GRAPH_VIDEO_API_BASE}/{clean_page_id}/videos"
    fields: dict[str, str] = {
        "access_token": clean_token,
        "title": title[:255] if title else "",
        "description": description or "",
    }
    if content_tag_ids:
        fields["content_tags"] = json.dumps(content_tag_ids)
    if custom_labels:
        fields["custom_labels"] = json.dumps(custom_labels, ensure_ascii=False)

    now_ts = int(time.time())
    if scheduled_publish_time and scheduled_publish_time > now_ts + 600:
        fields["published"] = "false"
        fields["scheduled_publish_time"] = str(scheduled_publish_time)
    else:
        fields["published"] = "true"

    files: list[tuple[str, Path, str]] = []
    if thumb_path and thumb_path.is_file():
        files.append(("thumb", thumb_path, "image/jpeg"))
    files.append(("source", video_path, "video/mp4"))
    body, boundary = _build_multipart_body(fields, files)

    opener = _build_urllib_opener(proxy_url)

    max_small_retries = 5
    small_delays = [10, 20, 30, 45, 60]
    last_small_error = None

    for attempt in range(1, max_small_retries + 1):
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "NexusStudio/1.0",
            },
            method="POST",
        )

        try:
            with opener.open(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="ignore")
            formatted = format_facebook_api_error(err_body, err.code)
            last_small_error = RuntimeError(f"Lỗi tải video lên Facebook: {formatted}")
            is_transient = is_transient_meta_error(err_body, err.code)
            if attempt < max_small_retries and is_transient:
                delay = small_delays[min(attempt - 1, len(small_delays) - 1)]
                logger.warning(
                    "Facebook Small Video Upload attempt %d/%d failed with transient error: %s. Retrying in %ds...",
                    attempt,
                    max_small_retries,
                    formatted,
                    delay,
                )
                time.sleep(delay)
                continue
            logger.error("Facebook Video Upload HTTP Error %s: %s", err.code, formatted)
            raise last_small_error from err
        except Exception as exc:
            last_small_error = exc
            if attempt < max_small_retries:
                delay = small_delays[min(attempt - 1, len(small_delays) - 1)]
                logger.warning(
                    "Facebook Video Upload attempt %d/%d failed: %s. Retrying in %ds...",
                    attempt,
                    max_small_retries,
                    exc,
                    delay,
                )
                time.sleep(delay)
                continue
            logger.error("Facebook Video Upload Failed: %s", exc)
            raise RuntimeError(f"Không thể upload video lên Facebook: {exc}") from exc

    if last_small_error:
        raise last_small_error


def convert_video_to_vertical(source_path: Path, output_path: Path) -> Path:
    """Render a 1080x1920 H.264 copy with the full source centered over a blurred fill."""
    if not source_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy video nguồn để chuyển 9:16: {source_path.name}")

    ffmpeg_directory = Path(ensure_ffmpeg_directory())
    ffmpeg_executable = ffmpeg_directory / "ffmpeg.exe"
    if not ffmpeg_executable.is_file():
        raise RuntimeError("Không tìm thấy FFmpeg để chuyển video sang 9:16")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    filter_graph = (
        "[0:v]split=2[background][foreground];"
        f"[background]scale={VERTICAL_VIDEO_WIDTH}:{VERTICAL_VIDEO_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={VERTICAL_VIDEO_WIDTH}:{VERTICAL_VIDEO_HEIGHT},"
        "boxblur=30:2[blurred];"
        f"[foreground]scale={VERTICAL_VIDEO_WIDTH}:{VERTICAL_VIDEO_HEIGHT}:"
        "force_original_aspect_ratio=decrease[content];"
        "[blurred][content]overlay=(W-w)/2:(H-h)/2,"
        "setsar=1,format=yuv420p[vertical]"
    )
    command = [
        str(ffmpeg_executable),
        "-y",
        "-i",
        str(source_path),
        "-filter_complex",
        filter_graph,
        "-map",
        "[vertical]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
        output_path.unlink(missing_ok=True)
        error_detail = str(result.stderr or "FFmpeg không tạo được file đầu ra").strip()
        if len(error_detail) > 1200:
            error_detail = error_detail[-1200:]
        raise RuntimeError(f"Không thể chuyển video sang 9:16: {error_detail}")
    return output_path


def convert_thumbnail_to_vertical(
    source_path: Path,
    output_path: Path | None = None,
) -> Path:
    """Create a 1080x1920 JPEG with centered source artwork over a blurred fill."""
    target_path = output_path or source_path
    if not source_path.is_file():
        raise FileNotFoundError(
            f"Không tìm thấy thumbnail nguồn để chuyển 9:16: {source_path.name}"
        )

    try:
        with Image.open(source_path) as source_image:
            source_image.load()
            image = source_image.convert("RGB")

        target_size = (VERTICAL_VIDEO_WIDTH, VERTICAL_VIDEO_HEIGHT)
        background = ImageOps.fit(
            image,
            target_size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        ).filter(ImageFilter.GaussianBlur(radius=32))
        foreground = ImageOps.contain(
            image,
            target_size,
            method=Image.Resampling.LANCZOS,
        )
        paste_position = (
            (VERTICAL_VIDEO_WIDTH - foreground.width) // 2,
            (VERTICAL_VIDEO_HEIGHT - foreground.height) // 2,
        )
        background.paste(foreground, paste_position)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        for quality in (92, 85, 75, 65):
            background.save(target_path, format="JPEG", quality=quality, optimize=True)
            if target_path.stat().st_size <= META_THUMBNAIL_MAX_BYTES:
                return target_path
        target_path.unlink(missing_ok=True)
        raise ValueError("Thumbnail 9:16 vẫn vượt giới hạn 10 MB của Meta")
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        if target_path != source_path:
            target_path.unlink(missing_ok=True)
        raise RuntimeError(f"Không thể chuyển thumbnail sang 9:16: {exc}") from exc


def download_thumbnail(
    thumbnail_url: str,
    output_path: Path,
    source_gpm_profile_id: str = "",
) -> bool:
    """Download and normalize a thumbnail through the source channel proxy with fallback support."""
    if not thumbnail_url:
        return False

    candidate_urls = [thumbnail_url]
    if "maxresdefault.jpg" in thumbnail_url:
        candidate_urls.append(thumbnail_url.replace("maxresdefault.jpg", "hqdefault.jpg"))
    elif "hqdefault.jpg" in thumbnail_url:
        candidate_urls.insert(0, thumbnail_url.replace("hqdefault.jpg", "maxresdefault.jpg"))

    proxy_url = _get_proxy_for_gpm_profile(source_gpm_profile_id)
    opener = _build_urllib_opener(proxy_url)

    for url in candidate_urls:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with opener.open(req, timeout=30) as resp:
                raw_image = resp.read(META_THUMBNAIL_MAX_BYTES * 4 + 1)
            if not raw_image:
                continue

            with Image.open(io.BytesIO(raw_image)) as source_image:
                source_image.load()
                image = source_image.convert("RGB")

            output_path.parent.mkdir(parents=True, exist_ok=True)
            for quality in (92, 85, 75, 65):
                image.save(output_path, format="JPEG", quality=quality, optimize=True)
                if output_path.stat().st_size <= META_THUMBNAIL_MAX_BYTES:
                    return True

            raise ValueError("Thumbnail exceeds Meta's 10 MB limit after normalization")
        except urllib.error.HTTPError as http_err:
            if http_err.code == 404:
                continue  # Try fallback candidate
            logger.warning("HTTP error downloading thumbnail %s: %s", url, http_err)
        except (OSError, ValueError, UnidentifiedImageError, urllib.error.URLError) as exc:
            logger.warning("Could not download thumbnail %s: %s", url, exc)

    output_path.unlink(missing_ok=True)
    return False


def _facebook_http_error(prefix: str, error: urllib.error.HTTPError) -> RuntimeError:
    body = error.read().decode("utf-8", errors="ignore")
    formatted = format_facebook_api_error(body, error.code)
    return RuntimeError(f"{prefix}: {formatted}")


def get_facebook_video_metadata(
    video_id: str,
    access_token: str,
    target_gpm_profile_id: str = "",
) -> dict[str, Any]:
    clean_video_id = str(video_id or "").strip()
    clean_token = sanitize_fb_token(access_token)
    if not clean_video_id or not clean_token:
        raise ValueError("Video ID hoặc Access Token Facebook không hợp lệ")

    query = urllib.parse.urlencode({
        "fields": (
            "id,title,description,content_tags,custom_labels,scheduled_publish_time,published,status,"
            "thumbnails.limit(100){id,is_preferred,uri}"
        ),
    })
    request = urllib.request.Request(
        f"{GRAPH_API_BASE}/{clean_video_id}?{query}",
        headers={
            "User-Agent": "NexusStudio/1.0",
            "Authorization": f"Bearer {clean_token}",
        },
    )
    proxy_url = _get_proxy_for_gpm_profile(target_gpm_profile_id)
    opener = _build_urllib_opener(proxy_url)
    try:
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise _facebook_http_error("Không đọc được metadata video Facebook", error) from error


def _content_tag_id_set(value: Any) -> set[str]:
    if isinstance(value, dict):
        value = value.get("data") or []
    if not isinstance(value, list):
        return set()
    tag_ids: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            tag_id = item.get("id")
        else:
            tag_id = item
        if tag_id is not None and str(tag_id).strip():
            tag_ids.add(str(tag_id).strip())
    return tag_ids


def update_facebook_video_metadata(
    video_id: str,
    access_token: str,
    title: str,
    description: str,
    content_tag_ids: list[str] | None = None,
    custom_labels: list[str] | None = None,
    thumb_path: Path | None = None,
    target_gpm_profile_id: str = "",
    scheduled_publish_time: int | None = None,
) -> dict[str, Any]:
    """Update an existing Page video, upload a preferred thumbnail, optionally update schedule, and verify the result."""
    clean_video_id = str(video_id or "").strip()
    clean_token = sanitize_fb_token(access_token)
    if not clean_video_id or not clean_token:
        raise ValueError("Video ID hoặc Access Token Facebook không hợp lệ")

    proxy_url = _get_proxy_for_gpm_profile(target_gpm_profile_id)
    opener = _build_urllib_opener(proxy_url)
    before = get_facebook_video_metadata(
        clean_video_id,
        clean_token,
        target_gpm_profile_id=target_gpm_profile_id,
    )

    update_fields: dict[str, str] = {
        "access_token": clean_token,
        "title": str(title or "")[:255],
        "description": str(description or ""),
    }
    if scheduled_publish_time and scheduled_publish_time > int(time.time()) + 600:
        update_fields["scheduled_publish_time"] = str(scheduled_publish_time)
    if content_tag_ids:
        update_fields["content_tags"] = json.dumps(content_tag_ids)
    if custom_labels:
        update_fields["custom_labels"] = json.dumps(custom_labels, ensure_ascii=False)
    update_request = urllib.request.Request(
        f"{GRAPH_API_BASE}/{clean_video_id}",
        data=urllib.parse.urlencode(update_fields).encode("utf-8"),
        headers={
            "User-Agent": "NexusStudio/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with opener.open(update_request, timeout=60) as response:
            update_result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise _facebook_http_error("Không cập nhật được metadata video Facebook", error) from error

    thumbnail_result: dict[str, Any] = {}
    if thumb_path and thumb_path.is_file():
        thumbnail_body, thumbnail_boundary = _build_multipart_body(
            {"access_token": clean_token, "is_preferred": "true"},
            [("source", thumb_path, "image/jpeg")],
        )
        thumbnail_request = urllib.request.Request(
            f"{GRAPH_API_BASE}/{clean_video_id}/thumbnails",
            data=thumbnail_body,
            headers={
                "User-Agent": "NexusStudio/1.0",
                "Content-Type": f"multipart/form-data; boundary={thumbnail_boundary}",
            },
            method="POST",
        )
        try:
            with opener.open(thumbnail_request, timeout=60) as response:
                thumbnail_result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise _facebook_http_error("Không cập nhật được thumbnail Facebook", error) from error

    expected_tags = {str(tag_id) for tag_id in content_tag_ids or []}
    before_preferred = {
        str(item.get("id"))
        for item in ((before.get("thumbnails") or {}).get("data") or [])
        if isinstance(item, dict) and item.get("is_preferred") and item.get("id")
    }
    verified: dict[str, Any] = {}
    for attempt in range(5):
        verified = get_facebook_video_metadata(
            clean_video_id,
            clean_token,
            target_gpm_profile_id=target_gpm_profile_id,
        )
        description_ok = str(verified.get("description") or "") == str(description or "")
        tags_ok = not expected_tags or expected_tags.issubset(
            _content_tag_id_set(verified.get("content_tags"))
        )
        preferred_after = {
            str(item.get("id"))
            for item in ((verified.get("thumbnails") or {}).get("data") or [])
            if isinstance(item, dict) and item.get("is_preferred") and item.get("id")
        }
        thumbnail_ok = not thumb_path or bool(preferred_after - before_preferred)
        if description_ok and tags_ok and thumbnail_ok:
            return {
                "success": True,
                "video_id": clean_video_id,
                "update": update_result,
                "thumbnail": thumbnail_result,
                "verified": verified,
            }
        if attempt < 4:
            time.sleep(2)

    raise RuntimeError(
        "Meta đã nhận yêu cầu nhưng metadata hoặc thumbnail chưa khớp sau khi đọc lại"
    )


def delete_facebook_video(
    video_id: str,
    access_token: str,
    target_gpm_profile_id: str = "",
) -> dict[str, Any]:
    clean_video_id = str(video_id or "").strip()
    clean_token = sanitize_fb_token(access_token)
    if not clean_video_id or not clean_token:
        raise ValueError("Video ID hoặc Access Token Facebook không hợp lệ")
    request = urllib.request.Request(
        f"{GRAPH_API_BASE}/{clean_video_id}",
        data=urllib.parse.urlencode({"access_token": clean_token}).encode("utf-8"),
        headers={"User-Agent": "NexusStudio/1.0"},
        method="DELETE",
    )
    proxy_url = _get_proxy_for_gpm_profile(target_gpm_profile_id)
    opener = _build_urllib_opener(proxy_url)
    try:
        with opener.open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise _facebook_http_error("Không xóa được video Facebook cũ", error) from error


def _fetch_source_metadata(
    youtube_url: str,
    source_gpm_profile_id: str,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "http_headers": {
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        "extractor_args": {"youtube": {"lang": ["vi"]}},
    }
    proxy_url = _get_proxy_for_gpm_profile(source_gpm_profile_id)
    if proxy_url:
        options["proxy"] = proxy_url
    with YoutubeDL(options) as downloader:
        return downloader.extract_info(youtube_url, download=False) or {}


def repair_fb_queue_item(
    item_id: int,
    *,
    allow_replacement: bool = True,
) -> dict[str, Any]:
    """Repair one already-created Facebook video without embedding video-specific IDs."""
    item = db.get_fb_crossposter_queue_item(item_id)
    if not item:
        raise ValueError(f"Không tìm thấy video ID #{item_id} trong hàng đợi")
    old_video_id = str(item.get("fb_post_id") or "").strip()
    if not old_video_id:
        raise ValueError("Queue item chưa có Facebook Video ID để sửa")

    target_page_id = str(item.get("target_page_id") or "").strip()
    settings = db.get_fb_crossposter_runtime_settings(target_page_id)
    access_token = sanitize_fb_token(settings.get("target_access_token"))
    if not access_token:
        raise ValueError("Page Access Token Facebook đang trống hoặc không hợp lệ")
    source_profile_id = str(settings.get("source_gpm_profile_id") or "")
    target_profile_id = str(settings.get("target_gpm_profile_id") or "")
    default_tags = settings.get("default_tags", [])
    source_info = _fetch_source_metadata(item["youtube_url"], source_profile_id)

    source_title = str(source_info.get("title") or item.get("original_title") or "").strip()
    source_description = str(source_info.get("description") or "")
    source_tags = source_info.get("tags") or []
    if not isinstance(source_tags, list):
        source_tags = _unique_tag_keywords(source_tags)
    source_thumbnail_url = str(
        source_info.get("thumbnail") or item.get("thumbnail_url") or ""
    ).strip()
    auto_caption = _caption_is_automatic(
        item,
        settings.get("post_template", ""),
        default_tags=default_tags,
    )
    caption = (
        build_fb_caption(
            item.get("fb_title") or source_title,
            source_description,
            source_tags,
            settings.get("post_template"),
            default_tags=default_tags,
        )
        if auto_caption
        else append_missing_default_hashtags(
            str(item.get("fb_description") or ""),
            default_tags,
        )
    )
    title = str(item.get("fb_title") or source_title)
    content_tag_ids, skipped_tags = resolve_content_tag_ids(
        source_tags,
        access_token,
        target_gpm_profile_id=target_profile_id,
        raw_description=source_description,
        default_tags=default_tags,
    )
    custom_labels = get_custom_labels(
        source_tags,
        raw_description=source_description,
        default_tags=default_tags,
    )

    TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = TEMP_DOWNLOAD_DIR / f"repair_{item['youtube_id']}_{item_id}.jpg"
    if not source_thumbnail_url or not download_thumbnail(
        source_thumbnail_url,
        thumb_path,
        source_gpm_profile_id=source_profile_id,
    ):
        raise RuntimeError("Không thể chuẩn bị thumbnail để sửa video Facebook")
    if settings.get("convert_to_vertical"):
        try:
            convert_thumbnail_to_vertical(thumb_path)
        except Exception:
            thumb_path.unlink(missing_ok=True)
            raise

    db.update_fb_crossposter_queue_item(item_id, {
        "original_title": source_title,
        "original_description": source_description,
        "original_tags_json": json.dumps(source_tags, ensure_ascii=False),
        "thumbnail_url": source_thumbnail_url,
        "fb_description": caption,
        "fb_description_source": "auto" if auto_caption else "manual",
    })

    direct_error = ""
    try:
        result = update_facebook_video_metadata(
            old_video_id,
            access_token,
            title,
            caption,
            content_tag_ids=content_tag_ids,
            custom_labels=custom_labels,
            thumb_path=thumb_path,
            target_gpm_profile_id=target_profile_id,
        )
        db.update_fb_crossposter_queue_item(item_id, {"error_message": ""})
        return {
            **result,
            "mode": "updated",
            "skipped_tags": skipped_tags,
        }
    except Exception as exc:
        direct_error = security_logging.redact_sensitive(exc)
        logger.warning(
            "Direct Facebook metadata repair failed for queue item #%d: %s",
            item_id,
            direct_error,
        )
        if not allow_replacement:
            raise
    finally:
        thumb_path.unlink(missing_ok=True)

    old_status = str(item.get("status") or "published")
    try:
        replacement = process_queue_item_jit(item_id)
        replacement_id = str(replacement.get("fb_post_id") or "").strip()
        if not replacement_id or replacement_id == old_video_id:
            raise RuntimeError("Meta không trả về ID video thay thế hợp lệ")
        replacement_metadata = get_facebook_video_metadata(
            replacement_id,
            access_token,
            target_gpm_profile_id=target_profile_id,
        )
        replacement_verified = False
        for attempt in range(5):
            description_ok = str(replacement_metadata.get("description") or "") == caption
            tags_ok = not content_tag_ids or set(content_tag_ids).issubset(
                _content_tag_id_set(replacement_metadata.get("content_tags"))
            )
            thumbnail_ok = any(
                isinstance(thumbnail, dict) and thumbnail.get("is_preferred")
                for thumbnail in ((replacement_metadata.get("thumbnails") or {}).get("data") or [])
            )
            if description_ok and tags_ok and thumbnail_ok:
                replacement_verified = True
                break
            if attempt < 4:
                time.sleep(2)
                replacement_metadata = get_facebook_video_metadata(
                    replacement_id,
                    access_token,
                    target_gpm_profile_id=target_profile_id,
                )
        if not replacement_verified:
            raise RuntimeError(
                "Video thay thế chưa khớp mô tả, content_tags hoặc thumbnail ưu tiên"
            )
        delete_facebook_video(
            old_video_id,
            access_token,
            target_gpm_profile_id=target_profile_id,
        )
        return {
            "success": True,
            "mode": "replaced",
            "old_video_id": old_video_id,
            "video_id": replacement_id,
            "direct_error": direct_error,
            "skipped_tags": skipped_tags,
        }
    except Exception:
        current = db.get_fb_crossposter_queue_item(item_id) or {}
        replacement_id = str(current.get("fb_post_id") or "").strip()
        if replacement_id and replacement_id != old_video_id:
            try:
                delete_facebook_video(
                    replacement_id,
                    access_token,
                    target_gpm_profile_id=target_profile_id,
                )
            except Exception as cleanup_exc:
                logger.error(
                    "Could not remove failed Facebook replacement %s: %s",
                    replacement_id,
                    cleanup_exc,
                )
        db.update_fb_crossposter_queue_item(item_id, {
            "fb_post_id": old_video_id,
            "status": old_status,
        })
        raise


def process_queue_item_jit(
    item_id: int,
    parent_task_id: str | None = None,
    sys_job_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Execute Just-in-Time download, metadata prep, Facebook upload, and cleanup for one queue item."""
    item = db.get_fb_crossposter_queue_item(item_id)
    if not item:
        raise ValueError(f"Không tìm thấy video ID #{item_id} trong hàng đợi")

    target_page_id = str(item.get("target_page_id") or "").strip()
    settings = db.get_fb_crossposter_runtime_settings(target_page_id)
    page_id = str(settings.get("target_fb_page_id") or target_page_id).strip()
    access_token = sanitize_fb_token(settings.get("target_access_token"))

    page_name = str(settings.get("target_fb_page_name") or target_page_id or "Fanpage").strip()
    job_page_label = f"{page_name} ({target_page_id[-6:]})" if target_page_id and len(target_page_id) > 6 else page_name
    youtube_id = item["youtube_id"]
    youtube_url = item["youtube_url"]
    v_title = item.get("fb_title") or item.get("original_title", f"Video {youtube_id}")
    source_gpm_profile_id = settings.get("source_gpm_profile_id", "")
    target_gpm_profile_id = settings.get("target_gpm_profile_id", "")
    default_tags = settings.get("default_tags", [])

    # Idempotency Guard: Do not re-publish videos that have already succeeded unless explicitly forced
    existing_post_id = str(item.get("fb_post_id") or "").strip()
    if item.get("status") == "published" and existing_post_id and not force:
        logger.info(
            "Queue item #%d (%s) already published with Post ID %s on Page %s. Skipping duplicate upload.",
            item_id,
            youtube_id,
            existing_post_id,
            page_name,
        )
        if sys_job_id:
            try:
                db.update_system_job(
                    sys_job_id,
                    status="completed",
                    progress=f"Bỏ qua vì video đã được đăng trước đó lên Fanpage {page_name} (Post ID: {existing_post_id})",
                    finished_at=db.utc_now(),
                )
            except Exception:
                pass
        return {
            "success": True,
            "already_published": True,
            "fb_post_id": existing_post_id,
            "item_id": item_id,
            "title": v_title,
        }

    if not page_id or not access_token:
        raise ValueError(f"Chưa cấu hình Fanpage ID hoặc Access Token Facebook trong Settings cho Fanpage {page_name}")

    if not parent_task_id and not sys_job_id:
        sys_job_id = f"fb-pub-{item_id}-{int(time.time() * 1000)}"
        try:
            db.create_system_job(
                job_id=sys_job_id,
                job_type="fb_crosspost",
                title=f"Đăng Facebook ({job_page_label}): {v_title[:45]}",
                payload={
                    "item_id": item_id,
                    "youtube_id": youtube_id,
                    "target_page_id": target_page_id,
                    "page_name": page_name,
                },
            )
        except Exception as sys_exc:
            logger.warning("Could not register fb_crosspost system_job: %s", sys_exc)

    if sys_job_id:
        try:
            db.update_system_job(sys_job_id, status="running", progress="Đang tải video (JIT)...")
        except Exception:
            pass

    TEMP_DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    video_file = TEMP_DOWNLOAD_DIR / f"{youtube_id}_{item_id}.mp4"
    vertical_video_file = TEMP_DOWNLOAD_DIR / f"{youtube_id}_{item_id}_vertical.mp4"
    thumb_file = TEMP_DOWNLOAD_DIR / f"{youtube_id}_{item_id}.jpg"
    vertical_video_file.unlink(missing_ok=True)

    # Step 1: Set status to downloading
    db.update_fb_crossposter_queue_item(item_id, {"status": "downloading", "error_message": ""})

    proxy_url = _get_proxy_for_gpm_profile(source_gpm_profile_id)
    logger.info("JIT Downloading video %s (#%d) (Proxy: %s)", youtube_id, item_id, proxy_url or "Direct")

    ydl_opts: dict[str, Any] = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "ffmpeg_location": ensure_ffmpeg_directory(),
        "outtmpl": str(TEMP_DOWNLOAD_DIR / f"{youtube_id}_{item_id}.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "http_headers": {
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        "extractor_args": {
            "youtube": {"lang": ["vi"]},
        },
    }
    if proxy_url:
        ydl_opts["proxy"] = proxy_url

    try:
        with YoutubeDL(ydl_opts) as ydl:
            source_info = ydl.extract_info(youtube_url, download=True) or {}

        if not video_file.is_file():
            # Check if downloaded with another extension
            candidates = list(TEMP_DOWNLOAD_DIR.glob(f"{youtube_id}_{item_id}.*"))
            video_candidates = [c for c in candidates if c.suffix in {".mp4", ".mkv", ".webm"}]
            if video_candidates:
                video_file = video_candidates[0]
            else:
                raise FileNotFoundError(f"Không tìm thấy file video sau khi tải: {youtube_id}")

        source_title = str(source_info.get("title") or item.get("original_title") or "").strip()
        source_description = str(
            source_info.get("description") or item.get("original_description") or ""
        )
        source_tags = source_info.get("tags") or item.get("original_tags") or []
        if not isinstance(source_tags, list):
            source_tags = _unique_tag_keywords(source_tags)
        source_thumbnail_url = str(
            source_info.get("thumbnail") or item.get("thumbnail_url") or ""
        ).strip()
        auto_caption = _caption_is_automatic(
            item,
            settings.get("post_template", ""),
            default_tags=default_tags,
        )

        metadata_updates: dict[str, Any] = {
            "original_title": source_title,
            "original_description": source_description,
            "original_tags_json": json.dumps(source_tags, ensure_ascii=False),
            "thumbnail_url": source_thumbnail_url,
        }
        if auto_caption:
            metadata_updates.update({
                "fb_description": build_fb_caption(
                    item.get("fb_title") or source_title,
                    source_description,
                    source_tags,
                    settings.get("post_template"),
                    default_tags=default_tags,
                ),
                "fb_description_source": "auto",
            })
        db.update_fb_crossposter_queue_item(item_id, metadata_updates)
        item.update(metadata_updates)
        item["original_tags"] = source_tags

        if not source_thumbnail_url:
            raise RuntimeError("Video YouTube không có thumbnail để gửi lên Facebook")
        if not download_thumbnail(
            source_thumbnail_url,
            thumb_file,
            source_gpm_profile_id=source_gpm_profile_id,
        ):
            raise RuntimeError("Không thể tải hoặc chuẩn hóa thumbnail YouTube")

        upload_video_file = video_file
        if settings.get("convert_to_vertical"):
            if sys_job_id:
                try:
                    db.update_system_job(
                        sys_job_id,
                        status="running",
                        progress="Đang chuyển video và thumbnail sang 9:16...",
                    )
                except Exception:
                    pass
            upload_video_file = convert_video_to_vertical(
                video_file,
                vertical_video_file,
            )
            convert_thumbnail_to_vertical(thumb_file)

        db.update_fb_crossposter_queue_item(
            item_id,
            {"file_size_bytes": upload_video_file.stat().st_size},
        )

        # Step 2: Set status to uploading
        db.update_fb_crossposter_queue_item(item_id, {"status": "uploading"})
        if sys_job_id:
            try:
                db.update_system_job(sys_job_id, status="running", progress="Đang tải video lên Facebook...")
            except Exception:
                pass

        caption = item.get("fb_description") or build_fb_caption(
            item.get("fb_title") or item.get("original_title", ""),
            item.get("original_description", ""),
            item.get("original_tags", []),
            settings.get("post_template"),
            default_tags=default_tags,
        )
        if not auto_caption:
            caption = append_missing_default_hashtags(caption, default_tags)
        title = item.get("fb_title") or item.get("original_title", "")
        scheduled_time = item.get("scheduled_publish_time")
        content_tag_ids, skipped_tags = resolve_content_tag_ids(
            item.get("original_tags", []),
            access_token,
            target_gpm_profile_id=target_gpm_profile_id,
            raw_description=item.get("original_description", ""),
            default_tags=default_tags,
        )
        custom_labels = get_custom_labels(
            item.get("original_tags", []),
            raw_description=item.get("original_description", ""),
            default_tags=default_tags,
        )
        if skipped_tags:
            logger.warning(
                "Facebook content tags skipped for queue item #%d: %s",
                item_id,
                ", ".join(skipped_tags),
            )

        upload_result = upload_video_to_facebook(
            page_id=page_id,
            access_token=access_token,
            video_path=upload_video_file,
            title=title,
            description=caption,
            scheduled_publish_time=scheduled_time,
            thumb_path=thumb_file,
            content_tag_ids=content_tag_ids,
            custom_labels=custom_labels,
            target_gpm_profile_id=target_gpm_profile_id,
        )

        fb_post_id = upload_result.get("id") or ""

        # Step 3: Success -> Update DB
        db.update_fb_crossposter_queue_item(item_id, {
            "status": "published",
            "fb_post_id": str(fb_post_id),
            "error_message": "",
        })
        if sys_job_id:
            try:
                db.update_system_job(
                    sys_job_id,
                    status="completed",
                    progress=(
                        f"Đăng thành công lên Fanpage (Post ID: {fb_post_id}; "
                        f"thẻ Meta: {len(content_tag_ids)}/"
                        f"{len(_unique_tag_keywords(source_tags, default_tags=default_tags))})"
                    ),
                    finished_at=db.utc_now(),
                )
            except Exception:
                pass

        return {
            "success": True,
            "fb_post_id": str(fb_post_id),
            "item_id": item_id,
            "title": title,
        }

    except Exception as exc:
        safe_error = security_logging.redact_sensitive(exc)
        logger.error("JIT processing failed for video #%d (%s): %s", item_id, youtube_id, safe_error)
        db.update_fb_crossposter_queue_item(item_id, {
            "status": "error",
            "error_message": safe_error,
        })
        if sys_job_id:
            try:
                db.update_system_job(
                    sys_job_id,
                    status="failed",
                    progress=f"Lỗi đăng video: {safe_error[:100]}",
                    error=safe_error,
                    finished_at=db.utc_now(),
                )
            except Exception:
                pass
        raise RuntimeError(safe_error) from exc
    finally:
        # Step 4: Always cleanup temp files to preserve disk space
        if video_file.exists():
            try:
                video_file.unlink()
            except Exception:
                pass
        if vertical_video_file.exists():
            try:
                vertical_video_file.unlink()
            except Exception:
                pass
        if thumb_file.exists():
            try:
                thumb_file.unlink()
            except Exception:
                pass


# =========================================================================
# Batch Cloud Pre-Scheduler ("Lên lịch trước cho N ngày")
# =========================================================================

_schedule_ahead_tasks: dict[str, dict[str, Any]] = {}
_schedule_ahead_lock = threading.Lock()


def get_schedule_ahead_status(task_id: str | None = None, page_id: str | None = None) -> dict[str, Any]:
    """Get the current progress of a batch pre-schedule task."""
    with _schedule_ahead_lock:
        if task_id and task_id in _schedule_ahead_tasks:
            return dict(_schedule_ahead_tasks[task_id])
        if page_id:
            for tid, tdata in _schedule_ahead_tasks.items():
                if tdata.get("page_id") == page_id and tdata.get("status") in {"running", "starting"}:
                    return dict(tdata)
            # Find last task for page
            for tid, tdata in reversed(list(_schedule_ahead_tasks.items())):
                if tdata.get("page_id") == page_id:
                    return dict(tdata)
        return {"status": "idle", "message": "Không có tác vụ đặt lịch trước nào đang chạy"}


def get_schedule_ahead_batch_status(task_id: str) -> dict[str, Any] | None:
    """Get the live status and progress metrics of a pre-schedule batch task."""
    with _schedule_ahead_lock:
        task = _schedule_ahead_tasks.get(task_id)
        return dict(task) if task else None


def cancel_schedule_ahead_batch(task_id: str | None = None, page_id: str | None = None) -> bool:
    """Request cancellation of an active schedule-ahead background batch task."""
    cancelled = False
    target_ids: list[str] = []
    with _schedule_ahead_lock:
        if task_id and task_id in _schedule_ahead_tasks:
            target_ids.append(task_id)
        elif page_id:
            for tid, tdata in _schedule_ahead_tasks.items():
                if tdata.get("page_id") == page_id and tdata.get("status") in {"pending", "running"}:
                    target_ids.append(tid)
        elif not task_id and not page_id:
            for tid, tdata in _schedule_ahead_tasks.items():
                if tdata.get("status") in {"pending", "running"}:
                    target_ids.append(tid)

        for tid in target_ids:
            _schedule_ahead_tasks[tid]["cancel_requested"] = True
            cancelled = True

    # Also request cancel in system_jobs table
    try:
        for tid in target_ids:
            sys_id = f"fb-crosspost-{tid}" if not tid.startswith("fb-crosspost-") else tid
            db.request_cancel_system_job(sys_id)
    except Exception as exc:
        logger.warning("Could not request cancel in system_jobs: %s", exc)

    return cancelled


def _run_schedule_ahead_worker(
    task_id: str,
    target_page_id: str,
    days_ahead: int,
    sys_job_id: str = "",
):
    """Background worker thread executing the pre-scheduling batch."""
    if not sys_job_id:
        sys_job_id = f"fb-crosspost-{task_id}"
    try:
        # Fetch due items from queue that have scheduled_publish_time set within range
        items = db.get_fb_queue_items_for_schedule_ahead(days_ahead=days_ahead, target_page_id=target_page_id)
        total_items = len(items)

        with _schedule_ahead_lock:
            if task_id not in _schedule_ahead_tasks:
                return
            _schedule_ahead_tasks[task_id]["total_items"] = total_items
            _schedule_ahead_tasks[task_id]["status"] = "running"
        try:
            db.update_system_job(
                sys_job_id,
                status="running",
                progress=f"Đang chuẩn bị {len(items)} video để đặt lịch...",
            )
        except Exception:
            pass

        completed_count = 0
        last_error = ""
        for idx, item in enumerate(items):
            # Check cancel request (from RAM or system_jobs)
            sys_job = db.get_system_job(sys_job_id) if hasattr(db, "get_system_job") else None
            cancel_req = (
                _schedule_ahead_tasks[task_id].get("cancel_requested")
                or (sys_job and sys_job.get("cancel_requested") == 1)
                or (sys_job and sys_job.get("status") == "canceled")
            )
            if cancel_req:
                with _schedule_ahead_lock:
                    _schedule_ahead_tasks[task_id]["status"] = "canceled"
                    _schedule_ahead_tasks[task_id]["message"] = f"Đã hủy tác vụ (Đã hoàn thành {completed_count}/{len(items)} video)"
                try:
                    db.update_system_job(
                        sys_job_id,
                        status="canceled",
                        progress=f"Đã hủy theo yêu cầu (Đã xử lý {completed_count}/{len(items)} video)",
                        cancel_requested=0,
                        finished_at=db.utc_now(),
                    )
                except Exception:
                    pass
                return

            v_title = item.get("fb_title") or item.get("original_title", "")
            with _schedule_ahead_lock:
                _schedule_ahead_tasks[task_id]["current_index"] = idx + 1
                _schedule_ahead_tasks[task_id]["current_item_id"] = item["id"]
                _schedule_ahead_tasks[task_id]["current_video_title"] = v_title
                _schedule_ahead_tasks[task_id]["phase"] = "downloading"
                _schedule_ahead_tasks[task_id]["progress_percent"] = round((idx / len(items)) * 100, 1)

            try:
                db.update_system_job(
                    sys_job_id,
                    progress=f"[{idx + 1}/{len(items)}] Đang tải/đăng: {v_title[:45]}...",
                )
            except Exception:
                pass

            # JIT process single video
            try:
                with _schedule_ahead_lock:
                    _schedule_ahead_tasks[task_id]["phase"] = "uploading"
                process_queue_item_jit(item["id"], parent_task_id=task_id)
                completed_count += 1
            except Exception as v_err:
                last_error = str(v_err)
                logger.error("Schedule ahead failed for item #%d: %s", item["id"], v_err)
                with _schedule_ahead_lock:
                    _schedule_ahead_tasks[task_id]["error"] = last_error

            with _schedule_ahead_lock:
                _schedule_ahead_tasks[task_id]["completed_count"] = completed_count
                _schedule_ahead_tasks[task_id]["progress_percent"] = round(((idx + 1) / len(items)) * 100, 1)

            # Cooldown pause between consecutive videos to allow Meta ingest pipeline to settle
            if idx < len(items) - 1:
                with _schedule_ahead_lock:
                    _schedule_ahead_tasks[task_id]["phase"] = "cooldown"
                logger.info(
                    "Video %d/%d processed. Pausing 15s cooldown before next video in batch...",
                    idx + 1,
                    len(items),
                )
                try:
                    db.update_system_job(
                        sys_job_id,
                        progress=f"[{idx + 1}/{len(items)}] Hoàn tất. Đang nghỉ 15s trước video tiếp theo...",
                    )
                except Exception:
                    pass
                for _ in range(15):
                    sys_job = db.get_system_job(sys_job_id) if hasattr(db, "get_system_job") else None
                    if (
                        _schedule_ahead_tasks[task_id].get("cancel_requested")
                        or (sys_job and sys_job.get("cancel_requested") == 1)
                        or (sys_job and sys_job.get("status") == "canceled")
                    ):
                        break
                    time.sleep(1)

        if completed_count == 0 and len(items) > 0:
            msg = f"Lỗi: Không thể tải/đăng video nào ({last_error or 'Lỗi kết nối Facebook'})"
            task_status = "error"
        elif completed_count < len(items):
            msg = f"Đã hoàn thành {completed_count}/{len(items)} video ({len(items) - completed_count} video lỗi: {last_error})"
            task_status = "completed"
        else:
            msg = f"Đã hoàn thành đặt lịch trước cho {completed_count}/{len(items)} video lên Meta Cloud ({days_ahead} ngày)"
            task_status = "completed"

        with _schedule_ahead_lock:
            _schedule_ahead_tasks[task_id]["status"] = task_status
            _schedule_ahead_tasks[task_id]["message"] = msg
        try:
            db.update_system_job(
                sys_job_id,
                status=task_status if task_status == "completed" else "failed",
                progress=msg,
                error=last_error if task_status == "error" else "",
                finished_at=db.utc_now(),
            )
        except Exception:
            pass

    except Exception as exc:
        safe_error = security_logging.redact_sensitive(exc)
        logger.error("Fatal error in schedule ahead batch %s: %s", task_id, safe_error)
        with _schedule_ahead_lock:
            _schedule_ahead_tasks[task_id]["status"] = "error"
            _schedule_ahead_tasks[task_id]["error"] = safe_error
        try:
            db.update_system_job(
                sys_job_id,
                status="failed",
                error=safe_error,
                finished_at=db.utc_now(),
            )
        except Exception:
            pass


def start_schedule_ahead_batch(
    target_page_id: str,
    days_ahead: int,
    existing_sys_job_id: str = "",
) -> dict[str, Any]:
    """Start background sequential batch upload to Meta Cloud for N days ahead."""
    if days_ahead < 1 or days_ahead > 60:
        raise ValueError("Số ngày đặt lịch trước phải từ 1 đến 60 ngày")

    settings = db.get_fb_crossposter_runtime_settings(target_page_id)
    if not settings.get("target_access_token"):
        raise ValueError("Chưa cấu hình Page Access Token cho Fanpage này. Vui lòng nhập Access Token, bấm [Kiểm tra] -> [Lưu Cấu Hình] trước khi đặt lịch.")
    if not settings.get("target_fb_page_id") and not target_page_id:
        raise ValueError("Chưa cấu hình Fanpage ID cho chiến dịch này.")

    if existing_sys_job_id:
        sys_job_id = existing_sys_job_id
        if sys_job_id.startswith("fb-crosspost-"):
            task_id = sys_job_id[len("fb-crosspost-"):]
        else:
            task_id = f"task_{int(time.time())}_{target_page_id or 'default'}"
    else:
        task_id = f"task_{int(time.time())}_{target_page_id or 'default'}"
        sys_job_id = f"fb-crosspost-{task_id}"

    with _schedule_ahead_lock:
        # Check if already running for this page
        for tid, tdata in _schedule_ahead_tasks.items():
            if tid != task_id and tdata.get("page_id") == target_page_id and tdata.get("status") in {"running", "starting"}:
                return tdata

        task_record = {
            "task_id": task_id,
            "page_id": target_page_id,
            "days_ahead": days_ahead,
            "status": "starting",
            "current_index": 0,
            "total_items": 0,
            "completed_count": 0,
            "current_video_title": "",
            "phase": "init",
            "progress_percent": 0.0,
            "cancel_requested": False,
            "error": "",
            "created_at": datetime.datetime.now().isoformat(),
        }
        _schedule_ahead_tasks[task_id] = task_record

    # Create or update system_job for central monitoring in Job Center
    try:
        page_name = settings.get("page_name") or target_page_id or "Fanpage"
        if not existing_sys_job_id:
            db.create_system_job(
                job_id=sys_job_id,
                job_type="fb_crosspost",
                title=f"Đăng chéo Facebook ({page_name}) - Đặt lịch {days_ahead} ngày",
                payload={"task_id": task_id, "page_id": target_page_id, "days_ahead": days_ahead},
            )
        db.update_system_job(sys_job_id, status="running", progress="Đang khởi tạo danh sách video...")
    except Exception as exc:
        logger.warning("Could not register fb_crosspost system_job: %s", exc)

    worker_thread = threading.Thread(
        target=_run_schedule_ahead_worker,
        args=(task_id, target_page_id, days_ahead, sys_job_id),
        name=f"ScheduleAheadWorker-{target_page_id}",
        daemon=True,
    )
    worker_thread.start()

    return task_record


class FbCrossPosterScheduler:
    """Background daemon task that handles recurring auto-sync and auto-publishing across all Fanpage campaigns."""

    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_sync_checks: dict[str, float] = {}

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="FbCrossPosterScheduler",
        )
        self._thread.start()
        logger.info("FbCrossPosterScheduler background service started")

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("FbCrossPosterScheduler background service stopped")

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.error("Error in FbCrossPosterScheduler tick: %s", exc)
            self._stop_event.wait(timeout=60)  # Check every 60 seconds

    def _tick(self):
        campaigns = db.list_all_crossposter_campaigns()
        # If no explicit campaigns in DB, fallback to default settings
        if not campaigns:
            campaigns = [{"page_id": "", "page_name": "Default"}]

        now = datetime.datetime.now()
        now_ts = int(now.timestamp())

        for camp in campaigns:
            page_id = camp.get("page_id", "")
            settings = db.get_fb_crossposter_settings(page_id)

            # 1. Auto-Sync Check
            if settings.get("auto_sync_enabled") and settings.get("source_channel_id"):
                channel_url = settings["source_channel_id"]
                gpm_profile_id = settings.get("source_gpm_profile_id", "")
                sync_type = settings.get("auto_sync_type", "interval")
                should_sync = False
                last_check_ts = self._last_sync_checks.get(page_id, 0.0)

                if sync_type == "interval":
                    interval_hours = max(1, settings.get("auto_sync_interval_hours", 6))
                    last_synced_str = settings.get("last_synced_at") or ""
                    # Cooldown guard: never re-trigger within 5 minutes of previous check
                    if (now_ts - last_check_ts) >= 300:
                        if not last_synced_str:
                            should_sync = True
                        else:
                            try:
                                last_dt = datetime.datetime.fromisoformat(last_synced_str)
                                if (now - last_dt).total_seconds() >= interval_hours * 3600:
                                    should_sync = True
                            except Exception:
                                should_sync = True

                elif sync_type == "fixed_time":
                    fixed_times = settings.get("auto_sync_fixed_times", ["06:00", "18:00"])
                    current_hhmm = now.strftime("%H:%M")
                    if current_hhmm in fixed_times and (now_ts - last_check_ts > 70):
                        should_sync = True

                if should_sync:
                    self._last_sync_checks[page_id] = now_ts
                    logger.info("Triggering scheduled auto-sync for Page %s (Channel: %s)", page_id or "Default", channel_url)
                    try:
                        sync_channel_public_videos(
                            channel_url=channel_url,
                            gpm_profile_id=gpm_profile_id,
                            sort_order_mode=settings.get("sort_order_mode", "oldest_first"),
                            target_page_id=page_id,
                        )
                    except Exception as sync_exc:
                        logger.error("Auto-sync failed for Page %s: %s", page_id, sync_exc)

            # 2. Auto-Publish Check with Lead-Time Buffer
            if settings.get("auto_publish_enabled"):
                lead_time_minutes = max(0, settings.get("lead_time_minutes", 60))
                # Trigger upload if scheduled publish time is within lead_time_minutes from now
                target_threshold_ts = now_ts + (lead_time_minutes * 60)

                conn = db.sqlite3.connect(str(db.DB_PATH), timeout=30)
                conn.row_factory = db.sqlite3.Row
                try:
                    where_clause = "status = 'scheduled' AND scheduled_publish_time <= ?"
                    params: list[Any] = [target_threshold_ts]
                    if page_id:
                        where_clause += " AND target_page_id = ?"
                        params.append(page_id)
                    else:
                        where_clause += " AND (target_page_id = '' OR target_page_id IS NULL)"

                    due_item = conn.execute(f"""
                        SELECT id FROM fb_crossposter_queue
                        WHERE {where_clause}
                        ORDER BY scheduled_publish_time ASC
                        LIMIT 1
                    """, params).fetchone()
                finally:
                    conn.close()

                if due_item:
                    item_id = due_item["id"]
                    logger.info("Executing scheduled publication for queue item #%d (Lead-time: %d mins)", item_id, lead_time_minutes)
                    try:
                        process_queue_item_jit(item_id)
                    except Exception as pub_exc:
                        logger.error("Scheduled publication failed for item #%d: %s", item_id, pub_exc)


# Global scheduler instance
scheduler = FbCrossPosterScheduler()
