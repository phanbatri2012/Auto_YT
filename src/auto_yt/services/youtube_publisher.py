"""Idempotent YouTube video upload and post-upload finalization helpers."""

from __future__ import annotations

import datetime as dt
import json
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from auto_yt.services import database as db
from auto_yt.services.proxy_utils import create_proxy_opener


YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
MAX_API_RESPONSE_BYTES = 10 * 1024 * 1024


class YouTubePublishError(RuntimeError):
    pass


class YouTubeUploadReconciliationRequired(YouTubePublishError):
    pass


class YouTubePublicUploadRestricted(YouTubePublishError):
    pass


class YouTubeAuthenticationError(YouTubePublishError):
    pass


class YouTubeQuotaExceeded(YouTubePublishError):
    pass


class YouTubeProcessingFailed(YouTubePublishError):
    pass


class YouTubeProcessingPending(YouTubePublishError):
    def __init__(self, message: str, delay_seconds: int = 120):
        super().__init__(message)
        self.delay_seconds = delay_seconds


class YouTubeTransientError(YouTubePublishError):
    pass


def _decode_response(response) -> dict:
    body = response.read(MAX_API_RESPONSE_BYTES + 1)
    if len(body) > MAX_API_RESPONSE_BYTES:
        raise YouTubePublishError("Phản hồi YouTube vượt giới hạn an toàn.")
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YouTubePublishError("YouTube trả về dữ liệu không hợp lệ.") from exc
    return payload if isinstance(payload, dict) else {}


def _error_details(error: urllib.error.HTTPError) -> tuple[str, set[str]]:
    try:
        payload = json.loads(error.read(MAX_API_RESPONSE_BYTES).decode("utf-8"))
        api_error = payload.get("error") if isinstance(payload, dict) else {}
        message = api_error.get("message") if isinstance(api_error, dict) else ""
        reasons = {
            str(item.get("reason") or "").strip()
            for item in (api_error.get("errors") or [])
            if isinstance(item, dict) and str(item.get("reason") or "").strip()
        }
        if message:
            return str(message), reasons
    except Exception:
        pass
    return f"YouTube HTTP {error.code}", set()


def _publish_error_from_http(error: urllib.error.HTTPError) -> YouTubePublishError:
    message, reasons = _error_details(error)
    if "forbiddenPrivacySetting" in reasons:
        return YouTubePublicUploadRestricted(
            "YouTube giới hạn video của API project ở chế độ Private; "
            "hãy hoàn tất API Compliance Audit trước khi đặt lịch."
        )
    if error.code == 401 or reasons & {
        "authError",
        "invalidCredentials",
        "youtubeSignupRequired",
    }:
        return YouTubeAuthenticationError(
            "Quyền OAuth YouTube không còn hợp lệ; hãy kết nối lại kênh."
        )
    if reasons & {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"}:
        return YouTubeQuotaExceeded(
            "YouTube API đã hết quota; hãy chờ quota được cấp lại rồi tiếp tục."
        )
    if (
        error.code == 429
        or reasons & {"rateLimitExceeded", "userRateLimitExceeded"}
        or 500 <= error.code <= 599
    ):
        return YouTubeTransientError(message)
    if error.code == 403:
        return YouTubeAuthenticationError(message)
    return YouTubePublishError(message)


def _api_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    content_type: str = "application/json; charset=UTF-8",
    raw_body: bytes | None = None,
    timeout: float = 60,
    proxy: str | None = None,
) -> dict:
    body = raw_body
    if body is None and payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = create_proxy_opener(proxy, timeout=timeout, require_proxy=True)
    try:
        with opener.open(request, timeout=timeout) as response:
            return _decode_response(response)
    except urllib.error.HTTPError as exc:
        raise _publish_error_from_http(exc) from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise YouTubeTransientError("Không kết nối được YouTube API.") from exc


