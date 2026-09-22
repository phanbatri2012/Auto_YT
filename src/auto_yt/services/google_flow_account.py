"""Encrypted persistence for the local Google Flow account."""

from __future__ import annotations

import json
import re

from auto_yt.paths import GOOGLE_FLOW_ACCOUNT_PATH
from auto_yt.services.secret_store import decrypt_secret, encrypt_secret, write_private_text


SECRET_FIELDS = ("password", "totp_secret")
COOKIE_FIELD = "session_cookies"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GOOGLE_COOKIE_DOMAINS = ("google.com", "labs.google")
GOOGLE_AUTH_COOKIE_NAMES = frozenset(
    {
        "SID",
        "HSID",
        "SSID",
        "APISID",
        "SAPISID",
        "__Secure-1PAPISID",
        "__Secure-3PAPISID",
        "__Secure-1PSID",
        "__Secure-3PSID",
        "__Secure-1PSIDTS",
        "__Secure-3PSIDTS",
    }
)


def _read_document() -> dict:
    if not GOOGLE_FLOW_ACCOUNT_PATH.exists():
        return {}
    try:
        value = json.loads(GOOGLE_FLOW_ACCOUNT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_account() -> dict:
    raw = _read_document()
    account = {
        key: value
        for key, value in raw.items()
        if key not in {
            *SECRET_FIELDS,
            *(f"{field}_encrypted" for field in SECRET_FIELDS),
            COOKIE_FIELD,
            f"{COOKIE_FIELD}_encrypted",
        }
    }
    requires_migration = False
    for field in SECRET_FIELDS:
        encrypted = raw.get(f"{field}_encrypted")
        if encrypted is None:
            encrypted = raw.get(field, "")
            requires_migration = requires_migration or field in raw
        account[field] = decrypt_secret(str(encrypted or ""))
    encrypted_cookies = raw.get(f"{COOKIE_FIELD}_encrypted")
    if encrypted_cookies:
        try:
            cookies = json.loads(decrypt_secret(str(encrypted_cookies)))
        except json.JSONDecodeError:
            cookies = []
    else:
        cookies = raw.get(COOKIE_FIELD, [])
        requires_migration = requires_migration or COOKIE_FIELD in raw
    account[COOKIE_FIELD] = cookies if isinstance(cookies, list) else []
    if raw and requires_migration:
        save_account(account)
    return account


def save_account(account: dict) -> None:
    stored = {
        key: value
        for key, value in dict(account).items()
        if key not in {
            *SECRET_FIELDS,
            *(f"{field}_encrypted" for field in SECRET_FIELDS),
            COOKIE_FIELD,
            f"{COOKIE_FIELD}_encrypted",
        }
    }
    for field in SECRET_FIELDS:
        value = str(account.get(field) or "")
        if value:
            stored[f"{field}_encrypted"] = encrypt_secret(value)
    cookies = account.get(COOKIE_FIELD)
    if isinstance(cookies, list) and cookies:
        stored[f"{COOKIE_FIELD}_encrypted"] = encrypt_secret(
            json.dumps(cookies, ensure_ascii=False, separators=(",", ":"))
        )
    write_private_text(
        GOOGLE_FLOW_ACCOUNT_PATH,
        json.dumps(stored, ensure_ascii=False, indent=2) + "\n",
    )


def save_credentials(payload: dict) -> dict:
    next_email = str(payload.get("email") or "").strip()
    if not EMAIL_PATTERN.fullmatch(next_email):
        raise ValueError("Email Google không hợp lệ.")
    account = load_account()
    previous_email = str(account.get("email") or "").strip().casefold()
    account["email"] = next_email
    for field in SECRET_FIELDS:
        if payload.get(field):
            account[field] = str(payload[field]).strip()
    if previous_email and next_email.casefold() != previous_email:
        account[COOKIE_FIELD] = []
    save_account(account)
    return get_account_status()


def get_account_status() -> dict:
    account = load_account()
    return {
        "email": str(account.get("email") or ""),
        "password_configured": bool(account.get("password")),
        "totp_configured": bool(account.get("totp_secret")),
        "session_configured": bool(account.get(COOKIE_FIELD)),
    }


def clear_account() -> None:
    GOOGLE_FLOW_ACCOUNT_PATH.unlink(missing_ok=True)


def is_google_flow_cookie(cookie: dict) -> bool:
    domain = str(cookie.get("domain") or "").strip().lstrip(".").casefold()
    return any(
        domain == allowed_domain or domain.endswith(f".{allowed_domain}")
        for allowed_domain in GOOGLE_COOKIE_DOMAINS
    )


def has_google_flow_auth_cookie(cookies: list[dict] | None) -> bool:
    return any(
        is_google_flow_cookie(cookie)
        and str(cookie.get("name") or "") in GOOGLE_AUTH_COOKIE_NAMES
        and bool(str(cookie.get("value") or ""))
        for cookie in (cookies or [])
        if isinstance(cookie, dict)
    )


def select_restorable_google_flow_cookies(
    saved_cookies: list[dict] | None,
    current_cookies: list[dict] | None,
) -> list[dict]:
    if has_google_flow_auth_cookie(current_cookies):
        return []
    return [
        dict(cookie)
        for cookie in (saved_cookies or [])
        if isinstance(cookie, dict) and is_google_flow_cookie(cookie)
    ]
