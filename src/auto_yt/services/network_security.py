"""Shared outbound download validation for untrusted remote media."""

from __future__ import annotations

import urllib.request
from urllib.parse import urlparse


MAX_AUDIO_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_IMAGE_DOWNLOAD_BYTES = 25 * 1024 * 1024
GENMAX_AUDIO_HOSTS = ("api.genmax.io",)
CHATGPT_IMAGE_HOSTS = ("chatgpt.com", "openai.com", "oaiusercontent.com")


class UnsafeRemoteResource(ValueError):
    """Raised when a remote resource does not satisfy the media policy."""


def validate_https_url(
    value: str,
    *,
    allowed_hosts: tuple[str, ...],
    allow_subdomains: bool = False,
) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    allowed = tuple(item.casefold().rstrip(".") for item in allowed_hosts)
    host_allowed = host in allowed or (
        allow_subdomains
        and any(host.endswith(f".{allowed_host}") for allowed_host in allowed)
    )
    if parsed.scheme.casefold() != "https" or not host_allowed:
        raise UnsafeRemoteResource("Remote media URL is not an approved HTTPS address.")
    if parsed.username or parsed.password:
        raise UnsafeRemoteResource("Remote media URL must not contain credentials.")
    return url


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...], allow_subdomains: bool) -> None:
        self.allowed_hosts = allowed_hosts
        self.allow_subdomains = allow_subdomains
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_https_url(
            newurl,
            allowed_hosts=self.allowed_hosts,
            allow_subdomains=self.allow_subdomains,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_bounded(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    allowed_content_types: tuple[str, ...],
    max_bytes: int,
    timeout_seconds: int,
    allow_subdomains: bool = False,
) -> bytes:
    validated_url = validate_https_url(
        url,
        allowed_hosts=allowed_hosts,
        allow_subdomains=allow_subdomains,
    )
    request = urllib.request.Request(
        validated_url,
        headers={"User-Agent": "Auto_YT/1.0", "Accept": ", ".join(allowed_content_types)},
    )
    opener = urllib.request.build_opener(
        _ValidatedRedirectHandler(allowed_hosts, allow_subdomains)
    )
    with opener.open(request, timeout=timeout_seconds) as response:
        validate_https_url(
            response.geturl(),
            allowed_hosts=allowed_hosts,
            allow_subdomains=allow_subdomains,
        )
        content_type = str(response.headers.get_content_type() or "").casefold()
        if content_type not in {item.casefold() for item in allowed_content_types}:
            raise UnsafeRemoteResource(
                f"Remote media returned an unsupported content type: {content_type or 'unknown'}."
            )
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError as exc:
                raise UnsafeRemoteResource("Remote media returned an invalid size.") from exc
            if declared_size < 0 or declared_size > max_bytes:
                raise UnsafeRemoteResource("Remote media exceeds the configured size limit.")

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(min(1024 * 1024, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise UnsafeRemoteResource("Remote media exceeds the configured size limit.")
            chunks.append(chunk)
        return b"".join(chunks)


def validate_image_bytes(data: bytes) -> None:
    if len(data) > MAX_IMAGE_DOWNLOAD_BYTES:
        raise UnsafeRemoteResource("Generated image exceeds the configured size limit.")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise UnsafeRemoteResource("Generated thumbnail is not a valid PNG image.")


def validate_chatgpt_image_url(url: str) -> str:
    return validate_https_url(
        url,
        allowed_hosts=CHATGPT_IMAGE_HOSTS,
        allow_subdomains=True,
    )