def build_upload_metadata(video: dict, publishing_settings: dict) -> dict:
    title = str(video.get("generated_title") or video.get("title") or "").strip()
    description = str(video.get("description") or "").strip()
    if not description:
        description = db.extract_generated_video_description(
            video.get("generated_script") or ""
        )
    if not title:
        raise YouTubePublishError("Video chưa có tiêu đề YouTube hợp lệ.")
    if len(title) > 100:
        raise YouTubePublishError("Tiêu đề YouTube vượt quá 100 ký tự.")
    if len(description) > 5000:
        raise YouTubePublishError("Mô tả YouTube vượt quá 5000 ký tự.")
    category_id = str(publishing_settings.get("category_id") or "22").strip() or "22"
    made_for_kids = bool(publishing_settings.get("made_for_kids", False))
    tags = list(dict.fromkeys(re.findall(r"(?<!\w)#([\w-]+)", description)))[:30]
    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
            "defaultLanguage": str(publishing_settings.get("language") or "vi"),
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": made_for_kids,
            "containsSyntheticMedia": bool(
                publishing_settings.get("contains_synthetic_media", True)
            ),
            "embeddable": True,
            "license": "youtube",
            "publicStatsViewable": True,
        },
    }


def start_resumable_upload(
    *,
    token: str,
    video_path: Path,
    metadata: dict,
    notify_subscribers: bool,
    proxy: str | None = None,
) -> str:
    query = urllib.parse.urlencode(
        {
            "uploadType": "resumable",
            "part": "snippet,status",
            "notifySubscribers": str(bool(notify_subscribers)).lower(),
        }
    )
    body = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{YOUTUBE_UPLOAD_BASE}/videos?{query}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Content-Length": str(len(body)),
            "X-Upload-Content-Length": str(video_path.stat().st_size),
            "X-Upload-Content-Type": "video/mp4",
        },
        method="POST",
    )
    opener = create_proxy_opener(proxy, timeout=60, require_proxy=True)
    try:
        with opener.open(request, timeout=60) as response:
            session_url = str(response.headers.get("Location") or "").strip()
    except urllib.error.HTTPError as exc:
        raise _publish_error_from_http(exc) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise YouTubeTransientError(
            "Không khởi tạo được phiên upload YouTube."
        ) from exc
    if not session_url.startswith("https://www.googleapis.com/"):
        raise YouTubePublishError("YouTube không trả về upload session hợp lệ.")
    return session_url


