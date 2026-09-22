"""Long-lived local Chromium owner for all ChatGPT automation workers.

The backend starts this process once. Individual video/comment workers attach
through a loopback-only CDP endpoint instead of launching another window. A
worker must never start this service because doing so could steal focus from a
fullscreen application at an arbitrary time.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from auto_yt.paths import DATA_DIR, PROJECT_ROOT, gpt_profile_dir
from auto_yt.services import account_store
from auto_yt.services.secret_store import write_private_text


DEFAULT_GPT_PROFILE = "PROFILE_GPT_1"
SERVICE_STATE_PATH = DATA_DIR / "chatgpt_browser_service.json"
SERVICE_STOP_PATH = DATA_DIR / "chatgpt_browser_service.stop.json"
SERVICE_ERROR_PATH = DATA_DIR / "chatgpt_browser_service.error.log"
SERVICE_LOG_DIR = DATA_DIR / "logs"
SERVICE_START_TIMEOUT_SECONDS = 30.0
SERVICE_STOP_TIMEOUT_SECONDS = 15.0
SERVICE_POLL_SECONDS = 0.25
SERVICE_HEALTH_CHECK_SECONDS = 5.0
SERVICE_HOME_URL = "https://chatgpt.com/"
SERVICE_NAVIGATION_TIMEOUT_MS = 60_000
CDP_HOST = "127.0.0.1"
WINDOWS_BACKGROUND_BROWSER_ARGS = (
    "--window-position=-32000,-32000",
    "--window-size=1280,800",
    "--start-minimized",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
)
COMMON_BROWSER_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-notifications",
    "--disable-session-crashed-bubble",
    "--no-first-run",
    "--no-default-browser-check",
)

_manager_lock = threading.RLock()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict) -> None:
    write_private_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError):
        return False
    return True


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((CDP_HOST, 0))
        return int(listener.getsockname()[1])


def _cdp_is_ready(endpoint: str, timeout: float = 0.8) -> bool:
    if not endpoint.startswith(f"http://{CDP_HOST}:"):
        return False
    try:
        with urllib.request.urlopen(
            f"{endpoint.rstrip('/')}/json/version",
            timeout=max(0.1, timeout),
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return bool(payload.get("webSocketDebuggerUrl"))


def _read_last_error() -> str:
    try:
        return SERVICE_ERROR_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_last_error(message: str) -> None:
    write_private_text(SERVICE_ERROR_PATH, str(message or "").strip() + "\n")


def get_browser_service_launch_options(
    browser_settings: dict,
    *,
    cdp_port: int,
    platform_name: str | None = None,
) -> dict:
    """Build launch options for the single browser owned by the service."""
    hidden_requested = bool(
        browser_settings.get("worker_headless", True)
        or browser_settings.get("game_mode", False)
    )
    platform_name = platform_name or sys.platform
    args = [
        *COMMON_BROWSER_ARGS,
        f"--remote-debugging-address={CDP_HOST}",
        f"--remote-debugging-port={int(cdp_port)}",
    ]
    if hidden_requested and platform_name == "win32":
        # True headless is more likely to trigger ChatGPT/Cloudflare checks.
        # The one headed window is created once, minimized and kept off-screen.
        args.extend(WINDOWS_BACKGROUND_BROWSER_ARGS)
        return {"headless": False, "args": args, "background": True}
    return {
        "headless": hidden_requested,
        "args": args,
        "background": hidden_requested,
    }


def get_browser_service_status() -> dict:
    """Return process and endpoint health without starting Chromium."""
    state = _read_json(SERVICE_STATE_PATH)
    pid = int(state.get("pid") or 0)
    endpoint = str(state.get("cdp_url") or "")
    process_alive = _pid_is_alive(pid)
    connected = process_alive and _cdp_is_ready(endpoint)
    window_visible = bool(state.get("window_visible", False))
    if connected and window_visible:
        service_state = "connected"
        message = "Trình duyệt ChatGPT đang hiển thị trên màn hình."
    elif connected:
        service_state = "connected"
        message = "Trình duyệt ChatGPT nền đã kết nối."
    elif process_alive:
        service_state = "starting"
        message = "Trình duyệt ChatGPT nền đang khởi động."
    else:
        service_state = "stopped"
        message = _read_last_error() or "Trình duyệt ChatGPT nền chưa chạy."
    return {
        "running": connected,
        "connected": connected,
        "process_alive": process_alive,
        "state": service_state,
        "pid": pid or None,
        "started_at": str(state.get("started_at") or ""),
        "window_visible": window_visible,
        "message": message,
    }


def get_browser_service_endpoint() -> str:
    """Return the healthy loopback CDP endpoint, or an empty string."""
    state = _read_json(SERVICE_STATE_PATH)
    endpoint = str(state.get("cdp_url") or "")
    pid = int(state.get("pid") or 0)
    if _pid_is_alive(pid) and _cdp_is_ready(endpoint):
        return endpoint
    return ""


def _service_environment() -> dict[str, str]:
    environment = dict(os.environ)
    source_root = str(PROJECT_ROOT / "src")
    existing_pythonpath = str(environment.get("PYTHONPATH") or "").strip()
    environment["PYTHONPATH"] = (
        f"{source_root}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else source_root
    )
    return environment


def _spawn_service_process() -> None:
    from auto_yt.services.win32_window import spawn_service_on_interactive_desktop

    SERVICE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_path = SERVICE_LOG_DIR / "chatgpt-browser-service.stdout.log"
    stderr_path = SERVICE_LOG_DIR / "chatgpt-browser-service.stderr.log"
    spawn_service_on_interactive_desktop(
        [
            sys.executable,
            "-m",
            "auto_yt.services.chatgpt_browser_service",
            "--serve",
        ],
        cwd=PROJECT_ROOT,
        env=_service_environment(),
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


def start_browser_service(
    timeout: float = SERVICE_START_TIMEOUT_SECONDS,
) -> dict:
    """Start the browser owner once; never call this from a worker job."""
    with _manager_lock:
        status = get_browser_service_status()
        if status["connected"]:
            return status
        if status["process_alive"]:
            deadline = time.monotonic() + max(0.0, timeout)
        else:
            SERVICE_STATE_PATH.unlink(missing_ok=True)
            SERVICE_STOP_PATH.unlink(missing_ok=True)
            SERVICE_ERROR_PATH.unlink(missing_ok=True)
            _spawn_service_process()
            deadline = time.monotonic() + max(0.0, timeout)

        while time.monotonic() < deadline:
            status = get_browser_service_status()
            if status["connected"]:
                return status
            if not status["process_alive"] and SERVICE_ERROR_PATH.exists():
                return status
            time.sleep(SERVICE_POLL_SECONDS)
        return get_browser_service_status()


def stop_browser_service(
    timeout: float = SERVICE_STOP_TIMEOUT_SECONDS,
) -> dict:
    """Ask the owning process to close its context and persist fresh cookies."""
    with _manager_lock:
        state = _read_json(SERVICE_STATE_PATH)
        pid = int(state.get("pid") or 0)
        instance_id = str(state.get("instance_id") or "")
        if not _pid_is_alive(pid):
            SERVICE_STATE_PATH.unlink(missing_ok=True)
            SERVICE_STOP_PATH.unlink(missing_ok=True)
            return get_browser_service_status()

        _write_json(
            SERVICE_STOP_PATH,
            {"instance_id": instance_id, "requested_at": _utc_now()},
        )
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if not _pid_is_alive(pid):
                break
            time.sleep(SERVICE_POLL_SECONDS)
        return get_browser_service_status()


def _visible_window_bounds() -> dict[str, int]:
    """Choose a useful on-screen size without assuming a fixed monitor size."""
    screen_width = 1440
    screen_height = 900
    if sys.platform == "win32":
        try:
            screen_width = max(800, int(ctypes.windll.user32.GetSystemMetrics(0)))
            screen_height = max(600, int(ctypes.windll.user32.GetSystemMetrics(1)))
        except (AttributeError, OSError, ValueError):
            pass
    width = min(1440, max(800, screen_width - 160))
    height = min(960, max(600, screen_height - 120))
    return {
        "left": max(0, (screen_width - width) // 2),
        "top": max(0, (screen_height - height) // 2),
        "width": width,
        "height": height,
    }


def _set_cdp_window_visibility(endpoint: str, visible: bool) -> None:
    """Move the existing Chromium window; never launch or replace a browser."""
    from playwright.sync_api import sync_playwright
    from auto_yt.services.win32_window import set_desktop_window_visibility

    browser_pids: set[int] = set()
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(endpoint)
        if not browser.contexts:
            raise RuntimeError("Browser Service chưa có browser context.")
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()

        try:
            browser_cdp = browser.new_browser_cdp_session()
            sys_info = browser_cdp.send("SystemInfo.getProcessInfo")
            for proc in sys_info.get("processInfo", []):
                proc_id = proc.get("id")
                if isinstance(proc_id, int) and proc_id > 0:
                    browser_pids.add(proc_id)
        except Exception:
            pass

        session = context.new_cdp_session(page)
        window_id = session.send("Browser.getWindowForTarget")["windowId"]
        session.send(
            "Browser.setWindowBounds",
            {"windowId": window_id, "bounds": {"windowState": "normal"}},
        )
        if visible:
            session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": _visible_window_bounds()},
            )
            try:
                page.bring_to_front()
            except Exception:
                pass
        else:
            session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {
                        "left": -32000,
                        "top": -32000,
                        "width": 1280,
                        "height": 800,
                    },
                },
            )
            session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "minimized"}},
            )

    if sys.platform == "win32" and browser_pids:
        set_desktop_window_visibility(browser_pids, visible)


def set_browser_service_window_visibility(visible: bool) -> dict:
    """Show or hide the one shared browser without touching the active page/job."""
    if visible:
        status = get_browser_service_status()
        if not status.get("connected"):
            status = start_browser_service()
            if not status.get("connected"):
                return status

    with _manager_lock:
        status = get_browser_service_status()
        if not status.get("connected"):
            return status
        state = _read_json(SERVICE_STATE_PATH)
        endpoint = str(state.get("cdp_url") or "")
        _set_cdp_window_visibility(endpoint, bool(visible))
        state["window_visible"] = bool(visible)
        state["window_visibility_changed_at"] = _utc_now()
        _write_json(SERVICE_STATE_PATH, state)
        return get_browser_service_status()


def _restore_saved_chatgpt_session(context) -> bool:
    try:
        current_cookies = context.cookies(
            ["https://chatgpt.com/", "https://auth.openai.com/"]
        )
        account = account_store.load_account()
        restorable = account_store.select_restorable_chatgpt_cookies(
            account.get("session_cookie") or [],
            current_cookies,
        )
        if not restorable:
            return False
        context.add_cookies(restorable)
        return True
    except Exception as exc:
        print(f"ChatGPT session restore skipped: {exc}", file=sys.stderr)
        return False


def _save_current_chatgpt_session(context) -> None:
    try:
        cookies = context.cookies(
            ["https://chatgpt.com/", "https://auth.openai.com/"]
        )
        if not account_store.has_chatgpt_auth_cookie(cookies):
            return
        account = account_store.load_account()
        account["session_cookie"] = cookies
        account_store.save_account(account)
    except Exception as exc:
        print(f"ChatGPT session save skipped: {exc}", file=sys.stderr)


def _initialize_service_page(context) -> None:
    """Replace Playwright's visible blank startup page with ChatGPT."""
    page = context.pages[0] if context.pages else context.new_page()
    if str(page.url or "").strip().casefold() != "about:blank":
        return
    try:
        page.goto(
            SERVICE_HOME_URL,
            wait_until="domcontentloaded",
            timeout=SERVICE_NAVIGATION_TIMEOUT_MS,
        )
    except Exception as exc:
        # CDP ownership is still useful when ChatGPT is temporarily unreachable;
        # workers perform their own bounded navigation and attention checks.
        print(
            f"ChatGPT Browser Service initial navigation skipped: {exc}",
            file=sys.stderr,
        )


