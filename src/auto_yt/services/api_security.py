"""Loopback API session and browser request protections."""

from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse


ALLOWED_FRONTEND_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}
ALLOWED_API_HOSTS = {"127.0.0.1", "localhost", "testserver"}
SESSION_COOKIE_NAME = "autoyt_session"
CSRF_HEADER_NAME = "X-AutoYT-CSRF"
SESSION_PATH = "/api/security/session"
PUBLIC_API_PATHS = {
    "/api/youtube-comments/oauth/callback",
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Voice samples are accepted only by the authenticated loopback API and are
# validated again by the OmniVoice worker. Keep enough room for lossless WAV.
MAX_REQUEST_BODY_BYTES = 105 * 1024 * 1024
_SESSION_SECRET = secrets.token_urlsafe(48)
_CSRF_TOKEN = secrets.token_urlsafe(48)


def _error(status_code: int, detail: str) -> JSONResponse:
    return _secure_response(JSONResponse({"detail": detail}, status_code=status_code))


def _secure_response(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


def _has_allowed_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    if origin:
        return origin in ALLOWED_FRONTEND_ORIGINS
    referer = request.headers.get("referer", "")
    if referer:
        try:
            from urllib.parse import urlparse
            p = urlparse(referer)
            ref_origin = f"{p.scheme}://{p.netloc}"
            return ref_origin in ALLOWED_FRONTEND_ORIGINS
        except Exception:
            return False
    return True


def _is_cross_site(request: Request) -> bool:
    return request.headers.get("sec-fetch-site", "").lower() == "cross-site"


async def protect_loopback_api(request: Request, call_next):
    """Require a browser session for API access and a token for mutations."""
    path = request.url.path
    if not path.startswith("/api/") or request.method == "OPTIONS":
        return await call_next(request)

    if request.url.hostname not in ALLOWED_API_HOSTS:
        return _error(400, "Untrusted API host")

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            request_size = int(content_length)
        except ValueError:
            return _error(400, "Invalid request size")
        if request_size < 0 or request_size > MAX_REQUEST_BODY_BYTES:
            return _error(413, "Request body is too large")

    if path == SESSION_PATH:
        if request.method != "GET":
            return _error(405, "Method not allowed")
        if not _has_allowed_origin(request) or _is_cross_site(request):
            return _error(403, "Untrusted frontend origin")
        response = JSONResponse({"csrf_token": _CSRF_TOKEN})
        response.set_cookie(
            SESSION_COOKIE_NAME,
            _SESSION_SECRET,
            httponly=True,
            samesite="lax",
            secure=False,
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return _secure_response(response)

    if path in PUBLIC_API_PATHS:
        return _secure_response(await call_next(request))

    if not _has_allowed_origin(request) or _is_cross_site(request):
        return _error(403, "Cross-site API request blocked")

    session_secret = request.cookies.get(SESSION_COOKIE_NAME, "")
    csrf_header = request.headers.get(CSRF_HEADER_NAME, "")
    has_valid_cookie = bool(session_secret and secrets.compare_digest(session_secret, _SESSION_SECRET))
    has_valid_token = bool(csrf_header and secrets.compare_digest(csrf_header, _CSRF_TOKEN))

    if not has_valid_cookie and not has_valid_token:
        return _error(401, "Local API session required")

    if request.method in UNSAFE_METHODS:
        csrf_token = request.headers.get(CSRF_HEADER_NAME, "")
        if not csrf_token or not secrets.compare_digest(csrf_token, _CSRF_TOKEN):
            return _error(403, "Invalid API request token")

    return _secure_response(await call_next(request))
