import urllib.parse as urlparse
import re

from curl_cffi import requests as curl_requests
import truststore
from yt_dlp import YoutubeDL
from yt_dlp.networking.impersonate import ImpersonateTarget


YOUTUBE_REQUEST_TIMEOUT_SECONDS = 30


def _configure_system_trust_store() -> None:
    # Honor managed OS certificate authorities while keeping TLS verification enabled.
    truststore.inject_into_ssl()


_configure_system_trust_store()

def get_video_title(url: str) -> str:
    """Fetches the video title from YouTube URL."""
    try:
        response = curl_requests.get(
            url,
            impersonate="chrome",
            timeout=YOUTUBE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        html = response.text
        match = re.search(r'<title>(.*?)</title>', html)
        if match:
            return match.group(1).replace(' - YouTube', '').strip()
    except Exception as e:
        print(f"Failed to fetch title: {e}")
    return "Unknown Title"

def extract_video_id(url: str) -> str:
    """Extracts the video ID from a YouTube URL."""
    url_data = urlparse.urlparse(url)
    query = urlparse.parse_qs(url_data.query)
    if 'v' in query:
        return query['v'][0]
    elif url_data.netloc == 'youtu.be':
        return url_data.path.lstrip('/')
    else:
        raise ValueError("Could not extract video ID from URL")

def get_video_transcript(url: str) -> str:
    """
    Fetches the transcript for the given YouTube video URL using yt-dlp.
    Returns the transcript as a single formatted string.
    """
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

        response = curl_requests.get(
            target_fmt['url'],
            impersonate="chrome",
            timeout=YOUTUBE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()

        text_lines = []
        for event in data.get('events', []):
            if 'segs' in event:
                text = "".join(seg.get('utf8', '') for seg in event['segs'])
                if text.strip():
                    text_lines.append(text.strip())

        return " ".join(text_lines).replace("\n", " ").strip()
