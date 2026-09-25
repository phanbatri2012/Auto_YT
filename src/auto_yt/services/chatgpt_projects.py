import json
import os
import re
from urllib.parse import urlparse

from auto_yt.paths import PROMPTS_PATH

CHATGPT_PROJECT_URL_ENV = "CHATGPT_PROJECT_URL"
DEFAULT_CHATGPT_BOOTSTRAP_URL = "https://chatgpt.com/"
DEFAULT_CHATGPT_PROJECT_URL = (
    "https://chatgpt.com/g/"
    "g-p-6a1f9204f2d88191b39b64eb7f2dbb97-dd-vn2-phan-tich/project"
)
PROMPT_PIPELINE_ENV = "PROMPT_PIPELINE_JSON"
DEFAULT_PROMPT_PIPELINE = {
    "title": True,
    "slug": True,
    "description": True,
    "tags": True,
    "pinned_comment": True,
    "quiz": True,
    "chapters": True,
    "thumbnail_with_text": True,
    "thumbnail_without_text": True,
    "audio": True,
    "video_render": False,
    "youtube_upload": False,
    "youtube_schedule": False,
}

THUMBNAIL_VARIANTS = {"with_text", "without_text"}
DEFAULT_IMAGE_MODEL = "nano_banana_pro"
GOOGLE_FLOW_MODELS = {
    "nano_banana_pro": {
        "id": "nano_banana_pro",
        "name": "Nano Banana Pro (Imagen 3 Fast)",
        "provider": "google_flow",
        "credit_tier": "low",
        "credit_label": "🟢 Thấp nhất (~1 credit/ảnh)",
        "speed": "⚡ 3–5s",
        "description": "⭐ Khuyên dùng mặc định để tiết kiệm credit tối đa. Tốc độ sinh nhanh, màu sắc chân thực, phù hợp tạo 30–50 cảnh visual cho video dài.",
        "is_default": True,
    },
    "imagen_3_standard": {
        "id": "imagen_3_standard",
        "name": "Google Imagen 3 (High-Fidelity)",
        "provider": "google_flow",
        "credit_tier": "medium",
        "credit_label": "🟡 Trung bình (~2–3 credit/ảnh)",
        "speed": "⏳ 8–12s",
        "description": "Chất lượng siêu thực cao cấp. Tái tạo biểu cảm nhân vật, bàn tay, ánh sáng và chi tiết da xuất sắc; bám sát prompt phức tạp.",
        "is_default": False,
    },
    "imagen_3_photoreal": {
        "id": "imagen_3_photoreal",
        "name": "Google Imagen 3 (Photorealistic / 35mm)",
        "provider": "google_flow",
        "credit_tier": "standard",
        "credit_label": "🟡 Tiêu chuẩn (~2 credit/ảnh)",
        "speed": "⏳ 6–10s",
        "description": "Phong cách ảnh tư liệu / Điện ảnh thực tế. Tối ưu đặc biệt cho video kể chuyện, phim tài liệu, hạn chế cảm giác bóng bẩy 3D/CGI.",
        "is_default": False,
    },
    "google_veo_intro": {
        "id": "google_veo_intro",
        "name": "Google Veo (Intro Video Generator)",
        "provider": "google_flow",
        "credit_tier": "high",
        "credit_label": "🔴 Cao (~10–20 credit/clip)",
        "speed": "🐌 30–60s",
        "description": "Sinh video chuyển động AI 4–8 giây cho cảnh mở đầu để giữ chân người xem. (Chỉ nên bật cho cảnh đầu tiên để bảo toàn credit).",
        "is_default": False,
    },
}
DEFAULT_IMAGE_GENERATION_SETTINGS = {
    "provider": "google_flow",
    "model": DEFAULT_IMAGE_MODEL,
    "style_prompt": "",
    "avoid_prompt": "",
    "negative_prompt": "",  # alias for avoid_prompt, kept for frontend compatibility
    "density": 30,
    "outputs_per_scene": 1,
    "thumbnail_variant": "without_text",
    "enable_intro_video": True,
    "intro_scene_target_seconds": 8.0,
    "intro_crop_watermark": True,
}
DEFAULT_PUBLISHING_SETTINGS = {
    "category_id": "",
    "language": "vi",
    "made_for_kids": None,
    "notify_subscribers": True,
    "contains_synthetic_media": True,
    "description_template": "{description}\n\n{chapters}\n\n{tags}",
}


