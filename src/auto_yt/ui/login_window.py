"""PyQt6 login window and background worker for ChatGPT auto login."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from auto_yt.paths import ACCOUNT_PATH, DATA_DIR, SESSION_PATH, gpt_profile_dir
from auto_yt.services.chatgpt_login import ChatGPTLoginError, login_gpt_auto, restore_session

WINDOW_TITLE = "ChatGPT Auto Login"


def _extract_gpt_account(data: dict) -> dict:
    """Extract account fields from either flat or nested config format."""
    if DEFAULT_GPT_ACCOUNT_KEY in data and isinstance(data[DEFAULT_GPT_ACCOUNT_KEY], dict):
        return data[DEFAULT_GPT_ACCOUNT_KEY]
    if "email" in data:
        return data
    return {}


def _save_account_payload(account: dict) -> None:
    """Merge account payload back into account.json under gpt_account1."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = {}
    if ACCOUNT_PATH.exists():
        try:
            existing = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing[DEFAULT_GPT_ACCOUNT_KEY] = account
    ACCOUNT_PATH.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_saved_session_cookies() -> list[dict]:
    """Load saved ChatGPT session cookies from account.json."""
    if not ACCOUNT_PATH.exists():
        return []
    try:
        data = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
        account = _extract_gpt_account(data)
        cookies = account.get("session_cookie", [])
        return cookies if isinstance(cookies, list) else []
    except Exception:
        return []

WINDOW_WIDTH = 520
WINDOW_HEIGHT = 480
DEFAULT_GPT_ACCOUNT_KEY = "gpt_account1"
DEFAULT_GPT_PROFILE = "PROFILE_GPT_1"


# --- Worker thread -------------------------------------------------------

class LoginWorker(QThread):
    """Run login_gpt_auto in a background thread with its own asyncio loop."""

    log = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def __init__(self, account: dict, parent=None):
        super().__init__(parent)
        self.account = account

    def run(self):
        asyncio.run(self._run_login())

    async def _run_login(self):
        from playwright.async_api import async_playwright

        playwright = None
        context = None
        page = None

        try:
            self.log.emit("🚀 Launching browser...")
            playwright = await async_playwright().start()
            profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
            profile_dir.mkdir(parents=True, exist_ok=True)
            self.log.emit(f"📁 Using Chrome profile: {profile_dir}")
            context = await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(60_000)

            handler = _SignalLogHandler(self.log)
            login_logger = logging.getLogger("auto_yt.services.chatgpt_login")
            login_logger.addHandler(handler)
            login_logger.setLevel(logging.INFO)

            saved_cookies = self.account.get("session_cookie") or []
            if saved_cookies:
                self.log.emit(f"♻️ Restoring saved session ({len(saved_cookies)} cookies)...")
                try:
                    result = await restore_session(saved_cookies, page)
                    self.log.emit("✅ Saved session restored.")
                except ChatGPTLoginError as exc:
                    self.log.emit(f"⚠️ Saved session invalid: {exc}")
                    self.log.emit("🔐 Starting full login flow...")
                    result = await login_gpt_auto(self.account, page)
            else:
                self.log.emit("🔐 Starting full login flow...")
                result = await login_gpt_auto(self.account, page)

            login_logger.removeHandler(handler)

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            account_payload = dict(self.account)
            account_payload["session_cookie"] = result.get("cookies", [])
            account_payload.setdefault("folder_user_data", DEFAULT_GPT_PROFILE)
            _save_account_payload(account_payload)

            SESSION_PATH.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.log.emit(f"💾 Session saved to {SESSION_PATH.name}")
            self.finished.emit(result)

        except ChatGPTLoginError as exc:
            self.log.emit(f"❌ {exc.__class__.__name__}: {exc}")
            self.finished.emit({"success": False, "error": str(exc), "type": exc.__class__.__name__})

        except Exception as exc:
            self.log.emit(f"❌ Unexpected: {exc}")
            self.finished.emit({"success": False, "error": str(exc), "type": "Exception"})

        finally:
            for resource in (page, context):
                if resource:
                    try:
                        await resource.close()
                    except Exception:
                        pass
            if playwright:
                try:
                    await playwright.stop()
                except Exception:
                    pass


