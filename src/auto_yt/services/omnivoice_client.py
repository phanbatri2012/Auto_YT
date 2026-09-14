from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from auto_yt.paths import DATA_DIR, PROJECT_ROOT
from auto_yt.services.secret_store import (
    encrypt_secret,
    read_encrypted_secret_file,
    write_private_text,
)


DEFAULT_BASE_URL = "http://127.0.0.1:8011"
TOKEN_PATH = DATA_DIR / "omnivoice_internal_token.txt"
OMNIVOICE_ROOT = Path(
    os.environ.get(
        "AUTO_YT_OMNIVOICE_ROOT",
        str(PROJECT_ROOT.parent.parent / "omnivoice"),
    )
).resolve()
MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 2 * 1024 * 1024 * 1024
START_TIMEOUT_SECONDS = 45
_worker_process: subprocess.Popen | None = None


def _get_token() -> str:
    environment_token = os.environ.get("AUTO_YT_OMNIVOICE_TOKEN", "").strip()
    if environment_token:
        return environment_token
    if TOKEN_PATH.exists():
        token = read_encrypted_secret_file(TOKEN_PATH)
        if token:
            return token
    token = secrets.token_urlsafe(48)
    write_private_text(TOKEN_PATH, encrypt_secret(token))
    return token


def _request(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    body: bytes | None = None,
    content_type: str = "application/json",
    timeout: int = 30,
    base_url: str = DEFAULT_BASE_URL,
) -> bytes:
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
    )
    request.add_header("Authorization", f"Bearer {_get_token()}")
    if body is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_AUDIO_BYTES + 1)
            if len(raw) > MAX_AUDIO_BYTES:
                raise RuntimeError("OmniVoice returned a response that is too large.")
            return raw
    except urllib.error.HTTPError as exc:
        error_body = exc.read(64 * 1024).decode("utf-8", errors="replace")
        try:
            detail = json.loads(error_body).get("detail") or error_body
        except json.JSONDecodeError:
            detail = error_body
        raise RuntimeError(f"OmniVoice API error: {exc.code} - {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Không thể kết nối OmniVoice: {exc}") from exc


def _request_json(path: str, **kwargs) -> dict:
    raw = _request(path, **kwargs)
    if len(raw) > MAX_JSON_BYTES:
        raise RuntimeError("OmniVoice returned JSON that is too large.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("OmniVoice returned an invalid response.") from exc


def health(*, start_if_needed: bool = False, base_url: str = DEFAULT_BASE_URL) -> dict:
    try:
        return _request_json("/health", timeout=5, base_url=base_url)
    except RuntimeError:
        if not start_if_needed:
            raise
        ensure_worker_running(base_url=base_url)
        return _request_json("/health", timeout=5, base_url=base_url)


def ensure_worker_running(*, base_url: str = DEFAULT_BASE_URL) -> None:
    global _worker_process
    try:
        _request_json("/health", timeout=2, base_url=base_url)
        return
    except RuntimeError:
        pass
    python_path = OMNIVOICE_ROOT / ".venv" / "Scripts" / "python.exe"
    api_server_path = OMNIVOICE_ROOT / "api_server.py"
    if not python_path.exists() or not api_server_path.exists():
        raise RuntimeError(f"Không tìm thấy OmniVoice worker tại {OMNIVOICE_ROOT}.")
    environment = os.environ.copy()
    environment["AUTO_YT_OMNIVOICE_TOKEN"] = _get_token()
    environment.setdefault("OMNIVOICE_IDLE_UNLOAD_SECONDS", "900")
    creation_flags = 0
    startup_info = None
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NO_WINDOW
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup_info.wShowWindow = subprocess.SW_HIDE
    _worker_process = subprocess.Popen(
        [
            str(python_path),
            "-m",
            "uvicorn",
            "api_server:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8011",
        ],
        cwd=str(OMNIVOICE_ROOT),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        startupinfo=startup_info,
    )
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _worker_process.poll() is not None:
            raise RuntimeError("OmniVoice worker dừng trong lúc khởi động.")
        try:
            _request_json("/health", timeout=2, base_url=base_url)
            return
        except RuntimeError:
            time.sleep(0.5)
    raise RuntimeError("Quá thời gian chờ OmniVoice worker khởi động.")


def list_voices(*, base_url: str = DEFAULT_BASE_URL) -> dict:
    ensure_worker_running(base_url=base_url)
    return _request_json("/v1/voices", base_url=base_url)


def clone_voice(
    *,
    name: str,
    file_name: str,
    file_bytes: bytes,
    start_seconds: float,
    end_seconds: float,
    reference_text: str = "",
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    ensure_worker_running(base_url=base_url)
    boundary = f"----AutoYT{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for field_name, value in (
        ("name", name),
        ("start_seconds", str(start_seconds)),
        ("end_seconds", str(end_seconds)),
        ("reference_text", reference_text),
    ):
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    safe_file_name = Path(file_name).name
    parts.extend(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{safe_file_name}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return _request_json(
        "/v1/voices/clone",
        method="POST",
        body=b"".join(parts),
        content_type=f"multipart/form-data; boundary={boundary}",
        timeout=30 * 60,
        base_url=base_url,
    )


def submit_job(
    text: str,
    profile_id: str,
    request_hash: str,
    settings: dict,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    ensure_worker_running(base_url=base_url)
    return _request_json(
        "/v1/jobs",
        method="POST",
        payload={
            "text": text,
            "voice_id": profile_id,
            "request_hash": request_hash,
            "settings": settings,
        },
        timeout=60,
        base_url=base_url,
    )


def get_job(job_id: str, *, base_url: str = DEFAULT_BASE_URL) -> dict:
    ensure_worker_running(base_url=base_url)
    return _request_json(f"/v1/jobs/{job_id}", base_url=base_url)


def cancel_job(job_id: str, *, base_url: str = DEFAULT_BASE_URL) -> dict:
    ensure_worker_running(base_url=base_url)
    return _request_json(
        f"/v1/jobs/{job_id}/cancel",
        method="POST",
        payload={},
        base_url=base_url,
    )


def recover_job(job_id: str, *, base_url: str = DEFAULT_BASE_URL) -> dict:
    ensure_worker_running(base_url=base_url)
    return _request_json(
        f"/v1/jobs/{job_id}/recover",
        method="POST",
        payload={},
        base_url=base_url,
    )


def download_audio(
    job_id: str,
    destination: Path,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> Path:
    ensure_worker_running(base_url=base_url)
    raw = _request(f"/v1/jobs/{job_id}/audio", timeout=30 * 60, base_url=base_url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, destination)
    return destination
