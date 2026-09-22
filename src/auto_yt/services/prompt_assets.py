"""Service for managing reference assets (characters, landmarks, environments) per prompt version."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from auto_yt.paths import PROMPT_ASSETS_DIR, PROMPTS_PATH

logger = logging.getLogger(__name__)

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def strip_accents(text: str) -> str:
    """Normalize and remove Vietnamese accents."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")
    return text.lower()


def sanitize_folder_name(name: str) -> str:
    """Convert a prompt version name or key into a safe filesystem folder name."""
    clean = re.sub(r'[\\/*?:"<>|]', "_", str(name or "default").strip())
    return clean or "default"


def resolve_prompt_folder_name(prompt_version: str) -> str:
    """Resolve a prompt version ID or key to its human-readable prompt name.
    
    E.g.: 'v_1786603848860' -> 'GKVS'
          'v_1784470764118' -> 'Thấu Hiểu Hôn Nhân'
          'default' -> 'Bản gốc Đinh Đoàn'
    """
    clean_input = str(prompt_version or "").strip()
    if not clean_input:
        return "default"

    try:
        if PROMPTS_PATH.exists():
            data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
            versions = data.get("versions", {})

            # 1. If clean_input is a version ID in versions
            if clean_input in versions:
                v_name = str(versions[clean_input].get("name") or "").strip()
                if v_name:
                    return sanitize_folder_name(v_name)

            # 2. If clean_input already matches a version's name
            for vid, vdata in versions.items():
                v_name = str(vdata.get("name") or "").strip()
                if v_name and v_name.lower() == clean_input.lower():
                    return sanitize_folder_name(v_name)
    except Exception as exc:
        logger.debug("Could not resolve prompt folder name from config: %s", exc)

    return sanitize_folder_name(clean_input)


def get_prompt_asset_dir(prompt_version: str) -> Path:
    """Return the assets directory for a specific prompt version, creating it if needed."""
    folder_name = resolve_prompt_folder_name(prompt_version)
    target_dir = PROMPT_ASSETS_DIR / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    # Check if there is an old legacy folder named after the raw version ID (e.g. 'v_1784470764118')
    raw_folder_name = sanitize_folder_name(prompt_version)
    if raw_folder_name != folder_name:
        legacy_dir = PROMPT_ASSETS_DIR / raw_folder_name
        if legacy_dir.is_dir():
            for item in list(legacy_dir.iterdir()):
                dest = target_dir / item.name
                if not dest.exists():
                    try:
                        shutil.move(str(item), str(dest))
                    except Exception as e:
                        logger.warning("Could not move asset from %s to %s: %s", item, dest, e)
            try:
                if not any(legacy_dir.iterdir()):
                    legacy_dir.rmdir()
            except Exception:
                pass

    return target_dir


def rename_prompt_asset_dir(old_name: str, new_name: str) -> None:
    """Rename an asset directory when a prompt version name is changed."""
    old_folder = sanitize_folder_name(old_name)
    new_folder = sanitize_folder_name(new_name)
    if not old_folder or not new_folder or old_folder == new_folder:
        return

    old_dir = PROMPT_ASSETS_DIR / old_folder
    new_dir = PROMPT_ASSETS_DIR / new_folder
    if old_dir.is_dir() and not new_dir.exists():
        try:
            old_dir.rename(new_dir)
        except Exception as e:
            logger.warning("Could not rename asset dir %s to %s: %s", old_dir, new_dir, e)



def generate_asset_keywords(stem: str) -> list[str]:
    """Generate search variations for matching a filename against Vietnamese transcript.
    
    Example: 'bac_ba' -> ['bác ba', 'bac ba', 'bác 3', 'bac_ba']
    """
    cleaned = stem.replace("-", " ").replace("_", " ").strip()
    words = cleaned.split()
    joined_space = " ".join(words).lower()
    joined_nodash = stem.lower()
    
    variations = {joined_space, joined_nodash, strip_accents(joined_space)}
    
    # Common Vietnamese honorific prefixes
    if joined_space.startswith("bac ") or joined_space.startswith("ong ") or joined_space.startswith("ba ") or joined_space.startswith("chi ") or joined_space.startswith("anh "):
        short_name = " ".join(words[1:])
        if len(short_name) >= 2:
            variations.add(short_name)
            variations.add(strip_accents(short_name))

    return [v for v in variations if len(v) >= 2]


def list_prompt_assets(prompt_version: str) -> list[dict]:
    """List all supported reference images in the prompt version asset folder."""
    asset_dir = get_prompt_asset_dir(prompt_version)
    if not asset_dir.is_dir():
        return []

    assets = []
    for item in sorted(asset_dir.iterdir()):
        if item.is_file() and item.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            stem = item.stem
            display_name = stem.replace("_", " ").replace("-", " ").title()
            assets.append(
                {
                    "name": stem,
                    "display_name": display_name,
                    "filename": item.name,
                    "path": str(item.resolve()),
                    "extension": item.suffix.lower(),
                    "size_bytes": item.stat().st_size,
                    "keywords": generate_asset_keywords(stem),
                }
            )
    return assets


def match_scene_reference(transcript: str, assets: list[dict]) -> dict | None:
    """Match a scene transcript snippet against known prompt assets.
    
    Returns the asset with the strongest match, or None if no match found.
    """
    if not transcript or not assets:
        return None

    norm_transcript = transcript.lower()
    norm_no_accents = strip_accents(transcript)

    best_asset = None
    best_score = 0

    for asset in assets:
        score = 0
        keywords = asset.get("keywords", [])
        for kw in keywords:
            kw_lower = kw.lower()
            # Direct match with accented words
            if kw_lower in norm_transcript:
                # Give higher priority to longer, more specific keywords
                score = max(score, len(kw_lower) * 2)
            # Match without accents
            elif kw_lower in norm_no_accents:
                score = max(score, len(kw_lower))

        if score > best_score:
            best_score = score
            best_asset = asset

    return best_asset if best_score > 0 else None


def open_prompt_asset_dir(prompt_version: str) -> bool:
    """Open the asset directory in the OS file explorer (Windows Explorer)."""
    target_dir = get_prompt_asset_dir(prompt_version)
    target_str = str(target_dir.resolve())
    try:
        if sys.platform == "win32":
            os.startfile(target_str)
            return True
        else:
            subprocess.Popen(["xdg-open", target_str])
            return True
    except Exception as e:
        logger.warning("Could not open explorer for %s: %s", target_str, e)
        return False
