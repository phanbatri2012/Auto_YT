"""Proxy formatting and urllib request handler utilities for channel isolation.

Provides helper functions to parse various proxy string formats (e.g. from GPM profiles)
and build configured urllib openers so that API calls are routed through the channel's
dedicated proxy IP.
"""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class ProxyConfigurationError(RuntimeError):
    """Raised when an isolated channel request has no usable proxy."""


def proxy_display_value(raw_proxy: str | None) -> str:
    """Return a credential-free proxy label suitable for API responses."""
    cleaned = str(raw_proxy or "").strip()
    if not cleaned or cleaned.casefold() in {"direct", "none", "no proxy"}:
        return ""
    if "://" in cleaned:
        try:
            parsed = urllib.parse.urlsplit(cleaned)
            if parsed.hostname and parsed.port:
                return f"{parsed.scheme.lower()}://{parsed.hostname}:{parsed.port}"
        except ValueError:
            return ""
        return ""
    parts = cleaned.split(":")
    if len(parts) >= 2 and parts[0] and parts[1].isdigit():
        return f"{parts[0]}:{parts[1]}"
    return ""


def parse_proxy_url(raw_proxy: str | None) -> str | None:
    """Normalize raw proxy strings into a standard proxy URL.

    Supported formats:
    - host:port:user:pass -> http://user:pass@host:port
    - host:port -> http://host:port
    - http://user:pass@host:port -> preserved
    - socks5://user:pass@host:port -> preserved
    - http://host:port -> preserved
    - socks5://host:port -> preserved

    Returns None if raw_proxy is empty, whitespace, or invalid.
    """
    if not raw_proxy:
        return None

    cleaned = str(raw_proxy).strip()
    if not cleaned or cleaned.casefold() in {"direct", "none", "no proxy"}:
        return None

    # Handle standard URI scheme prefixes
    if "://" in cleaned:
        scheme, remainder = cleaned.split("://", 1)
        scheme_lower = scheme.lower()
        if scheme_lower in {"http", "https", "socks5", "socks5h", "socks4"}:
            try:
                parsed = urllib.parse.urlsplit(cleaned)
                if parsed.hostname and parsed.port:
                    return cleaned
            except ValueError:
                return None
            return None
        # For non-standard schemes (like ww:// or internal schemes), return None
        return None

    parts = cleaned.split(":")
    # Format: host:port:user:pass
    if len(parts) == 4:
        host, port, user, password = parts
        if not host or not port.isdigit() or not user or not password:
            return None
        escaped_user = urllib.parse.quote(user, safe="")
        escaped_pass = urllib.parse.quote(password, safe="")
        return f"http://{escaped_user}:{escaped_pass}@{host}:{port}"

    # Format: host:port
    if len(parts) == 2:
        host, port = parts
        if port.isdigit():
            return f"http://{host}:{port}"

    # Format: host:port:user (unusual, but handle if present)
    if len(parts) == 3:
        host, port, user = parts
        if not host or not port.isdigit() or not user:
            return None
        escaped_user = urllib.parse.quote(user, safe="")
        return f"http://{escaped_user}@{host}:{port}"

    logger.debug("Proxy string could not be parsed into a standard proxy URL.")
    return None


def create_proxy_opener(
    raw_proxy: str | None = None,
    timeout: float = 45.0,
    *,
    require_proxy: bool = False,
) -> urllib.request.OpenerDirector:
    """Create a urllib opener, optionally refusing any direct-network fallback."""
    del timeout
    proxy_url = parse_proxy_url(raw_proxy)
    if proxy_url:
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }
        proxy_handler = urllib.request.ProxyHandler(proxies)
        opener = urllib.request.build_opener(proxy_handler)
        logger.debug("Built urllib opener routed through proxy: %s", proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url)
        return opener

    if require_proxy:
        raise ProxyConfigurationError(
            "Kênh chưa có proxy GPM hợp lệ; kết nối trực tiếp đã bị chặn."
        )
    return urllib.request.build_opener()