def _pipeline_dependencies(thumbnail_variant: str) -> dict[str, tuple[str, ...]]:
    thumbnail_step = (
        "thumbnail_with_text"
        if thumbnail_variant == "with_text"
        else "thumbnail_without_text"
    )
    return {
        "video_render": ("audio",),
        "youtube_upload": ("video_render", "title", "description", "tags", thumbnail_step),
        "youtube_schedule": ("youtube_upload",),
    }


def resolve_prompt_pipeline_dependencies(
    pipeline: dict[str, bool],
    thumbnail_variant: str = "without_text",
) -> tuple[dict[str, bool], list[str]]:
    """Enable every prerequisite required by the selected terminal stages."""
    variant = (
        thumbnail_variant if thumbnail_variant in THUMBNAIL_VARIANTS else "without_text"
    )
    resolved = dict(pipeline)
    auto_enabled: list[str] = []
    dependencies = _pipeline_dependencies(variant)
    changed = True
    while changed:
        changed = False
        for step, required_steps in dependencies.items():
            if not resolved.get(step):
                continue
            for required_step in required_steps:
                if resolved.get(required_step):
                    continue
                resolved[required_step] = True
                auto_enabled.append(required_step)
                changed = True
    return resolved, list(dict.fromkeys(auto_enabled))


def normalize_prompt_pipeline(
    value: object,
    thumbnail_variant: str | None = None,
) -> dict[str, bool]:
    """Return a complete, dependency-safe pipeline for legacy config data."""
    pipeline = value if isinstance(value, dict) else {}
    legacy_metadata = pipeline.get("metadata")
    normalized = {}
    for key, default in DEFAULT_PROMPT_PIPELINE.items():
        if isinstance(pipeline.get(key), bool):
            normalized[key] = pipeline[key]
        elif (
            key in {"title", "slug", "description", "tags", "pinned_comment", "quiz"}
            and isinstance(legacy_metadata, bool)
        ):
            normalized[key] = legacy_metadata
        else:
            normalized[key] = default

    resolved_variant = thumbnail_variant
    if resolved_variant is None and normalized.get("youtube_upload"):
        with_text = normalized.get("thumbnail_with_text", False)
        without_text = normalized.get("thumbnail_without_text", False)
        if with_text != without_text:
            resolved_variant = "with_text" if with_text else "without_text"
    return resolve_prompt_pipeline_dependencies(
        normalized, resolved_variant or "without_text"
    )[0]


def validate_prompt_pipeline(
    value: object,
    thumbnail_variant: str | None = None,
) -> dict[str, bool]:
    """Validate pipeline writes while still filling keys added in newer releases."""
    if value is None:
        return dict(DEFAULT_PROMPT_PIPELINE)
    if not isinstance(value, dict):
        raise ValueError("Cấu hình pipeline của bộ prompt không hợp lệ.")
    cleaned_value = {k: v for k, v in value.items() if v is not None}
    allowed_keys = set(DEFAULT_PROMPT_PIPELINE) | {"metadata"}
    unknown_keys = set(cleaned_value) - allowed_keys
    if unknown_keys:
        raise ValueError(
            "Pipeline chứa bước không được hỗ trợ: " + ", ".join(sorted(unknown_keys))
        )
    invalid_keys = [
        key for key, enabled in cleaned_value.items() if not isinstance(enabled, bool)
    ]
    if invalid_keys:
        raise ValueError(
            "Trạng thái bước pipeline phải là bật hoặc tắt: "
            + ", ".join(sorted(invalid_keys))
        )
    return normalize_prompt_pipeline(cleaned_value, thumbnail_variant)


