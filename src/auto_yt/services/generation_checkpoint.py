"""Durable checkpoints for the multi-step ChatGPT video workflow."""

from __future__ import annotations

import json
from pathlib import Path

from auto_yt.paths import DATA_DIR


CHECKPOINT_DIR = DATA_DIR / "generation_checkpoints"


def checkpoint_path(video_id: int) -> Path:
    return CHECKPOINT_DIR / f"video_{int(video_id)}.json"


def load_checkpoint(video_id: int) -> dict:
    path = checkpoint_path(video_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_checkpoint(video_id: int, payload: dict) -> Path:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = checkpoint_path(video_id)
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)
    return path


def clear_checkpoint(video_id: int) -> None:
    path = checkpoint_path(video_id)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
