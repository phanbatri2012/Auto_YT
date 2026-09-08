import json
import os
from urllib.parse import urlparse

from auto_yt.paths import PROMPTS_PATH


CHATGPT_PROJECT_URL_ENV = "CHATGPT_PROJECT_URL"
DEFAULT_CHATGPT_PROJECT_URL = (
    "https://chatgpt.com/g/"
    "g-p-6a1f9204f2d88191b39b64eb7f2dbb97-dd-vn2-phan-tich/project"
)
PROMPT_PIPELINE_ENV = "PROMPT_PIPELINE_JSON"
DEFAULT_PROMPT_PIPELINE = {
    "metadata": True,
    "chapters": True,
    "thumbnail_with_text": True,
    "thumbnail_without_text": True,
    "audio": True,
}


def normalize_prompt_pipeline(value: object) -> dict[str, bool]:
    """Return a complete, dependency-safe pipeline for legacy config data."""
    pipeline = value if isinstance(value, dict) else {}
    return {
        key: pipeline.get(key) if isinstance(pipeline.get(key), bool) else default
        for key, default in DEFAULT_PROMPT_PIPELINE.items()
    }


def validate_prompt_pipeline(value: object) -> dict[str, bool]:
    """Validate pipeline writes while still filling keys added in newer releases."""
    if value is None:
        return dict(DEFAULT_PROMPT_PIPELINE)
    if not isinstance(value, dict):
        raise ValueError("Cấu hình pipeline của bộ prompt không hợp lệ.")
    unknown_keys = set(value) - set(DEFAULT_PROMPT_PIPELINE)
    if unknown_keys:
        raise ValueError(
            "Pipeline chứa bước không được hỗ trợ: "
            + ", ".join(sorted(unknown_keys))
        )
    invalid_keys = [key for key, enabled in value.items() if not isinstance(enabled, bool)]
    if invalid_keys:
        raise ValueError(
            "Trạng thái bước pipeline phải là bật hoặc tắt: "
            + ", ".join(sorted(invalid_keys))
        )
    return {
        key: value.get(key, default)
        for key, default in DEFAULT_PROMPT_PIPELINE.items()
    }


def validate_project_url(value: str) -> str:
    project_url = str(value or "").strip().rstrip("/")
    parsed_url = urlparse(project_url)
    path_parts = parsed_url.path.strip("/").split("/")
    is_project_url = (
        parsed_url.scheme == "https"
        and parsed_url.netloc == "chatgpt.com"
        and len(path_parts) == 3
        and path_parts[0] == "g"
        and path_parts[1].startswith("g-p-")
        and path_parts[2] == "project"
        and not parsed_url.query
        and not parsed_url.fragment
    )
    if not is_project_url:
        raise ValueError("Phải nhập đúng URL ChatGPT Project.")
    return project_url


def get_project_url(prompt_version: str = "") -> str:
    selected_version = prompt_version.strip() or os.environ.get(
        "PROMPT_VERSION", ""
    ).strip()
    if PROMPTS_PATH.exists():
        try:
            data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
            versions = data.get("versions", {})
            version_key = selected_version or data.get("active_version", "default")
            version = versions.get(version_key, {})
            configured_url = str(version.get("project_url", "")).strip()
            if configured_url:
                return validate_project_url(configured_url)
        except (OSError, json.JSONDecodeError):
            pass

    environment_url = os.environ.get(CHATGPT_PROJECT_URL_ENV, "").strip()
    if environment_url:
        return validate_project_url(environment_url)

    return DEFAULT_CHATGPT_PROJECT_URL


def add_project_defaults(data: dict) -> dict:
    normalized = json.loads(json.dumps(data))
    for version in normalized.get("versions", {}).values():
        version.setdefault("project_url", DEFAULT_CHATGPT_PROJECT_URL)
        version.setdefault("default_voice_id", "")
        version["pipeline"] = normalize_prompt_pipeline(version.get("pipeline"))
    return normalized


def validate_prompt_projects(data: dict) -> dict:
    normalized = add_project_defaults(data)
    source_versions = data.get("versions", {}) if isinstance(data, dict) else {}
    for version_id, version in normalized.get("versions", {}).items():
        version["project_url"] = validate_project_url(version["project_url"])
        source_version = source_versions.get(version_id, {})
        source_pipeline = (
            source_version.get("pipeline")
            if isinstance(source_version, dict)
            else None
        )
        version["pipeline"] = validate_prompt_pipeline(source_pipeline)
    return normalized