def normalize_image_generation_settings(value: object) -> dict:
    settings = value if isinstance(value, dict) else {}
    normalized = dict(DEFAULT_IMAGE_GENERATION_SETTINGS)

    # Model and Provider normalization
    model = str(settings.get("model") or "").strip()
    if model in GOOGLE_FLOW_MODELS:
        normalized["model"] = model
    else:
        normalized["model"] = DEFAULT_IMAGE_MODEL

    provider = str(settings.get("provider") or "").strip()
    normalized["provider"] = provider if provider else "google_flow"

    # Both negative_prompt (used by frontend) and avoid_prompt (backend alias) are supported.
    avoid = str(settings.get("avoid_prompt") or "").strip()
    neg = str(settings.get("negative_prompt") or "").strip()
    avoid_value = neg if neg else avoid

    normalized["style_prompt"] = str(settings.get("style_prompt") or "").strip()
    normalized["avoid_prompt"] = avoid_value
    normalized["negative_prompt"] = avoid_value  # mirror for frontend compatibility

    density = settings.get("density", 30)
    normalized["density"] = density if density in {25, 30, 35} else 30

    normalized["thumbnail_variant"] = (
        settings.get("thumbnail_variant")
        if settings.get("thumbnail_variant") in THUMBNAIL_VARIANTS
        else "without_text"
    )
    normalized["enable_intro_video"] = bool(settings.get("enable_intro_video", True))
    try:
        normalized["intro_scene_target_seconds"] = max(4.0, min(15.0, float(settings.get("intro_scene_target_seconds", 8.0) or 8.0)))
    except (TypeError, ValueError):
        normalized["intro_scene_target_seconds"] = 8.0
    normalized["intro_crop_watermark"] = bool(settings.get("intro_crop_watermark", True))
    return normalized


def validate_image_generation_settings(value: object) -> dict:
    if value is not None and not isinstance(value, dict):
        raise ValueError("Cấu hình tạo ảnh của bộ prompt không hợp lệ.")
    settings = value or {}
    # Allow unknown keys from old ComfyUI snapshots during migration read; only validate writable keys
    allowed_write_keys = set(DEFAULT_IMAGE_GENERATION_SETTINGS) | {
        "negative_prompt",  # migrated -> avoid_prompt
        "workflow_profile_id",  # legacy, silently ignored
        "reference_workflow_profile_id",  # legacy, silently ignored
        "seed_mode",  # legacy, silently ignored
        "scene_duration_min_seconds",  # legacy, silently ignored
        "scene_duration_target_seconds",  # legacy, silently ignored
        "scene_duration_max_seconds",  # legacy, silently ignored
    }
    unknown = set(settings) - allowed_write_keys
    if unknown:
        raise ValueError(
            "Cấu hình tạo ảnh chứa trường không được hỗ trợ: "
            + ", ".join(sorted(unknown))
        )
    if settings.get("thumbnail_variant", "without_text") not in THUMBNAIL_VARIANTS:
        raise ValueError("Loại thumbnail upload không được hỗ trợ.")
    return normalize_image_generation_settings(settings)



def normalize_publishing_settings(value: object) -> dict:
    settings = value if isinstance(value, dict) else {}
    normalized = dict(DEFAULT_PUBLISHING_SETTINGS)
    normalized["category_id"] = str(settings.get("category_id") or "").strip()
    normalized["language"] = str(settings.get("language") or "vi").strip() or "vi"
    normalized["made_for_kids"] = (
        settings.get("made_for_kids")
        if isinstance(settings.get("made_for_kids"), bool)
        else None
    )
    normalized["notify_subscribers"] = (
        settings.get("notify_subscribers")
        if isinstance(settings.get("notify_subscribers"), bool)
        else True
    )
    # All videos produced by this pipeline use synthetic scene images.
    normalized["contains_synthetic_media"] = True
    template_val = settings.get("description_template")
    if isinstance(template_val, str):
        normalized["description_template"] = template_val
    else:
        normalized["description_template"] = DEFAULT_PUBLISHING_SETTINGS["description_template"]
    return normalized