class OpenProfileWorker(QThread):
    """Open the persistent ChatGPT Chrome profile for manual use."""

    log = pyqtSignal(str)
    finished = pyqtSignal(dict)

    def run(self):
        asyncio.run(self._open_profile())

    async def _open_profile(self):
        from playwright.async_api import async_playwright

        playwright = None
        context = None

        try:
            profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
            profile_dir.mkdir(parents=True, exist_ok=True)
            self.log.emit(f"📂 Opening profile: {profile_dir}")

            playwright = await async_playwright().start()
            context = await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 800},
            )
            saved_cookies = _load_saved_session_cookies()
            if saved_cookies:
                await context.add_cookies(saved_cookies)
                self.log.emit(f"♻️ Injected saved session cookies ({len(saved_cookies)})")
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://chatgpt.com", wait_until="domcontentloaded", timeout=60_000)
            self.log.emit("✅ Profile opened. Close the browser window when done.")

            # Keep Playwright alive while the user manually uses the browser.
            while context.pages:
                await asyncio.sleep(1)

            self.finished.emit({"success": True})

        except Exception as exc:
            self.log.emit(f"❌ Cannot open profile: {exc}")
            self.finished.emit({"success": False, "error": str(exc)})

        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if playwright:
                try:
                    await playwright.stop()
                except Exception:
                    pass


class _SignalLogHandler(logging.Handler):
    """Forward log records to a pyqtSignal(str)."""

    def __init__(self, signal: pyqtSignal):
        super().__init__()
        self._signal = signal

    def emit(self, record: logging.LogRecord):
        try:
            self._signal.emit(self.format(record))
        except Exception:
            pass


# --- Main window ---------------------------------------------------------

