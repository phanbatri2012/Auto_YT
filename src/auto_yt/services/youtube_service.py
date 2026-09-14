import urllib.parse as urlparse
import re
from html import unescape

from curl_cffi import requests as curl_requests
import truststore
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget
from auto_yt.services.network_security import validate_https_url


YOUTUBE_REQUEST_TIMEOUT_SECONDS = 30
MAX_YOUTUBE_RESPONSE_BYTES = 10 * 1024 * 1024
YOUTUBE_PAGE_HOSTS = ("youtube.com", "youtu.be")
YOUTUBE_MEDIA_HOSTS = ("youtube.com", "googlevideo.com", "ytimg.com")


def validate_youtube_video_url(url: str) -> str:
    return validate_https_url(
        url,
        allowed_hosts=YOUTUBE_PAGE_HOSTS,
        allow_subdomains=True,
    )


def _configure_system_trust_store() -> None:
    # Honor managed OS certificate authorities while keeping TLS verification enabled.
    truststore.inject_into_ssl()


_configure_system_trust_store()

def get_video_title(url: str) -> str:
    """Fetches the video title from YouTube URL."""
    try:
        response = curl_requests.get(
            validate_youtube_video_url(url),
            impersonate="chrome",
            timeout=YOUTUBE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        response_url = getattr(response, "url", url)
        validate_youtube_video_url(
            str(response_url) if isinstance(response_url, (str, bytes)) else url
        )
        response_content = getattr(response, "content", b"")
        if isinstance(response_content, bytes) and len(response_content) > MAX_YOUTUBE_RESPONSE_BYTES:
            raise ValueError("YouTube page exceeds the configured size limit")
        page_html = response.text
        match = re.search(r'<title>(.*?)</title>', page_html)
        if match:
            return unescape(match.group(1)).replace(' - YouTube', '').strip()
    except Exception as e:
        print(f"Failed to fetch title: {e}")
    return "Unknown Title"

def extract_video_id(url: str) -> str:
    """Extracts the video ID from a YouTube URL."""
    url_data = urlparse.urlparse(validate_youtube_video_url(url))
    query = urlparse.parse_qs(url_data.query)
    if 'v' in query:
        return query['v'][0]
    elif url_data.netloc == 'youtu.be':
        return url_data.path.lstrip('/')
    else:
        raise ValueError("Could not extract video ID from URL")

def _get_transcript_with_ytdlp(url: str) -> str:
    url = validate_youtube_video_url(url)
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['vi', 'en'],
        'quiet': True,
        'javascript_runtimes': ['node'],
        'impersonate': ImpersonateTarget(client='chrome')
    }
    
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        subs = {}
        for cap_type in ['automatic_captions', 'subtitles']:
            if info.get(cap_type):
                for lang, formats in info[cap_type].items():
                    if lang not in subs:
                        subs[lang] = []
                    subs[lang].extend(formats)

        target_fmt = None
        for lang in ['vi', 'en']:
            if lang in subs:
                for fmt in subs[lang]:
                    if fmt['ext'] == 'json3':
                        target_fmt = fmt
                        break
                if target_fmt:
                    break

        if not target_fmt:
            raise ValueError("No transcript found for vi or en")

        transcript_url = validate_https_url(
            target_fmt['url'],
            allowed_hosts=YOUTUBE_MEDIA_HOSTS,
            allow_subdomains=True,
        )
        response = curl_requests.get(
            transcript_url,
            impersonate="chrome",
            timeout=YOUTUBE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        response_url = getattr(response, "url", transcript_url)
        validate_https_url(
            str(response_url) if isinstance(response_url, (str, bytes)) else transcript_url,
            allowed_hosts=YOUTUBE_MEDIA_HOSTS,
            allow_subdomains=True,
        )
        response_content = getattr(response, "content", b"")
        if isinstance(response_content, bytes) and len(response_content) > MAX_YOUTUBE_RESPONSE_BYTES:
            raise ValueError("YouTube transcript exceeds the configured size limit")
        data = response.json()

        text_lines = []
        for event in data.get('events', []):
            if 'segs' in event:
                text = "".join(seg.get('utf8', '') for seg in event['segs'])
                if text.strip():
                    text_lines.append(text.strip())

        transcript = " ".join(text_lines).replace("\n", " ").strip()
        if not transcript:
            raise ValueError("YouTube returned an empty transcript")
        return transcript


def _get_transcript_with_transcript_api(url: str) -> str:
    video_id = extract_video_id(url)
    fetched_transcript = YouTubeTranscriptApi().fetch(
        video_id,
        languages=("vi", "en"),
    )
    text_lines = []
    for snippet in fetched_transcript:
        text = (
            snippet.get("text", "")
            if isinstance(snippet, dict)
            else getattr(snippet, "text", "")
        )
        if str(text).strip():
            text_lines.append(str(text).strip())
    transcript = " ".join(text_lines).replace("\n", " ").strip()
    if not transcript:
        raise ValueError("YouTube returned an empty transcript")
    return transcript


def get_video_transcript(url: str) -> str:
    """Fetch a transcript with an independent fallback provider."""
    try:
        return _get_transcript_with_ytdlp(url)
    except Exception as primary_error:
        try:
            return _get_transcript_with_transcript_api(url)
        except Exception as fallback_error:
            raise RuntimeError(
                "Không thể tải transcript từ YouTube qua cả hai nguồn. "
                f"yt-dlp: {primary_error}; transcript API: {fallback_error}"
            ) from fallback_error
