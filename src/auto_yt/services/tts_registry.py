from __future__ import annotations

import hashlib
import json
import uuid
import wave
from abc import ABC, abstractmethod
from pathlib import Path

from auto_yt.paths import AUDIO_DIR
from auto_yt.services import omnivoice_client
from auto_yt.services import tts_service as genmax_service
from auto_yt.services import voice_config


OMNIVOICE_TASK_PREFIX = "omnivoice:"
PREVIEW_AUDIO_DIR = AUDIO_DIR / "previews"


class TTSProvider(ABC):
    id: str

    @abstractmethod
    def health(self, *, test_connection: bool = False) -> dict:
        raise NotImplementedError

    @abstractmethod
    def validate_voice(self, voice: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def submit(self, text: str, voice: dict, request_hash: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def poll(self, task_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, task_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def recover(self, task_id: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    def list_remote_voices(self) -> dict:
        raise NotImplementedError


class GenmaxProvider(TTSProvider):
    id = voice_config.GENMAX_PROVIDER_ID

    def health(self, *, test_connection: bool = False) -> dict:
        configured = genmax_service.has_api_key()
        result = {
            "ok": configured,
            "state": "ready" if configured else "unconfigured",
            "configured": configured,
        }
        if configured and test_connection:
            genmax_service.get_recent_tasks()
            result["connection_tested"] = True
        return result

    def validate_voice(self, voice: dict) -> None:
        if not voice.get("provider_voice_id"):
            raise ValueError("Giọng Genmax thiếu Voice ID.")
        if not genmax_service.has_api_key():
            raise ValueError("Chưa cấu hình Genmax API key.")

    def submit(self, text: str, voice: dict, request_hash: str) -> dict:
        del request_hash
        return genmax_service.submit_tts_task(
            text,
            voice["provider_voice_id"],
            voice.get("config") or {},
        )

    def poll(self, task_id: str) -> dict:
        return genmax_service.get_tts_task(task_id)

    def cancel(self, task_id: str) -> dict:
        del task_id
        raise RuntimeError("Genmax không hỗ trợ hủy task từ API hiện tại.")

    def recover(self, task_id: str) -> dict:
        return genmax_service.retry_tts_task(task_id)

    def list_remote_voices(self) -> dict:
        return {"profiles": [], "samples": []}


class OmniVoiceProvider(TTSProvider):
    id = voice_config.OMNIVOICE_PROVIDER_ID

    @staticmethod
    def _base_url() -> str:
        provider = voice_config.get_provider(voice_config.OMNIVOICE_PROVIDER_ID)
        return str((provider.get("config") or {}).get("base_url") or omnivoice_client.DEFAULT_BASE_URL)

    @staticmethod
    def _raw_task_id(task_id: str) -> str:
        return str(task_id or "").removeprefix(OMNIVOICE_TASK_PREFIX)

    @staticmethod
    def _normalize_task(task: dict) -> dict:
        raw_id = str(task.get("id") or "")
        result = {}
        if task.get("status") == "completed":
            result = {
                "audio_url": f"omnivoice://{raw_id}",
                "duration_seconds": task.get("duration_seconds"),
            }
        return {
            **task,
            "id": f"{OMNIVOICE_TASK_PREFIX}{raw_id}",
            "result": result,
        }

    def health(self, *, test_connection: bool = False) -> dict:
        return omnivoice_client.health(
            start_if_needed=test_connection,
            base_url=self._base_url(),
        )

    def validate_voice(self, voice: dict) -> None:
        provider_voice_id = str(voice.get("provider_voice_id") or "")
        if not provider_voice_id:
            raise ValueError("Giọng OmniVoice thiếu Profile ID.")
        remote = self.list_remote_voices()
        if provider_voice_id not in {
            profile.get("id") for profile in remote.get("profiles", [])
        }:
            raise ValueError("Profile giọng OmniVoice chưa sẵn sàng.")

    def submit(self, text: str, voice: dict, request_hash: str) -> dict:
        task = omnivoice_client.submit_job(
            text,
            voice["provider_voice_id"],
            request_hash,
            voice.get("config") or {},
            base_url=self._base_url(),
        )
        return self._normalize_task(task)

    def poll(self, task_id: str) -> dict:
        task = omnivoice_client.get_job(
            self._raw_task_id(task_id),
            base_url=self._base_url(),
        )
        return self._normalize_task(task)

    def cancel(self, task_id: str) -> dict:
        task = omnivoice_client.cancel_job(
            self._raw_task_id(task_id),
            base_url=self._base_url(),
        )
        return self._normalize_task(task)

    def recover(self, task_id: str) -> dict:
        task = omnivoice_client.recover_job(
            self._raw_task_id(task_id),
            base_url=self._base_url(),
        )
        return self._normalize_task(task)

    def list_remote_voices(self) -> dict:
        return omnivoice_client.list_voices(base_url=self._base_url())

    def download_audio(self, task_id: str, destination: Path) -> Path:
        return omnivoice_client.download_audio(
            self._raw_task_id(task_id),
            destination,
            base_url=self._base_url(),
        )


PROVIDERS: dict[str, TTSProvider] = {
    GenmaxProvider.id: GenmaxProvider(),
    OmniVoiceProvider.id: OmniVoiceProvider(),
}


def get_provider(provider_id: str) -> TTSProvider:
    try:
        provider = PROVIDERS[str(provider_id or "").strip().casefold()]
    except KeyError as exc:
        raise ValueError("Nhà cung cấp TTS chưa có adapter.") from exc
    configured = voice_config.get_provider(provider.id)
    if not configured.get("enabled", True):
        raise ValueError(f"Nhà cung cấp {configured['display_name']} đang bị tắt.")
    return provider


def get_voice_context(voice_id: str = "", *, require_active: bool = True) -> tuple[dict, TTSProvider]:
    voice = voice_config.get_voice(voice_id, include_inactive=not require_active)
    if require_active and voice.get("status") != "active":
        raise ValueError("Giọng đọc đã chọn hiện không hoạt động.")
    return voice, get_provider(voice["provider_id"])


def _voice_for_hash(voice_id: str, voice_snapshot: dict | None = None) -> dict:
    if voice_snapshot:
        return {
            "id": voice_snapshot.get("voice_id") or voice_id,
            "name": voice_snapshot.get("voice_name") or "Giọng đã lưu",
            "provider_id": voice_snapshot.get("provider_id") or "genmax",
            "provider_voice_id": voice_snapshot.get("provider_voice_id") or voice_id,
            "revision": int(voice_snapshot.get("voice_revision") or 1),
            "config": voice_snapshot.get("config") or {},
            "status": "active",
        }
    try:
        return voice_config.get_voice(voice_id)
    except ValueError:
        # Legacy database rows and isolated tests may reference a Genmax voice
        # that is no longer present in the editable catalog.
        return {
            "id": voice_id,
            "name": "Giọng đã lưu",
            "provider_id": voice_config.GENMAX_PROVIDER_ID,
            "provider_voice_id": voice_id,
            "revision": 1,
            "config": {},
        }


def get_request_hash(
    text: str,
    voice_id: str,
    voice_snapshot: dict | None = None,
) -> str:
    voice = _voice_for_hash(voice_id, voice_snapshot)
    request_data = {
        "script_hash": hashlib.sha256(str(text or "").encode("utf-8")).hexdigest(),
        "provider_id": voice["provider_id"],
        "provider_voice_id": voice["provider_voice_id"],
        "voice_revision": int(voice.get("revision") or 1),
        "settings": voice.get("config") or {},
    }
    canonical = json.dumps(
        request_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def split_text_for_tts(
    text: str,
    max_chars: int | None = None,
    voice_id: str = "",
    voice_snapshot: dict | None = None,
) -> list[str]:
    voice = _voice_for_hash(
        voice_id or voice_config.DEFAULT_VOICE_ID,
        voice_snapshot,
    )
    if voice["provider_id"] == voice_config.OMNIVOICE_PROVIDER_ID:
        normalized = str(text or "").strip()
        if not normalized:
            return []
        if len(normalized) > genmax_service.MAX_TTS_TOTAL_CHARS:
            raise ValueError("Kịch bản vượt giới hạn an toàn cho một lượt tạo audio.")
        return [normalized]
    return genmax_service.split_text_for_tts(
        text,
        max_chars or genmax_service.MAX_TTS_CHARS_PER_TASK,
    )


def get_generation_request_hash(
    text: str,
    voice_id: str,
    voice_snapshot: dict | None = None,
) -> str:
    return get_request_hash(text, voice_id, voice_snapshot)


def submit_tts_task(
    text: str,
    voice_id: str,
    voice_snapshot: dict | None = None,
) -> dict:
    if voice_snapshot:
        voice = _voice_for_hash(voice_id, voice_snapshot)
        provider = get_provider(voice["provider_id"])
    else:
        voice, provider = get_voice_context(voice_id)
    provider.validate_voice(voice)
    return provider.submit(
        text,
        voice,
        get_request_hash(text, voice_id, voice_snapshot),
    )


def _provider_for_task(task_id: str, provider_id: str = "") -> TTSProvider:
    if provider_id:
        return get_provider(provider_id)
    if str(task_id or "").startswith(OMNIVOICE_TASK_PREFIX):
        return get_provider(voice_config.OMNIVOICE_PROVIDER_ID)
    return get_provider(voice_config.GENMAX_PROVIDER_ID)


def get_tts_task(task_id: str, provider_id: str = "") -> dict:
    return _provider_for_task(task_id, provider_id).poll(task_id)


def retry_tts_task(task_id: str, provider_id: str = "") -> dict:
    return _provider_for_task(task_id, provider_id).recover(task_id)


def cancel_tts_task(task_id: str, provider_id: str = "") -> dict:
    return _provider_for_task(task_id, provider_id).cancel(task_id)


def find_matching_tasks(
    texts: list[str],
    voice_id: str,
    voice_snapshot: dict | None = None,
) -> dict[str, dict]:
    voice = _voice_for_hash(voice_id, voice_snapshot)
    if voice["provider_id"] != voice_config.GENMAX_PROVIDER_ID:
        return {}
    if voice.get("config"):
        return {}
    return genmax_service.find_matching_tasks(texts, voice["provider_voice_id"])


def find_matching_task(
    text: str,
    voice_id: str,
    voice_snapshot: dict | None = None,
) -> dict | None:
    return find_matching_tasks([text], voice_id, voice_snapshot).get(text)


def materialize_audio(task: dict, video_id: int, request_hash: str) -> tuple[str, float | None]:
    task_id = str(task.get("id") or "")
    provider = _provider_for_task(task_id)
    if isinstance(provider, OmniVoiceProvider):
        destination = AUDIO_DIR / f"video_{video_id}_{request_hash[:16]}.wav"
        provider.download_audio(task_id, destination)
        with wave.open(str(destination), "rb") as source:
            duration = source.getnframes() / source.getframerate()
        # Audio is served by the Auto_YT backend, while the UI normally runs
        # on Vite's port.  Keep the URL absolute like the legacy Genmax path so
        # browsers never try to fetch the WAV from port 5173.
        return f"http://127.0.0.1:8080/api/audio/{destination.name}", duration
    return str((task.get("result") or {}).get("audio_url") or ""), None


def discard_materialized_audio(video_id: int, request_hash: str) -> None:
    """Remove only the deterministic local file for a rejected OmniVoice result."""
    destination = AUDIO_DIR / f"video_{video_id}_{request_hash[:16]}.wav"
    destination.unlink(missing_ok=True)


def materialize_preview_audio(task: dict, preview_id: str) -> tuple[str, float]:
    task_id = str(task.get("id") or "")
    provider = _provider_for_task(task_id)
    if not isinstance(provider, OmniVoiceProvider):
        raise ValueError("Bản nghe thử này không thuộc OmniVoice.")
    normalized_preview_id = str(uuid.UUID(str(preview_id)))
    PREVIEW_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    destination = PREVIEW_AUDIO_DIR / f"{normalized_preview_id}.wav"
    try:
        provider.download_audio(task_id, destination)
        with wave.open(str(destination), "rb") as source:
            duration = source.getnframes() / source.getframerate()
        if duration <= 0:
            raise ValueError("OmniVoice trả về bản nghe thử rỗng.")
        return destination.name, duration
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def discard_preview_audio(filename: str) -> None:
    candidate = Path(str(filename or ""))
    if candidate.name != str(filename or "") or candidate.suffix.lower() != ".wav":
        return
    try:
        uuid.UUID(candidate.stem)
    except (ValueError, AttributeError):
        return
    (PREVIEW_AUDIO_DIR / candidate.name).unlink(missing_ok=True)


def migrate_api_key_storage() -> None:
    genmax_service.migrate_api_key_storage()


def ensure_omnivoice_worker_running() -> None:
    provider = voice_config.get_provider(voice_config.OMNIVOICE_PROVIDER_ID)
    if not provider.get("enabled", True):
        return
    base_url = str(
        (provider.get("config") or {}).get("base_url")
        or omnivoice_client.DEFAULT_BASE_URL
    )
    omnivoice_client.ensure_worker_running(base_url=base_url)


MAX_TTS_CHARS_PER_TASK = genmax_service.MAX_TTS_CHARS_PER_TASK
MAX_TTS_TOTAL_CHARS = genmax_service.MAX_TTS_TOTAL_CHARS
