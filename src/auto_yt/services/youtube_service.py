from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
import urllib.parse as urlparse
import urllib.request
import re
import truststore


def _configure_system_trust_store() -> None:
    # Honor managed OS certificate authorities while keeping TLS verification enabled.
    truststore.inject_into_ssl()


_configure_system_trust_store()

def get_video_title(url: str) -> str:
    """Fetches the video title from YouTube URL."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        html = urllib.request.urlopen(req).read().decode('utf-8')
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
    Fetches the transcript for the given YouTube video URL.
    Returns the transcript as a single formatted string.
    """
    video_id = extract_video_id(url)
    
    # Fetch transcript (tries Vietnamese and English)
    api = YouTubeTranscriptApi()
    transcript_list = api.list(video_id)
    transcript_obj = transcript_list.find_transcript(['vi', 'en'])
    transcript = transcript_obj.fetch()
    
    # Format into text
    formatter = TextFormatter()
    text_formatted = formatter.format_transcript(transcript)
    
    # Clean up line breaks for ChatGPT
    text_cleaned = " ".join(text_formatted.split())
    return text_cleaned
