"""Encrypted persistence for the local ChatGPT account configuration."""

from __future__ import annotations

import json
from pathlib import Path

from auto_yt.paths import ACCOUNT_PATH, SESSION_PATH
from auto_yt.services.secret_store import decrypt_secret, encrypt_secret, write_private_text


DEFAULT_ACCOUNT_KEY = "gpt_account1"
TEXT_SECRET_FIELDS = ("password", "totp_secret")
SESSION_COOKIE_FIELD = "session_cookie"
DEFAULT_BROWSER_AUTOMATION_SETTINGS = {
    "worker_headless": True,
    "game_mode": False,
}
CHATGPT_COOKIE_DOMAINS = ("chatgpt.com", "openai.com")
CHATGPT_AUTH_COOKIE_PREFIXES = (
    "__Secure-next-auth.session-token",
    "unified_session",
)


def _load_document() -> dict:
    if not ACCOUNT_PATH.exists():
        return {}
    try:
        value = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _extract_raw_account(document: dict) -> dict:
    nested = document.get(DEFAULT_ACCOUNT_KEY)
    if isinstance(nested, dict):
        return dict(nested)
    if "email" in document:
        return dict(document)
    return {}


def _decode_account(raw_account: dict) -> dict:
    account = {
        key: value
        for key, value in raw_account.items()
        if key not in {
            *TEXT_SECRET_FIELDS,
            *(f"{field}_encrypted" for field in TEXT_SECRET_FIELDS),
            SESSION_COOKIE_FIELD,
            f"{SESSION_COOKIE_FIELD}_encrypted",
        }
    }
    for field in TEXT_SECRET_FIELDS:
        stored_value = raw_account.get(f"{field}_encrypted")
        if stored_value is None:
            stored_value = raw_account.get(field, "")
        account[field] = decrypt_secret(str(stored_value or ""))

    stored_cookies = raw_account.get(f"{SESSION_COOKIE_FIELD}_encrypted")
    if stored_cookies:
        try:
            decoded_cookies = json.loads(decrypt_secret(str(stored_cookies)))
        except json.JSONDecodeError:
            decoded_cookies = []
    else:
        decoded_cookies = raw_account.get(SESSION_COOKIE_FIELD, [])
    account[SESSION_COOKIE_FIELD] = decoded_cookies if isinstance(decoded_cookies, list) else []
    return account


def _encode_account(account: dict) -> dict:
    stored = {
        key: value
        for key, value in account.items()
        if key not in {
            *TEXT_SECRET_FIELDS,
            *(f"{field}_encrypted" for field in TEXT_SECRET_FIELDS),
            SESSION_COOKIE_FIELD,
            f"{SESSION_COOKIE_FIELD}_encrypted",
        }
    }
    for field in TEXT_SECRET_FIELDS:
        value = str(account.get(field) or "")
        if value:
            stored[f"{field}_encrypted"] = encrypt_secret(value)
    cookies = account.get(SESSION_COOKIE_FIELD)
    if isinstance(cookies, list) and cookies:
        stored[f"{SESSION_COOKIE_FIELD}_encrypted"] = encrypt_secret(
            json.dumps(cookies, ensure_ascii=False, separators=(",", ":"))
        )
    return stored


def _requires_migration(document: dict, raw_account: dict) -> bool:
    return (
        DEFAULT_ACCOUNT_KEY not in document
        or any(field in raw_account for field in (*TEXT_SECRET_FIELDS, SESSION_COOKIE_FIELD))
    )


def load_account(*, migrate: bool = True) -> dict:
    """Return the account decrypted in memory and migrate legacy storage."""
    document = _load_document()
    raw_account = _extract_raw_account(document)
    account = _decode_account(raw_account)
    if migrate and raw_account and _requires_migration(document, raw_account):
        save_account(account)
    if migrate:
        # This legacy duplicate could contain plaintext cookies and is no
        # longer consumed anywhere in the application.
        SESSION_PATH.unlink(missing_ok=True)
    return account


def save_account(account: dict) -> None:
    """Persist a complete account payload without plaintext secrets."""
    document = _load_document()
    if DEFAULT_ACCOUNT_KEY not in document and "email" in document:
        document = {}
    document[DEFAULT_ACCOUNT_KEY] = _encode_account(dict(account))
    write_private_text(
        ACCOUNT_PATH,
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
    )


def clear_account() -> None:
    ACCOUNT_PATH.unlink(missing_ok=True)
    SESSION_PATH.unlink(missing_ok=True)


def is_chatgpt_cookie(cookie: dict) -> bool:
    """Return whether one browser cookie belongs to ChatGPT authentication."""
    domain = str(cookie.get("domain") or "").strip().lstrip(".").casefold()
    return any(
        domain == allowed or domain.endswith(f".{allowed}")
        for allowed in CHATGPT_COOKIE_DOMAINS
    )


def has_chatgpt_auth_cookie(cookies: list[dict] | None) -> bool:
    """Detect a real ChatGPT session token, not only preference cookies."""
    return any(
        is_chatgpt_cookie(cookie)
        and str(cookie.get("name") or "").startswith(CHATGPT_AUTH_COOKIE_PREFIXES)
        and bool(str(cookie.get("value") or ""))
        for cookie in (cookies or [])
        if isinstance(cookie, dict)
    )


def select_restorable_chatgpt_cookies(
    saved_cookies: list[dict] | None,
    current_cookies: list[dict] | None,
) -> list[dict]:
    """Return saved ChatGPT cookies only when the profile has no current session.

    This prevents a stale encrypted session from overwriting a newer browser
    login while still making account migration between project paths work.
    """
    if has_chatgpt_auth_cookie(current_cookies):
        return []
    return [
        dict(cookie)
        for cookie in (saved_cookies or [])
        if isinstance(cookie, dict) and is_chatgpt_cookie(cookie)
    ]


def get_browser_automation_settings() -> dict:
    """Return safe defaults for all unattended browser workers."""
    account = load_account()
    return {
        "worker_headless": bool(account.get("worker_headless", True)),
        "game_mode": bool(account.get("game_mode", False)),
    }


def save_browser_automation_settings(settings: dict) -> dict:
    """Persist browser behavior without touching credentials or cookies."""
    normalized = {
        "worker_headless": bool(settings.get("worker_headless", True)),
        "game_mode": bool(settings.get("game_mode", False)),
    }
    account = load_account()
    account.update(normalized)
    save_account(account)
    return normalized
