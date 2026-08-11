import json
import uuid

from auto_yt.paths import DATA_DIR


DEFAULT_VOICE_ID = "e1d9617c-045c-4072-8d17-9be0ec113723"
DEFAULT_VOICE_NAME = "Giọng mặc định hiện tại"
VOICE_CONFIG_PATH = DATA_DIR / "voices.json"
MAX_VOICE_OPTIONS = 50


def _default_config() -> dict:
    return {
        "active_voice_id": DEFAULT_VOICE_ID,
        "voices": [
            {
                "id": DEFAULT_VOICE_ID,
                "name": DEFAULT_VOICE_NAME,
            }
        ],
    }


def validate_voice_config(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Voice configuration must be an object.")

    raw_voices = data.get("voices")
    if not isinstance(raw_voices, list) or not raw_voices:
        raise ValueError("Phải có ít nhất một giọng đọc.")
    if len(raw_voices) > MAX_VOICE_OPTIONS:
        raise ValueError(f"Chỉ được cấu hình tối đa {MAX_VOICE_OPTIONS} giọng đọc.")

    voices = []
    seen_ids = set()
    seen_names = set()
    for raw_voice in raw_voices:
        if not isinstance(raw_voice, dict):
            raise ValueError("Thông tin giọng đọc không hợp lệ.")
        voice_id = str(raw_voice.get("id", "")).strip()
        voice_name = str(raw_voice.get("name", "")).strip()
        if not voice_name:
            raise ValueError("Tên giọng đọc không được để trống.")
        try:
            voice_id = str(uuid.UUID(voice_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"Voice ID của '{voice_name}' không đúng định dạng UUID."
            ) from exc
        normalized_name = voice_name.casefold()
        if voice_id in seen_ids:
            raise ValueError("Voice ID không được trùng nhau.")
        if normalized_name in seen_names:
            raise ValueError("Tên giọng đọc không được trùng nhau.")
        seen_ids.add(voice_id)
        seen_names.add(normalized_name)
        voices.append({"id": voice_id, "name": voice_name})

    active_voice_id = str(data.get("active_voice_id", "")).strip()
    if active_voice_id not in seen_ids:
        raise ValueError("Giọng mặc định phải nằm trong danh sách giọng đọc.")
    return {
        "active_voice_id": active_voice_id,
        "voices": voices,
    }


def load_voice_config() -> dict:
    if not VOICE_CONFIG_PATH.exists():
        config = _default_config()
        save_voice_config(config)
        return config
    try:
        return validate_voice_config(
            json.loads(VOICE_CONFIG_PATH.read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return _default_config()


def save_voice_config(data: dict) -> dict:
    config = validate_voice_config(data)
    VOICE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOICE_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return config


def get_voice(voice_id: str = "") -> dict:
    config = load_voice_config()
    selected_id = voice_id.strip() if voice_id else config["active_voice_id"]
    for voice in config["voices"]:
        if voice["id"] == selected_id:
            return voice
    raise ValueError("Giọng đọc đã chọn không có trong cấu hình.")