def _stop_requested(instance_id: str) -> bool:
    request = _read_json(SERVICE_STOP_PATH)
    return bool(request) and request.get("instance_id") == instance_id


def run_browser_service() -> int:
    """Own the persistent context until the backend or user requests a stop."""
    from playwright.sync_api import sync_playwright

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    instance_id = uuid.uuid4().hex
    cdp_port = _reserve_loopback_port()
    endpoint = f"http://{CDP_HOST}:{cdp_port}"
    state = {
        "pid": os.getpid(),
        "instance_id": instance_id,
        "cdp_url": endpoint,
        "profile_dir": str(gpt_profile_dir(DEFAULT_GPT_PROFILE).resolve()),
        "started_at": _utc_now(),
        "ready": False,
        "window_visible": False,
    }
    SERVICE_STOP_PATH.unlink(missing_ok=True)
    SERVICE_ERROR_PATH.unlink(missing_ok=True)
    _write_json(SERVICE_STATE_PATH, state)

    stop_event = threading.Event()

    def request_stop(_signum=None, _frame=None) -> None:
        stop_event.set()

    for signal_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is not None:
            try:
                signal.signal(signal_value, request_stop)
            except (OSError, ValueError):
                pass

    context = None
    exit_code = 0
    try:
        profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
        profile_dir.mkdir(parents=True, exist_ok=True)
        browser_settings = account_store.get_browser_automation_settings()
        launch_options = get_browser_service_launch_options(
            browser_settings,
            cdp_port=cdp_port,
        )
        state["window_visible"] = not bool(launch_options["background"])
        _write_json(SERVICE_STATE_PATH, state)
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=launch_options["headless"],
                args=launch_options["args"],
                viewport={"width": 1280, "height": 800},
            )
            _restore_saved_chatgpt_session(context)
            _initialize_service_page(context)
            ready_deadline = time.monotonic() + SERVICE_START_TIMEOUT_SECONDS
            while time.monotonic() < ready_deadline and not _cdp_is_ready(endpoint):
                time.sleep(SERVICE_POLL_SECONDS)
            if not _cdp_is_ready(endpoint):
                raise RuntimeError("Chromium did not expose its local CDP endpoint.")
            state["ready"] = True
            _write_json(SERVICE_STATE_PATH, state)
            print(f"ChatGPT Browser Service ready at {endpoint}", flush=True)

            next_health_check = time.monotonic() + SERVICE_HEALTH_CHECK_SECONDS
            while not stop_event.wait(SERVICE_POLL_SECONDS):
                if _stop_requested(instance_id):
                    break
                if time.monotonic() >= next_health_check:
                    if not _cdp_is_ready(endpoint):
                        raise RuntimeError(
                            "Chromium closed or its CDP endpoint was lost."
                        )
                    next_health_check = (
                        time.monotonic() + SERVICE_HEALTH_CHECK_SECONDS
                    )
            _save_current_chatgpt_session(context)
    except Exception as exc:
        exit_code = 1
        _write_last_error(str(exc))
        print(f"ChatGPT Browser Service failed: {exc}", file=sys.stderr, flush=True)
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        current_state = _read_json(SERVICE_STATE_PATH)
        if current_state.get("instance_id") == instance_id:
            SERVICE_STATE_PATH.unlink(missing_ok=True)
        stop_request = _read_json(SERVICE_STOP_PATH)
        if stop_request.get("instance_id") == instance_id:
            SERVICE_STOP_PATH.unlink(missing_ok=True)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    arguments = parser.parse_args()
    if not arguments.serve:
        parser.error("--serve is required")
    return run_browser_service()


if __name__ == "__main__":
    raise SystemExit(main())