def query_upload_offset(session_url: str, token: str, total_size: int, proxy: str | None = None) -> tuple[int, dict | None]:
    request = urllib.request.Request(
        session_url,
        data=b"",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Length": "0",
            "Content-Range": f"bytes */{total_size}",
        },
        method="PUT",
    )
    opener = create_proxy_opener(proxy, timeout=60, require_proxy=True)
    try:
        with opener.open(request, timeout=60) as response:
            return total_size, _decode_response(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 308:
            received = str(exc.headers.get("Range") or "")
            match = re.search(r"bytes=0-(\d+)", received)
            return (int(match.group(1)) + 1 if match else 0), None
        if exc.code in {404, 410}:
            raise YouTubeUploadReconciliationRequired(
                "Phiên upload đã hết hạn và chưa xác định được video từ xa."
            ) from exc
        raise _publish_error_from_http(exc) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise YouTubeTransientError(
            "Không kiểm tra được tiến độ upload YouTube."
        ) from exc


def upload_video_resumable(
    *,
    session_url: str,
    video_path: Path,
    token_provider,
    start_offset: int,
    persist_progress,
    cancel_check,
    persist_remote_video=lambda _payload: None,
    resume_session: bool = False,
    proxy: str | None = None,
) -> dict:
    total_size = video_path.stat().st_size
    offset = max(0, min(int(start_offset or 0), total_size))
    if offset or resume_session:
        offset, completed = query_upload_offset(session_url, token_provider(), total_size, proxy=proxy)
        persist_progress(offset)
        if completed and completed.get("id"):
            persist_remote_video(completed)
            return completed
    opener = create_proxy_opener(proxy, timeout=180, require_proxy=True)
    with video_path.open("rb") as source:
        source.seek(offset)
        while offset < total_size:
            cancel_check()
            chunk = source.read(min(UPLOAD_CHUNK_SIZE, total_size - offset))
            if not chunk:
                break
            end = offset + len(chunk) - 1
            request = urllib.request.Request(
                session_url,
                data=chunk,
                headers={
                    "Authorization": f"Bearer {token_provider()}",
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{total_size}",
                },
                method="PUT",
            )
            try:
                with opener.open(request, timeout=180) as response:
                    payload = _decode_response(response)
                    if payload.get("id"):
                        persist_remote_video(payload)
                        persist_progress(total_size)
                        return payload
                    raise YouTubePublishError("YouTube hoàn tất chunk nhưng thiếu Video ID.")
            except urllib.error.HTTPError as exc:
                if exc.code == 308:
                    received = str(exc.headers.get("Range") or "")
                    match = re.search(r"bytes=0-(\d+)", received)
                    offset = int(match.group(1)) + 1 if match else end + 1
                    source.seek(offset)
                    persist_progress(offset)
                    continue
                if exc.code in {404, 410}:
                    raise YouTubeUploadReconciliationRequired(
                        "Phiên upload hết hạn trong khi chưa nhận được Video ID."
                    ) from exc
                raise _publish_error_from_http(exc) from exc
            except (urllib.error.URLError, TimeoutError, OSError):
                offset, completed = query_upload_offset(
                    session_url, token_provider(), total_size, proxy=proxy
                )
                persist_progress(offset)
                if completed and completed.get("id"):
                    persist_remote_video(completed)
                    return completed
                source.seek(offset)
    raise YouTubePublishError("Upload kết thúc nhưng YouTube chưa trả Video ID.")


def upload_thumbnail(token: str, youtube_video_id: str, thumbnail_path: Path, proxy: str | None = None) -> dict:
    query = urllib.parse.urlencode(
        {"videoId": youtube_video_id, "uploadType": "media"}
    )
    mime_type = mimetypes.guess_type(thumbnail_path.name)[0] or "image/png"
    return _api_json(
        f"{YOUTUBE_UPLOAD_BASE}/thumbnails/set?{query}",
        token,
        method="POST",
        raw_body=thumbnail_path.read_bytes(),
        content_type=mime_type,
        timeout=120,
        proxy=proxy,
    )


def _multipart_related(metadata: dict, media: bytes, media_type: str) -> tuple[bytes, str]:
    boundary = f"auto_yt_{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        + json.dumps(metadata, ensure_ascii=False)
        + f"\r\n--{boundary}\r\nContent-Type: {media_type}\r\n\r\n"
    ).encode("utf-8") + media + f"\r\n--{boundary}--\r\n".encode("ascii")
    return body, f"multipart/related; boundary={boundary}"


def find_caption_track(token: str, youtube_video_id: str, language: str, name: str, proxy: str | None = None) -> str:
    query = urllib.parse.urlencode({"part": "snippet", "videoId": youtube_video_id})
    payload = _api_json(f"{YOUTUBE_API_BASE}/captions?{query}", token, proxy=proxy)
    for item in payload.get("items") or []:
        snippet = item.get("snippet") or {}
        if snippet.get("language") == language and snippet.get("name") == name:
            return str(item.get("id") or "")
    return ""


def upload_caption(
    token: str,
    youtube_video_id: str,
    srt_path: Path,
    *,
    language: str = "vi",
    name: str = "Tiếng Việt",
    proxy: str | None = None,
) -> dict:
    track_id = find_caption_track(token, youtube_video_id, language, name, proxy=proxy)
    snippet = {
        "videoId": youtube_video_id,
        "language": language,
        "name": name,
        "isDraft": False,
    }
    metadata = {"id": track_id, "snippet": snippet} if track_id else {"snippet": snippet}
    body, content_type = _multipart_related(
        metadata, srt_path.read_bytes(), "application/octet-stream"
    )
    query = urllib.parse.urlencode(
        {"part": "snippet", "uploadType": "multipart"}
    )
    endpoint = "captions" if not track_id else "captions"
    return _api_json(
        f"{YOUTUBE_UPLOAD_BASE}/{endpoint}?{query}",
        token,
        method="PUT" if track_id else "POST",
        raw_body=body,
        content_type=content_type,
        timeout=120,
        proxy=proxy,
    )


