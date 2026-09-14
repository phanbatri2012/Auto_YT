"""Windows user-bound secret storage helpers."""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import os
import tempfile
from pathlib import Path


DPAPI_PREFIX = "dpapi:"


class SecretStorageError(RuntimeError):
    """Raised when a local secret cannot be protected or recovered."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob_from_bytes(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value)
    return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _require_windows_dpapi() -> None:
    if not hasattr(ctypes, "windll"):
        raise SecretStorageError("Secret encryption is only supported on Windows.")


def encrypt_secret(value: str) -> str:
    """Encrypt a string for the current Windows user with DPAPI."""
    if not value:
        return ""
    if value.startswith(DPAPI_PREFIX):
        return value
    _require_windows_dpapi()
    input_blob, input_buffer = _blob_from_bytes(value.encode("utf-8"))
    output_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    ):
        raise SecretStorageError(str(ctypes.WinError()))
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        del input_buffer


def decrypt_secret(value: str) -> str:
    """Decrypt a DPAPI value; legacy plaintext is returned for migration."""
    if not value or not value.startswith(DPAPI_PREFIX):
        return value or ""
    _require_windows_dpapi()
    try:
        encrypted = base64.b64decode(value.removeprefix(DPAPI_PREFIX), validate=True)
    except (ValueError, TypeError) as exc:
        raise SecretStorageError("Encrypted secret is malformed.") from exc
    input_blob, input_buffer = _blob_from_bytes(encrypted)
    output_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob)
    ):
        raise SecretStorageError(str(ctypes.WinError()))
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)
        del input_buffer


def write_private_text(path: Path, value: str) -> None:
    """Atomically replace a local secret-bearing text file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_encrypted_secret_file(path: Path, *, migrate_plaintext: bool = True) -> str:
    """Read a secret file and migrate legacy plaintext in place."""
    if not path.exists():
        return ""
    stored_value = path.read_text(encoding="utf-8").strip()
    if not stored_value:
        return ""
    plaintext = decrypt_secret(stored_value)
    if migrate_plaintext and not stored_value.startswith(DPAPI_PREFIX):
        write_private_text(path, encrypt_secret(plaintext))
    return plaintext

