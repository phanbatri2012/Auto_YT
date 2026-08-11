import urllib.request
import urllib.error
import json
import hashlib
import os
import time
import sys

from auto_yt.paths import DATA_DIR

API_KEY_FILE = DATA_DIR / "genmax_api_key.txt"
BASE_URL = "https://api.genmax.io/v1"
POLL_INTERVAL_SECONDS = 5
MAX_WAIT_SECONDS = 40 * 60
HISTORY_PAGE_SIZE = 100
HISTORY_PAGES_TO_CHECK = 3
MAX_TTS_CHARS_PER_TASK = 9_000
BATCH_REQUEST_VERSION = 1
PROVIDER = "minimax"
MODEL_ID = "speech-2.8-hd"
LANGUAGE_CODE = "Vietnamese"
VOICE_SETTINGS = {
    "speed": 0.95,
    "pitch": 0,
    "vol": 1.0,
}


def _get_api_key() -> str:
    environment_key = os.environ.get("GENMAX_API_KEY", "").strip()
    if environment_key:
        return environment_key

    if API_KEY_FILE.exists():
        file_key = API_KEY_FILE.read_text(encoding="utf-8").strip()
        if file_key:
            return file_key

    raise ValueError(
        "Genmax API key is missing. Set GENMAX_API_KEY or save the key to "
        f"{API_KEY_FILE}."
    )


def _build_payload(text: str) -> dict:
    return {
        "text": text,
        "provider": PROVIDER,
        "model_id": MODEL_ID,
        "language_code": LANGUAGE_CODE,
        "voice_settings": VOICE_SETTINGS,
    }


def get_request_hash(text: str, voice_id: str) -> str:
    request_data = {
        "voice_id": voice_id,
        **_build_payload(text),
    }
    canonical_data = json.dumps(
        request_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_data.encode("utf-8")).hexdigest()


def split_text_for_tts(
    text: str,
    max_chars: int = MAX_TTS_CHARS_PER_TASK,
) -> list[str]:
    """Split long TTS input at natural boundaries below the provider limit."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    remaining = text.strip()
    chunks = []
    preferred_minimum = int(max_chars * 0.6)
    while len(remaining) > max_chars:
        window = remaining[:max_chars + 1]
        cut_at = -1
        for boundary in ("\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " "):
            candidate = window.rfind(boundary)
            if candidate >= preferred_minimum:
                cut_at = candidate + len(boundary)
                break
        if cut_at <= 0:
            cut_at = max_chars
        chunks.append(remaining[:cut_at].strip())
        remaining = remaining[cut_at:].lstrip()

    if remaining:
        chunks.append(remaining)
    return chunks


def get_generation_request_hash(text: str, voice_id: str) -> str:
    chunks = split_text_for_tts(text)
    if len(chunks) == 1:
        return get_request_hash(text, voice_id)
    batch_payload = {
        "batch_version": BATCH_REQUEST_VERSION,
        "chunk_limit": MAX_TTS_CHARS_PER_TASK,
        "voice_id": voice_id,
        "chunks": chunks,
        **_build_payload(""),
    }
    canonical_data = json.dumps(
        batch_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_data.encode("utf-8")).hexdigest()


def _request_json(url: str, method: str = "GET", payload: dict = None) -> dict:
    api_key = _get_api_key()
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("xi-api-key", api_key)
    req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
    if payload is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Genmax API error: {exc.code} - {error_body}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Không thể kết nối Genmax: {exc}") from exc


def submit_tts_task(text: str, voice_id: str) -> dict:
    print(">>> SUBMITTING AUDIO TASK (TTS)...", file=sys.stderr)
    url = f"{BASE_URL}/text-to-speech/{voice_id}"
    response = _request_json(url, method="POST", payload=_build_payload(text))
    task_id = response.get("id")
    if not task_id:
        raise RuntimeError("Genmax không trả về Task ID.")
    print(f"    -> TTS task submitted (Task ID: {task_id})", file=sys.stderr)
    return response


def get_tts_task(task_id: str) -> dict:
    return _request_json(f"{BASE_URL}/history/{task_id}")


def retry_tts_task(task_id: str) -> dict:
    response = _request_json(
        f"{BASE_URL}/history/{task_id}/retry",
        method="POST",
    )
    if not response.get("id"):
        raise RuntimeError("Genmax không trả về Task ID sau khi retry.")
    return response


def get_recent_tasks() -> list[dict]:
    tasks = []
    for page in range(HISTORY_PAGES_TO_CHECK):
        history = _request_json(
            f"{BASE_URL}/history?page_size={HISTORY_PAGE_SIZE}&page={page}"
        )
        tasks.extend(history.get("tasks", []))
        if not history.get("has_more"):
            break
    return tasks


def find_matching_tasks(texts: list[str], voice_id: str) -> dict[str, dict]:
    requested_texts = set(texts)
    matches_by_text: dict[str, list[dict]] = {}
    for task in get_recent_tasks():
        task_text = task.get("text")
        if (
            task_text in requested_texts
            and task.get("voice_id") == voice_id
            and task.get("provider") == PROVIDER
            and task.get("model_id") == MODEL_ID
        ):
            matches_by_text.setdefault(task_text, []).append(task)

    status_priority = {
        "completed": 0,
        "processing": 1,
        "pending": 2,
        "failed": 3,
    }
    return {
        text: min(
            matches,
            key=lambda task: (
                status_priority.get(task.get("status"), 4),
                task.get("created_at", ""),
            ),
        )
        for text, matches in matches_by_text.items()
    }


def find_matching_task(text: str, voice_id: str) -> dict | None:
    matches = find_matching_tasks([text], voice_id)
    return matches.get(text)


def generate_tts(text: str, voice_id: str) -> str:
    existing_task = find_matching_task(text, voice_id)
    task = existing_task or submit_tts_task(text, voice_id)
    task_id = task["id"]

    if task.get("status") == "completed":
        return task.get("result", {}).get("audio_url")
    if task.get("status") == "failed":
        raise RuntimeError(
            f"Task Genmax trước đó đã thất bại: {task.get('error') or 'Unknown error'}"
        )

    print(f">>> WAITING FOR AUDIO (Task ID: {task_id})...", file=sys.stderr)
    max_retries = MAX_WAIT_SECONDS // POLL_INTERVAL_SECONDS
    for attempt in range(max_retries):
        if attempt % 6 == 0:
            elapsed = attempt * POLL_INTERVAL_SECONDS
            print(
                f"    -> TTS đang xử lý... ({elapsed}s đã chờ, tối đa 40 phút)",
                file=sys.stderr,
            )

        task = get_tts_task(task_id)
        status = task.get("status")
        if status == "completed":
            audio_url = task.get("result", {}).get("audio_url")
            if not audio_url:
                raise RuntimeError("Task Genmax hoàn thành nhưng thiếu URL audio.")
            return audio_url
        if status == "failed":
            raise RuntimeError(
                f"Tạo TTS thất bại: {task.get('error') or 'Unknown error'}"
            )
        time.sleep(POLL_INTERVAL_SECONDS)

    raise RuntimeError(
        f"Quá thời gian chờ Genmax xử lý audio. Task ID: {task_id}"
    )
