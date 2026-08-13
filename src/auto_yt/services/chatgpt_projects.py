import json
import os
from urllib.parse import urlparse

from auto_yt.paths import PROMPTS_PATH


CHATGPT_PROJECT_URL_ENV = "CHATGPT_PROJECT_URL"
DEFAULT_CHATGPT_PROJECT_URL = (
    "https://chatgpt.com/g/"
    "g-p-6a1f9204f2d88191b39b64eb7f2dbb97-dd-vn2-phan-tich/project"
)


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
    return normalized


def validate_prompt_projects(data: dict) -> dict:
    normalized = add_project_defaults(data)
    for version in normalized.get("versions", {}).values():
        version["project_url"] = validate_project_url(version["project_url"])
    return normalized
