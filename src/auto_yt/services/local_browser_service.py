"""Local Chromium Browser Service (Cốc Cốc, Chrome, Edge, Brave).

Provides discovery, profile inspection, process launch with Remote Debugging Port,
and Playwright CDP integration for locally installed Chromium browsers on Windows.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

# Standard executable and User Data locations on Windows
BROWSER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "coccoc": {
        "name": "Cốc Cốc",
        "exe_candidates": [
            r"%LOCALAPPDATA%\CocCoc\Browser\Application\browser.exe",
            r"%PROGRAMFILES%\CocCoc\Browser\Application\browser.exe",
            r"%PROGRAMFILES(X86)%\CocCoc\Browser\Application\browser.exe",
        ],
        "user_data_candidates": [
            r"%LOCALAPPDATA%\CocCoc\Browser\User Data",
        ],
    },
    "chrome": {
        "name": "Google Chrome",
        "exe_candidates": [
            r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
            r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ],
        "user_data_candidates": [
            r"%LOCALAPPDATA%\Google\Chrome\User Data",
        ],
    },
    "edge": {
        "name": "Microsoft Edge",
        "exe_candidates": [
            r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
            r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
            r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
        ],
        "user_data_candidates": [
            r"%LOCALAPPDATA%\Microsoft\Edge\User Data",
        ],
    },
    "brave": {
        "name": "Brave",
        "exe_candidates": [
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%PROGRAMFILES%\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
        "user_data_candidates": [
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data",
        ],
    },
}


def _expand_path(raw_path: str) -> Path:
    return Path(os.path.expandvars(raw_path))


def find_free_port(start_port: int = 9222, max_port: int = 9299) -> int:
    """Find an available TCP port for Chrome Remote Debugging."""
    for port in range(start_port, max_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    # Fallback to OS assigned port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def resolve_browser_paths(browser_key: str) -> tuple[Path | None, Path | None]:
    """Find existing executable and User Data paths for a browser key."""
    defn = BROWSER_DEFINITIONS.get(browser_key)
    if not defn:
        return None, None

    exe_path: Path | None = None
    for cand in defn["exe_candidates"]:
        p = _expand_path(cand)
        if p.exists() and p.is_file():
            exe_path = p
            break

    user_data_path: Path | None = None
    for cand in defn["user_data_candidates"]:
        p = _expand_path(cand)
        if p.exists() and p.is_dir():
            user_data_path = p
            break

    return exe_path, user_data_path


def list_local_browser_profiles() -> list[dict[str, Any]]:
    """Scan all installed local Chromium browsers and enumerate their user profiles."""
    results: list[dict[str, Any]] = []

    for b_key, defn in BROWSER_DEFINITIONS.items():
        exe_path, user_data_path = resolve_browser_paths(b_key)
        if not exe_path or not user_data_path:
            continue

        browser_name = defn["name"]
        local_state_path = user_data_path / "Local State"
        info_cache: dict[str, Any] = {}

        if local_state_path.exists():
            try:
                content = local_state_path.read_text(encoding="utf-8", errors="ignore")
                parsed = json.loads(content)
                info_cache = parsed.get("profile", {}).get("info_cache", {})
            except Exception as exc:
                logger.debug("Lỗi đọc Local State của %s: %s", browser_name, exc)

        # Collect profile directories (Default, Profile 1, Profile 2...)
        found_dirs: set[str] = set(info_cache.keys())
        # Also check directories containing Preferences
        try:
            for child in user_data_path.iterdir():
                if child.is_dir() and (child / "Preferences").exists():
                    found_dirs.add(child.name)
        except Exception:
            pass

        if not found_dirs:
            # If no profiles found, at least add Default
            found_dirs.add("Default")

        # Sort profiles: Default first, then Profile 1, 2...
        sorted_dirs = sorted(
            list(found_dirs),
            key=lambda d: (0 if d.lower() == "default" else 1, d.lower()),
        )

        for p_dir in sorted_dirs:
            meta = info_cache.get(p_dir, {})
            custom_name = str(meta.get("name") or "").strip()
            user_name = str(meta.get("user_name") or "").strip()
            display_name = custom_name if custom_name else p_dir
            if user_name and user_name not in display_name:
                display_label = f"[Local] {browser_name} — {display_name} ({user_name})"
            else:
                display_label = f"[Local] {browser_name} — {display_name}"

            profile_id = f"local_{b_key}_{p_dir.lower().replace(' ', '_')}"
            results.append({
                "id": profile_id,
                "type": "local",
                "browser_key": b_key,
                "browser_name": browser_name,
                "profile_dir": p_dir,
                "profile_name": display_name,
                "user_name": user_name,
                "exe_path": str(exe_path),
                "user_data_dir": str(user_data_path),
                "display_label": display_label,
                "raw_proxy": "Direct (Mạng Local)",
            })

    return results


def find_running_local_browser_port(browser_key: str, profile_dir: str) -> dict[str, Any] | None:
    """Check if the local browser profile is currently running with a CDP port."""
    if sys.platform != "win32":
        return None

    _, user_data_path = resolve_browser_paths(browser_key)
    if not user_data_path:
        return None

    norm_user_data = str(user_data_path).replace("/", "\\").lower()
    norm_profile = profile_dir.strip().lower()

    ps_cmd = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*--remote-debugging-port=*' } | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        stdout = res.stdout.strip()
        if not stdout:
            return None

        parsed = json.loads(stdout)
        proc_list = [parsed] if isinstance(parsed, dict) else parsed

        for proc in proc_list:
            cmdline = str(proc.get("CommandLine") or "").lower()
            if norm_user_data in cmdline and (f"--profile-directory={norm_profile}" in cmdline or norm_profile == "default"):
                port_match = re.search(r"--remote-debugging-port=(\d+)", cmdline)
                if port_match:
                    port = int(port_match.group(1))
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2.0) as vresp:
                            vdata = json.loads(vresp.read().decode("utf-8"))
                            return {
                                "port": port,
                                "endpoint_url": f"http://127.0.0.1:{port}",
                                "ws_url": str(vdata.get("webSocketDebuggerUrl") or "").strip(),
                                "process_id": proc.get("ProcessId"),
                            }
                    except Exception:
                        pass
    except Exception as exc:
        logger.debug("Lỗi khi kiểm tra tiến trình local browser: %s", exc)

    return None


def is_browser_process_running(browser_key: str) -> bool:
    """Check if any process of the specified browser executable is running."""
    exe_path, _ = resolve_browser_paths(browser_key)
    if not exe_path:
        return False
    exe_name = exe_path.name
    try:
        out = subprocess.check_output(
            f'tasklist /FI "IMAGENAME eq {exe_name}" /FO CSV /NH',
            shell=True,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3.0,
        )
        return exe_name.lower() in out.lower()
    except Exception:
        return False


def terminate_local_browser_processes(browser_key: str) -> bool:
    """Safely terminate existing non-debug processes of the browser."""
    exe_path, _ = resolve_browser_paths(browser_key)
    if not exe_path:
        return False
    exe_name = exe_path.name
    try:
        logger.info("Đang đóng các tiến trình %s cũ để kích hoạt cổng kết nối CDP...", exe_name)
        subprocess.run(
            ["taskkill", "/F", "/T", "/IM", exe_name],
            capture_output=True,
            timeout=5.0,
        )
        time.sleep(1.0)
        return True
    except Exception as exc:
        logger.warning("Không thể tắt tiến trình %s: %s", exe_name, exc)
        return False


def start_local_browser(
    browser_key: str,
    profile_dir: str = "Default",
    target_url: str = "",
    preferred_port: int | None = None,
) -> dict[str, Any]:
    """Start local browser (Cốc Cốc, Chrome, Edge) with Remote Debugging Port enabled.

    - If already running with CDP: navigates/opens target_url and returns coordinates.
    - If running without CDP: opens target_url in a new tab of the existing browser WITHOUT terminating or closing it.
    - If not running: launches with remote debugging port enabled.
    """
    exe_path, user_data_path = resolve_browser_paths(browser_key)
    if not exe_path or not user_data_path:
        raise RuntimeError(f"Không tìm thấy trình duyệt {browser_key} trên hệ thống.")

    defn = BROWSER_DEFINITIONS.get(browser_key, {})
    browser_display_name = defn.get("name", browser_key.title())

    # 1. Check if already running with CDP
    running = find_running_local_browser_port(browser_key, profile_dir)
    if running:
        port = running["port"]
        logger.info("Phát hiện %s (Profile %s) đang chạy trên port %s", browser_key, profile_dir, port)
        if target_url:
            try:
                encoded = urllib.parse.quote(target_url, safe="")
                req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{encoded}", method="PUT")
                with urllib.request.urlopen(req, timeout=3.0):
                    pass
            except Exception:
                pass
        return {
            "success": True,
            "port": port,
            "endpoint_url": running["endpoint_url"],
            "ws_url": running["ws_url"],
            "already_running": True,
            "browser_key": browser_key,
            "profile_dir": profile_dir,
        }

    # 2. If running without CDP, NEVER terminate user's browser. Open a new tab in the running browser.
    if is_browser_process_running(browser_key):
        logger.info(
            "Trình duyệt %s đang chạy chế độ thông thường. Mở thêm tab mới mà không tắt trình duyệt...",
            browser_display_name,
        )
        if target_url:
            try:
                subprocess.Popen(
                    [str(exe_path), target_url],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                logger.debug("Không thể mở tab qua process invocation: %s", e)

        raise RuntimeError(
            f"Trình duyệt {browser_display_name} đang mở sẵn ở chế độ thông thường (chưa bật cổng tự động). "
            f"Hệ thống đã tự động mở thêm tab '{target_url or 'Meta'}' trên trình duyệt của bạn mà không tắt trình duyệt. "
            f"Để chạy tự động 100%, bạn có thể bấm nút 'Mở trình duyệt' trên hệ thống."
        )

    # 3. Not running: pick a free port and launch with remote debugging port
    port = preferred_port or find_free_port()
    args = [
        str(exe_path),
        f"--user-data-dir={user_data_path}",
        f"--profile-directory={profile_dir}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if target_url:
        args.append(target_url)

    logger.info("Khởi chạy %s với cờ debug port %s: %s", browser_key, port, args)
    subprocess.Popen(
        args,
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 4. Wait for debug port to be responsive
    deadline = time.time() + 15.0
    ws_url = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ws_url = str(data.get("webSocketDebuggerUrl") or "").strip()
                if ws_url:
                    break
        except Exception:
            time.sleep(0.6)

    if not ws_url:
        raise RuntimeError(
            f"Không thể kích hoạt cổng gỡ lỗi ({port}) cho trình duyệt {browser_display_name}. "
            f"Vui lòng tắt hoàn toàn trình duyệt {browser_display_name} và thử lại."
        )

    return {
        "success": True,
        "port": port,
        "endpoint_url": f"http://127.0.0.1:{port}",
        "ws_url": ws_url,
        "already_running": False,
        "browser_key": browser_key,
        "profile_dir": profile_dir,
    }


@asynccontextmanager
async def local_browser_session(
    browser_key: str,
    profile_dir: str = "Default",
    target_url: str = "",
) -> AsyncGenerator[Any, None]:
    """Async context manager that connects Playwright via CDP to a local browser (Cốc Cốc, Chrome, etc.).

    CRITICAL: Never terminates or closes the user's host browser on exit.
    Only closes temporary pages and disconnects Playwright CDP client.
    """
    from playwright.async_api import async_playwright

    launch_info = await asyncio.to_thread(
        start_local_browser,
        browser_key,
        profile_dir,
        target_url=target_url,
    )

    port = launch_info.get("port")
    endpoint_url = launch_info.get("ws_url") or f"http://127.0.0.1:{port}"

    playwright_cm = async_playwright()
    playwright = await playwright_cm.start()
    browser = None
    try:
        browser = await playwright.chromium.connect_over_cdp(endpoint_url, timeout=10000)
        contexts = browser.contexts
        context = contexts[0] if contexts else await browser.new_context()
        yield context, browser
    finally:
        # DO NOT call browser.close() because it sends Browser.close to the user's browser!
        # Stopping playwright gracefully disconnects the CDP WebSocket connection without closing the browser.
        try:
            await playwright.stop()
        except Exception:
            pass
