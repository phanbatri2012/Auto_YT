"""Safe error reporting helpers that avoid leaking local secrets to the UI."""

from __future__ import annotations

import logging
import re
import uuid


LOGGER = logging.getLogger("auto_yt.security")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|password|cookie)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"EAA[A-Za-z0-9_-]{16,}"),
)
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\[^\r\n]*")


def redact_sensitive(value: object) -> str:
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        replacement = r"\1[REDACTED]" if pattern.groups else "[REDACTED]"
        text = pattern.sub(replacement, text)
    return _WINDOWS_PATH.sub("[LOCAL_PATH]", text)[:2_000]


def report_exception(context: str, exc: BaseException) -> str:
    error_id = uuid.uuid4().hex[:10]
    LOGGER.error(
        "%s failed (error_id=%s, error_type=%s): %s",
        context,
        error_id,
        type(exc).__name__,
        redact_sensitive(exc),
    )
    return error_id


def safe_user_error(context: str, exc: BaseException, message: str) -> str:
    error_id = report_exception(context, exc)
    return f"{message} Mã lỗi: {error_id}."
