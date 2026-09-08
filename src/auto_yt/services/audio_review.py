"""Deterministic preflight checks for scripts before paid TTS submission."""

from __future__ import annotations

import hashlib
import re
from collections import Counter


REQUIRED_AUDIO_SECTIONS = ("INTRO", "BODY", "OUTRO")
# Genmax voices vary considerably in cadence. Keep the estimate deliberately
# broad and informational; it must never become a content-length requirement.
WORDS_PER_MINUTE_SLOW = 160
WORDS_PER_MINUTE_FAST = 240
SECTION_PATTERN = re.compile(
    r"(?ms)^### \[([^\]]+)\]\s*\n(.*?)(?=^### \[[^\]]+\]\s*$|\Z)"
)
EDITORIAL_PREFIXES = (
    "bổ sung ",
    "chuyển ý",
    "dẫn dắt ",
    "đào sâu ",
    "giải thích ",
    "giữ nhịp",
    "khai thác ",
    "kết nối ",
    "làm rõ ",
    "mở rộng ",
    "nhấn mạnh ",
    "nêu bật ",
    "tăng nhịp",
    "triển khai ",
)


def extract_audio_sections(script_text: str) -> dict[str, str]:
    """Return the narrative sections that are actually eligible for TTS."""
    sections: dict[str, str] = {}
    for match in SECTION_PATTERN.finditer(script_text or ""):
        name = match.group(1).strip().upper()
        if name in REQUIRED_AUDIO_SECTIONS and name not in sections:
            content = re.sub(r"\n[ \t]*\n+", "\n\n", match.group(2)).strip()
            sections[name] = content
    return sections


def get_audio_script(script_text: str) -> str:
    sections = extract_audio_sections(script_text)
    return "\n\n".join(
        sections[name] for name in REQUIRED_AUDIO_SECTIONS if sections.get(name)
    ).strip()


def get_audio_script_hash(script_text: str) -> str:
    audio_script = get_audio_script(script_text)
    normalized = "\n".join(
        line.rstrip() for line in audio_script.replace("\r\n", "\n").splitlines()
    ).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _word_count(value: str) -> int:
    return len(re.findall(r"[^\W_]+(?:['’][^\W_]+)?", value, re.UNICODE))


def _issue(code: str, message: str, section: str = "") -> dict[str, str]:
    issue = {"code": code, "message": message}
    if section:
        issue["section"] = section
    return issue


def _find_editorial_artifacts(sections: dict[str, str]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    for section_name, section_text in sections.items():
        blocks = [
            block.strip()
            for block in re.split(r"\n[ \t]*\n+", section_text)
            if block.strip()
        ]
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines or len(lines) > 3:
                continue
            normalized_lines = [
                re.sub(r"^(?:#{1,6}\s*|[-*•]\s+|\d+[.)]\s+)", "", line).strip()
                for line in lines
            ]
            if any(_word_count(line) > 14 for line in normalized_lines):
                continue
            if any(re.search(r'[.!?…;]["”’\])]*$', line) for line in normalized_lines):
                continue
            lowered = " ".join(normalized_lines).lower()
            looks_editorial = (
                any(line.startswith("#") for line in lines)
                or lowered.startswith(EDITORIAL_PREFIXES)
                or bool(
                    re.match(
                        r"^(?:intro|body|outro|ghi chú|ý chính|trọng tâm|"
                        r"phần\s+(?:\d+|intro|body|outro))\b",
                        lowered,
                    )
                )
            )
            if looks_editorial or len(blocks) > 2:
                preview = " / ".join(normalized_lines)[:120]
                warnings.append(
                    _issue(
                        "editorial_artifact",
                        f'Có dòng ngắn giống nhãn/ghi chú biên tập: “{preview}”.',
                        section_name,
                    )
                )
    return warnings


def _find_duplicate_paragraphs(sections: dict[str, str]) -> list[dict[str, str]]:
    paragraphs: list[tuple[str, str, str]] = []
    for section_name, section_text in sections.items():
        for paragraph in re.split(r"\n[ \t]*\n+", section_text):
            cleaned = re.sub(r"\s+", " ", paragraph).strip()
            if len(cleaned) < 160 or _word_count(cleaned) < 25:
                continue
            normalized = re.sub(r"[^\w]+", " ", cleaned.lower(), flags=re.UNICODE).strip()
            paragraphs.append((normalized, cleaned, section_name))

    counts = Counter(item[0] for item in paragraphs)
    warnings: list[dict[str, str]] = []
    reported: set[str] = set()
    for normalized, paragraph, section_name in paragraphs:
        if counts[normalized] < 2 or normalized in reported:
            continue
        reported.add(normalized)
        warnings.append(
            _issue(
                "duplicate_paragraph",
                f'Đoạn văn bị lặp {counts[normalized]} lần: “{paragraph[:100]}…”.',
                section_name,
            )
        )
    return warnings


def audit_script_for_audio(script_text: str) -> dict:
    """Audit content without enforcing an arbitrary target length."""
    sections = extract_audio_sections(script_text)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    for section_name in REQUIRED_AUDIO_SECTIONS:
        if section_name not in sections:
            errors.append(
                _issue(
                    "missing_section",
                    f"Thiếu phần {section_name} trong kịch bản.",
                    section_name,
                )
            )
        elif not sections[section_name].strip():
            errors.append(
                _issue(
                    "empty_section",
                    f"Phần {section_name} đang trống.",
                    section_name,
                )
            )

    audio_script = get_audio_script(script_text)
    if audio_script and (
        "\ufffd" in audio_script
        or re.search(r"(?:\w\?\w|\?{2,}\w|\w\?{2,})", audio_script, re.UNICODE)
    ):
        errors.append(
            _issue(
                "corrupted_unicode",
                "Phần đọc có ký tự lỗi/mất dấu; cần sửa trước khi tạo audio.",
            )
        )

    if re.search(r"```|###\s*\[", audio_script):
        errors.append(
            _issue(
                "unexpected_markup",
                "Phần đọc còn chứa khối mã hoặc nhãn hệ thống không hợp lệ.",
            )
        )

    warnings.extend(_find_editorial_artifacts(sections))
    warnings.extend(_find_duplicate_paragraphs(sections))

    word_count = _word_count(audio_script)
    character_count = len(audio_script)
    paragraph_count = len(
        [part for part in re.split(r"\n[ \t]*\n+", audio_script) if part.strip()]
    )
    estimated_min_seconds = round(word_count / WORDS_PER_MINUTE_FAST * 60)
    estimated_max_seconds = round(word_count / WORDS_PER_MINUTE_SLOW * 60)

    section_metrics = {
        section_name: {
            "word_count": _word_count(sections.get(section_name, "")),
            "character_count": len(sections.get(section_name, "")),
        }
        for section_name in REQUIRED_AUDIO_SECTIONS
    }
    return {
        "script_hash": get_audio_script_hash(script_text),
        "can_approve": not errors,
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "word_count": word_count,
            "character_count": character_count,
            "paragraph_count": paragraph_count,
            "estimated_min_seconds": estimated_min_seconds,
            "estimated_max_seconds": estimated_max_seconds,
            "sections": section_metrics,
        },
    }