class LoginWindow(QWidget):
    def __init__(self):
        super().__init__()
        self._worker: LoginWorker | None = None
        self._profile_worker: OpenProfileWorker | None = None
        self._saved_cookies: list[dict] = []
        self._saved_email = ""
        self._init_ui()
        self._load_config()

    def _init_ui(self):
        self.setWindowTitle(WINDOW_TITLE)
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Email ---
        layout.addWidget(QLabel("Email"))
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("you@example.com")
        layout.addWidget(self.email_input)

        # --- Password ---
        layout.addWidget(QLabel("Password"))
        password_row = QHBoxLayout()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("••••••••")
        password_row.addWidget(self.password_input)

        self.show_password_cb = QCheckBox("Show")
        self.show_password_cb.toggled.connect(self._toggle_password)
        password_row.addWidget(self.show_password_cb)
        layout.addLayout(password_row)

        # --- TOTP Secret ---
        layout.addWidget(QLabel("TOTP Secret (MFA)"))
        self.totp_input = QLineEdit()
        self.totp_input.setPlaceholderText("Base32 secret (optional)")
        layout.addWidget(self.totp_input)

        # --- Buttons row ---
        btn_row = QHBoxLayout()

        self.save_btn = QPushButton("💾  Save")
        self.save_btn.setFixedHeight(38)
        self.save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self.save_btn)

        self.login_btn = QPushButton("🔐  Auto Login")
        self.login_btn.setFixedHeight(38)
        self.login_btn.clicked.connect(self._on_login)
        btn_row.addWidget(self.login_btn)

        layout.addLayout(btn_row)

        self.clear_btn = QPushButton("🗑️  Clear Account")
        self.clear_btn.setFixedHeight(32)
        self.clear_btn.clicked.connect(self._on_clear)
        layout.addWidget(self.clear_btn)

        self.open_profile_btn = QPushButton("📂  Open Profile")
        self.open_profile_btn.setFixedHeight(32)
        self.open_profile_btn.clicked.connect(self._on_open_profile)
        layout.addWidget(self.open_profile_btn)

        # --- Log area ---
        layout.addWidget(QLabel("Log"))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        layout.addWidget(self.log_area, stretch=1)

        # --- Status bar ---
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

    # --- Config persistence -----------------------------------------------

    def _load_config(self):
        if not ACCOUNT_PATH.exists():
            return
        try:
            data = json.loads(ACCOUNT_PATH.read_text(encoding="utf-8"))
            account = _extract_gpt_account(data)
            self.email_input.setText(account.get("email", ""))
            self.password_input.setText(account.get("password", ""))
            self.totp_input.setText(account.get("totp_secret", ""))
            self._saved_email = account.get("email", "").strip()
            self._saved_cookies = account.get("session_cookie", [])
            if self._saved_cookies:
                self._log(f"♻️ Saved session found ({len(self._saved_cookies)} cookies)")
                self.status_label.setText("♻️ Session ready")
                self.status_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        except Exception:
            pass

    def _save_config(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        current_email = self.email_input.text().strip()
        session_cookie = self._saved_cookies if current_email == self._saved_email else []
        data = {
            DEFAULT_GPT_ACCOUNT_KEY: {
                "email": current_email,
                "password": self.password_input.text(),
                "totp_secret": self.totp_input.text().strip(),
                "session_cookie": session_cookie,
                "folder_user_data": str(gpt_profile_dir(DEFAULT_GPT_PROFILE)),
                "type_account": "unknown",
            }
        }
        ACCOUNT_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._saved_email = current_email
        self._saved_cookies = session_cookie

    # --- UI callbacks -----------------------------------------------------

    def _toggle_password(self, checked: bool):
        mode = QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        self.password_input.setEchoMode(mode)

    def _on_save(self):
        self._save_config()
        self._log("💾 Account saved.")
        self.status_label.setText("💾 Saved")
        self.status_label.setStyleSheet("color: #2196F3; font-weight: bold;")

    def _on_login(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            self._log("⚠️  Email and Password are required.")
            return

        self.log_area.clear()
        self._set_running(True)

        account = {
            "email": email,
            "password": password,
            "totp_secret": self.totp_input.text().strip() or None,
            "folder_user_data": str(gpt_profile_dir(DEFAULT_GPT_PROFILE)),
            "session_cookie": self._saved_cookies if email == self._saved_email else [],
        }

        self._worker = LoginWorker(account)
        self._worker.log.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_clear(self):
        self._saved_cookies = []
        if SESSION_PATH.exists():
            SESSION_PATH.unlink()
        profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)
        self._save_config()
        self._log("🗑️ Session, cookies, and Chrome profile cleared.")
        self.status_label.setText("🗑️ Cleared")
        self.status_label.setStyleSheet("color: gray; font-weight: bold;")

    def _on_open_profile(self):
        if self._profile_worker and self._profile_worker.isRunning():
            self._log("⚠️ Profile browser is already open.")
            return
        self._log("📂 Opening profile browser...")
        self._profile_worker = OpenProfileWorker()
        self._profile_worker.log.connect(self._log)
        self._profile_worker.finished.connect(self._on_profile_closed)
        self.open_profile_btn.setEnabled(False)
        self.open_profile_btn.setText("📂  Browser open...")
        self._profile_worker.start()

    def _on_profile_closed(self, result: dict):
        self._profile_worker = None
        self.open_profile_btn.setEnabled(True)
        self.open_profile_btn.setText("📂  Open Profile")
        self._log("📂 Profile browser closed.")

    def _on_finished(self, result: dict):
        self._set_running(False)
        self._worker = None

        if result.get("success"):
            user = result.get("user", {})
            cookies = result.get("cookies", [])
            self._saved_cookies = cookies
            self._saved_email = self.email_input.text().strip()
            self._log("✅ Login successful!")
            self._log(f"   User: {user.get('name', '')} ({user.get('email', '')})")
            self._log(f"   Plan: {user.get('plan', 'N/A')}")
            self._log(f"   Cookies: {len(cookies)} captured")
            self.status_label.setText("✅ Login successful")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            error_type = result.get("type", "Error")
            error_msg = result.get("error", "Unknown error")
            self._log(f"❌ Failed — {error_type}: {error_msg}")
            self.status_label.setText(f"❌ {error_type}")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")

    def _set_running(self, running: bool):
        self.login_btn.setEnabled(not running)
        self.save_btn.setEnabled(not running)
        self.clear_btn.setEnabled(not running)
        self.open_profile_btn.setEnabled(not running)
        self.login_btn.setText("⏳  Logging in..." if running else "🔐  Auto Login")
        if running:
            self.status_label.setText("⏳ Running...")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")

    def _log(self, message: str):
        self.log_area.append(message)
        scrollbar = self.log_area.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(scrollbar.maximum())
