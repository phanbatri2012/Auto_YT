"""Shared runtime signals for unattended ChatGPT browser jobs."""

from __future__ import annotations


ATTENTION_REQUIRED_MARKER = "###CHATGPT_ATTENTION_REQUIRED###"
CHATGPT_LOGIN_REQUIRED_MESSAGE = "Phiên đăng nhập ChatGPT đã hết hạn."


class ChatGPTAttentionRequiredError(RuntimeError):
    """Raised when a person must repair the saved ChatGPT browser session."""


def is_chatgpt_login_required(error: BaseException | str) -> bool:
    """Return whether Playwright positively identified an expired login."""
    return str(error or "").strip().startswith(CHATGPT_LOGIN_REQUIRED_MESSAGE)


def encode_attention_error(message: str) -> str:
    """Encode an attention error so a subprocess boundary preserves its type."""
    return f"{ATTENTION_REQUIRED_MARKER}{str(message).strip()}"


def decode_attention_error(message: str) -> str | None:
    """Return the user-facing message when stderr contains the marker."""
    value = str(message or "")
    if ATTENTION_REQUIRED_MARKER not in value:
        return None
    return value.rsplit(ATTENTION_REQUIRED_MARKER, 1)[1].strip()