def get_video_processing(token: str, youtube_video_id: str, proxy: str | None = None) -> dict:
    query = urllib.parse.urlencode(
        {"part": "status,processingDetails,snippet", "id": youtube_video_id}
    )
    payload = _api_json(f"{YOUTUBE_API_BASE}/videos?{query}", token, proxy=proxy)
    items = payload.get("items") or []
    if not items:
        raise YouTubePublishError("Không tìm thấy video vừa upload trên YouTube.")
    item = items[0]
    return {
        "status": item.get("status") or {},
        "processing": item.get("processingDetails") or {},
        "snippet": item.get("snippet") or {},
    }


def require_processing_succeeded(token: str, youtube_video_id: str, proxy: str | None = None) -> dict:
    details = get_video_processing(token, youtube_video_id, proxy=proxy)
    upload_status = str(details["status"].get("uploadStatus") or "")
    processing_status = str(details["processing"].get("processingStatus") or "")
    if upload_status in {"failed", "rejected"} or processing_status in {
        "failed",
        "terminated",
    }:
        reason = (
            details["status"].get("failureReason")
            or details["status"].get("rejectionReason")
            or details["processing"].get("processingFailureReason")
            or "unknown"
        )
        raise YouTubeProcessingFailed(f"YouTube xử lý video thất bại: {reason}")
    if upload_status != "processed" and processing_status != "succeeded":
        raise YouTubeProcessingPending("YouTube vẫn đang xử lý video.")
    return details


def _parse_api_datetime(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def schedule_video(
    token: str,
    youtube_video_id: str,
    publish_at: str,
    *,
    preserved_status: dict | None = None,
    proxy: str | None = None,
) -> dict:
    try:
        requested_publish_at = _parse_api_datetime(publish_at)
    except (TypeError, ValueError) as exc:
        raise YouTubePublishError("Khung giờ đăng YouTube không hợp lệ.") from exc
    if requested_publish_at <= dt.datetime.now(dt.timezone.utc):
        raise YouTubePublishError(
            "Từ chối đặt lịch YouTube trong quá khứ để tránh công khai ngay lập tức."
        )
    source_status = preserved_status if isinstance(preserved_status, dict) else {}
    status = {
        "privacyStatus": "private",
        "publishAt": publish_at,
        "selfDeclaredMadeForKids": bool(
            source_status.get("selfDeclaredMadeForKids", False)
        ),
        "containsSyntheticMedia": bool(
            source_status.get("containsSyntheticMedia", True)
        ),
        "license": str(source_status.get("license") or "youtube"),
        "embeddable": bool(source_status.get("embeddable", True)),
        "publicStatsViewable": bool(source_status.get("publicStatsViewable", True)),
    }
    query = urllib.parse.urlencode({"part": "status"})
    result = _api_json(
        f"{YOUTUBE_API_BASE}/videos?{query}",
        token,
        method="PUT",
        payload={"id": youtube_video_id, "status": status},
        proxy=proxy,
    )
    response_status = result.get("status") if isinstance(result, dict) else None
    try:
        confirmed_publish_at = _parse_api_datetime(
            str((response_status or {}).get("publishAt") or "")
        )
    except (TypeError, ValueError) as exc:
        raise YouTubeUploadReconciliationRequired(
            "YouTube đã nhận yêu cầu nhưng không xác nhận khung giờ; cần đối soát từ xa."
        ) from exc
    if (
        str(result.get("id") or "") != youtube_video_id
        or str((response_status or {}).get("privacyStatus") or "") != "private"
        or confirmed_publish_at != requested_publish_at
    ):
        raise YouTubeUploadReconciliationRequired(
            "YouTube trả về trạng thái đặt lịch không khớp; cần đối soát từ xa."
        )
    return result