def validate_publishing_settings(value: object) -> dict:
    if value is not None and not isinstance(value, dict):
        raise ValueError("Cấu hình đăng YouTube của bộ prompt không hợp lệ.")
    settings = value or {}
    unknown = set(settings) - set(DEFAULT_PUBLISHING_SETTINGS)
    if unknown:
        raise ValueError(
            "Cấu hình đăng YouTube chứa trường không được hỗ trợ: "
            + ", ".join(sorted(unknown))
        )
    category_id = str(settings.get("category_id") or "").strip()
    if category_id and (not category_id.isdigit() or len(category_id) > 10):
        raise ValueError("YouTube Category ID không hợp lệ.")
    language = str(settings.get("language") or "vi").strip()
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language):
        raise ValueError("Mã ngôn ngữ YouTube không hợp lệ.")
    made_for_kids = settings.get("made_for_kids")
    if made_for_kids is not None and not isinstance(made_for_kids, bool):
        raise ValueError("Lựa chọn dành cho trẻ em phải là Có hoặc Không.")
    notify_subscribers = settings.get("notify_subscribers", True)
    if not isinstance(notify_subscribers, bool):
        raise ValueError("Thiết lập thông báo người đăng ký không hợp lệ.")
    desc_template = settings.get("description_template")
    if desc_template is not None and not isinstance(desc_template, str):
        raise ValueError("Mẫu mô tả YouTube phải là chuỗi ký tự.")
    if isinstance(desc_template, str) and len(desc_template) > 5000:
        raise ValueError("Mẫu mô tả YouTube không được vượt quá 5000 ký tự.")
    return normalize_publishing_settings(settings)


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
    selected_version = (
        prompt_version.strip() or os.environ.get("PROMPT_VERSION", "").strip()
    )
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
        version.setdefault("default_youtube_channel_id", "")
        version["image_generation_settings"] = normalize_image_generation_settings(
            version.get("image_generation_settings")
        )
        version["publishing_settings"] = normalize_publishing_settings(
            version.get("publishing_settings")
        )
        version["pipeline"] = normalize_prompt_pipeline(
            version.get("pipeline"),
            version["image_generation_settings"]["thumbnail_variant"],
        )
    return normalized


def validate_prompt_projects(data: dict) -> dict:
    normalized = add_project_defaults(data)
    source_versions = data.get("versions", {}) if isinstance(data, dict) else {}
    for version_id, version in normalized.get("versions", {}).items():
        version["project_url"] = validate_project_url(version["project_url"])
        default_channel_id = str(
            version.get("default_youtube_channel_id", "") or ""
        ).strip()
        if len(default_channel_id) > 100:
            raise ValueError(f"YouTube Channel ID của bộ prompt {version_id} quá dài.")
        version["default_youtube_channel_id"] = default_channel_id
        source_version = source_versions.get(version_id, {})
        source_pipeline = (
            source_version.get("pipeline") if isinstance(source_version, dict) else None
        )
        source_image_settings = (
            source_version.get("image_generation_settings")
            if isinstance(source_version, dict)
            else None
        )
        source_publishing_settings = (
            source_version.get("publishing_settings")
            if isinstance(source_version, dict)
            else None
        )
        version["image_generation_settings"] = validate_image_generation_settings(
            source_image_settings
        )
        version["publishing_settings"] = validate_publishing_settings(
            source_publishing_settings
        )
        version["pipeline"] = validate_prompt_pipeline(
            source_pipeline,
            version["image_generation_settings"]["thumbnail_variant"],
        )
    return normalized
