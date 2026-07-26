"""Central path constants for the auto_yt project."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
CHROME_USER_DATA_DIR = DATA_DIR / "chrome_user_data"

ACCOUNT_PATH = DATA_DIR / "account.json"
PROMPTS_PATH = DATA_DIR / "prompts.json"
ACCOUNT_EXAMPLE_PATH = CONFIG_DIR / "account.example.json"
SESSION_PATH = DATA_DIR / "session_chatgpt.json"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"


def gpt_profile_dir(profile_name: str = "PROFILE_GPT_1") -> Path:
    """Return the Chrome user-data dir for a specific GPT profile."""
    return CHROME_USER_DATA_DIR / profile_name
