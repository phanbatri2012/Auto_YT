"""GPM-Login Local API (v3 & v1 compatible) Client and Playwright CDP integration.

This module provides complete management of GPM-Login antidetect browser profiles,
enabling isolated proxy IPs, browser fingerprints, and independent sessions per YouTube channel.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, AsyncGenerator

from auto_yt.paths import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_GPM_API_URL = "http://127.0.0.1:19995"
GPM_CONFIG_PATH = DATA_DIR / "gpm_config.json"
DEFAULT_TIMEOUT_SECONDS = 15.0


class GpmError(RuntimeError):
    """Base exception for all GPM-Login operations."""


class GpmConnectionError(GpmError):
    """Raised when GPM-Login Local API server is unreachable or offline."""


class GpmProfileNotFoundError(GpmError):
    """Raised when a specified GPM profile ID does not exist."""


class GpmProfileLaunchError(GpmError):
    """Raised when GPM fails to start a profile browser."""


def get_gpm_config() -> dict[str, Any]:
    """Load GPM configuration from disk or return safe defaults."""
    if not GPM_CONFIG_PATH.exists():
        return {
            "api_url": DEFAULT_GPM_API_URL,
            "auto_stop_on_finish": True,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        }
    try:
        data = json.loads(GPM_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}

    api_url = str(data.get("api_url") or "").strip().rstrip("/")
    if not api_url:
        api_url = DEFAULT_GPM_API_URL

    return {
        "api_url": api_url,
        "auto_stop_on_finish": bool(data.get("auto_stop_on_finish", True)),
        "timeout_seconds": float(data.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
    }


def save_gpm_config(
    api_url: str | None = None,
    auto_stop_on_finish: bool | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Save updated GPM configuration atomically to disk."""
    config = get_gpm_config()
    if api_url is not None:
        cleaned_url = str(api_url).strip().rstrip("/")
        if not cleaned_url.startswith("http://") and not cleaned_url.startswith("https://"):
            raise ValueError("GPM API URL phải bắt đầu bằng http:// hoặc https://")
        config["api_url"] = cleaned_url
    if auto_stop_on_finish is not None:
        config["auto_stop_on_finish"] = bool(auto_stop_on_finish)
    if timeout_seconds is not None:
        config["timeout_seconds"] = max(2.0, min(float(timeout_seconds), 120.0))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = GPM_CONFIG_PATH.with_suffix(".tmp")
    try:
        temp_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(GPM_CONFIG_PATH)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise GpmError(f"Không thể lưu file cấu hình GPM: {exc}") from exc

    return config


def _execute_raw_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> tuple[int, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "AutoYT-GPMClient/1.0",
    }
    body_bytes = None
    if payload is not None:
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        return resp.status, data.decode("utf-8", errors="replace")


def _request_gpm_api(
    endpoint: str,
    *,
    api_url: str | None = None,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Execute an HTTP request against the GPM-Login Local API with automatic v3/v1 fallback."""
    config = get_gpm_config()
    base_url = (api_url or config["api_url"]).strip().rstrip("/")
    timeout_val = timeout or config["timeout_seconds"]

    clean_endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"

    query_str = ""
    if params:
        query_str = urllib.parse.urlencode({k: str(v) for k, v in params.items() if v is not None})
        if query_str:
            query_str = f"?{query_str}"

    # Priority 1: If endpoint already has explicit prefix, try as-is
    candidate_endpoints = []
    if clean_endpoint.startswith("/api/v3") or clean_endpoint.startswith("/api/v1"):
        candidate_endpoints.append(clean_endpoint)
    else:
        # Try /api/v3 first (standard GPM 3.x), then /api/v1 (older GPM)
        candidate_endpoints.append(f"/api/v3{clean_endpoint}")
        candidate_endpoints.append(f"/api/v1{clean_endpoint}")

    last_error: Exception | None = None
    for candidate in candidate_endpoints:
        url = f"{base_url}{candidate}{query_str}"
        try:
            status, text = _execute_raw_request(url, method=method, payload=payload, timeout=timeout_val)
            trimmed = text.strip()
            # GPM returns plain "GPM-Login" if route not matched
            if trimmed == "GPM-Login":
                continue
            parsed = json.loads(trimmed)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"success": True, "data": parsed, "message": "OK"}
        except urllib.error.HTTPError as exc:
            error_body = ""
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
                parsed_err = json.loads(error_body)
                if isinstance(parsed_err, dict) and parsed_err.get("message"):
                    error_body = str(parsed_err["message"])
            except Exception:
                pass
            if exc.code == 404:
                last_error = GpmProfileNotFoundError(f"GPM không tìm thấy tài nguyên: {error_body or f'HTTP 404 ({url})'}")
            else:
                last_error = GpmError(f"GPM API HTTP {exc.code}: {error_body or exc.reason}")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise GpmConnectionError(
                f"Không kết nối được tới GPM-Login tại {base_url}. "
                "Hãy đảm bảo ứng dụng GPM-Login đang bật và Local API được kích hoạt."
            ) from exc
        except json.JSONDecodeError as exc:
            last_error = GpmError(f"GPM API trả về dữ liệu không phải JSON: {exc}")

    if last_error:
        raise last_error
    raise GpmError(f"Không tìm thấy endpoint hợp lệ trên GPM cho {endpoint}")


def check_gpm_connection(api_url: str | None = None) -> dict[str, Any]:
    """Test connection to GPM-Login Local API and return diagnostic info."""
    config = get_gpm_config()
    target_url = (api_url or config["api_url"]).strip().rstrip("/")
    try:
        profiles_info = list_gpm_profiles(page_size=200, api_url=target_url)
        total = profiles_info.get("total", 0)
        sender = str(profiles_info.get("sender") or "GPM-Login v3")
        return {
            "online": True,
            "api_url": target_url,
            "total_profiles": total,
            "sender": sender,
            "message": f"Kết nối GPM-Login thành công. Tìm thấy {total} profiles.",
        }
    except GpmConnectionError as exc:
        return {
            "online": False,
            "api_url": target_url,
            "total_profiles": 0,
            "sender": "",
            "message": str(exc),
        }
    except Exception as exc:
        return {
            "online": False,
            "api_url": target_url,
            "total_profiles": 0,
            "sender": "",
            "message": f"Lỗi kiểm tra GPM: {exc}",
        }


def list_gpm_profiles(
    search: str = "",
    page: int = 1,
    page_size: int = 200,
    sort: int = 0,
    api_url: str | None = None,
) -> dict[str, Any]:
    """Retrieve list of profiles from GPM-Login with search support."""
    resp = _request_gpm_api("/profiles", api_url=api_url)
    data = resp.get("data")
    items = []

    raw_items = []
    if isinstance(data, list):
        raw_items = data
    elif isinstance(data, dict):
        raw_items = data.get("data") or []

    search_lower = str(search or "").strip().lower()

    for item in raw_items:
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            profile_id = str(item.get("id") or "")
            raw_proxy = str(item.get("raw_proxy") or "")
            browser_info = item.get("browser")
            browser_name = str(item.get("browser_type") or (browser_info.get("name") if isinstance(browser_info, dict) else "Chrome"))
            browser_version = str(item.get("browser_version") or (browser_info.get("version") if isinstance(browser_info, dict) else ""))

            if search_lower and (search_lower not in name.lower() and search_lower not in raw_proxy.lower()):
                continue

            items.append({
                "id": profile_id,
                "name": name,
                "group_id": str(item.get("group_id") or ""),
                "raw_proxy": raw_proxy,
                "browser_name": browser_name,
                "browser_version": browser_version,
                "os": str(item.get("os") or "windows"),
                "note": str(item.get("note") or ""),
                "created_at": str(item.get("created_at") or ""),
                "tags": item.get("tags") or [],
            })

    total = data.get("total") if isinstance(data, dict) and "total" in data and not search_lower else len(items)
    return {
        "current_page": page,
        "per_page": page_size,
        "total": total,
        "last_page": 1,
        "items": items,
        "sender": str(resp.get("sender") or "GPM-Login v3"),
    }


def find_running_gpm_profile_coordinates(
    profile_id: str, api_url: str | None = None
) -> dict[str, Any] | None:
    """Detect CDP coordinates of an already running GPM profile on Windows."""
    clean_id = str(profile_id).strip()
    if not clean_id:
        return None
    profile_path = ""
    try:
        detail = get_gpm_profile_detail(clean_id, api_url=api_url)
        profile_path = str(detail.get("profile_path") or detail.get("id") or "").strip()
    except Exception:
        profile_path = clean_id

    if sys.platform == "win32":
        try:
            ps_cmd = (
                "$ErrorActionPreference='SilentlyContinue'; "
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -like '*chrome*' -or $_.Name -like '*gpm*' -or $_.Name -like '*msedge*' -or $_.Name -like '*coccoc*' } | "
                "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=6.0,
            )
            stdout = res.stdout.strip()
            if stdout:
                parsed = json.loads(stdout)
                proc_list = [parsed] if isinstance(parsed, dict) else parsed
                for proc in proc_list:
                    cmdline = str(proc.get("CommandLine") or "")
                    if (profile_path and profile_path in cmdline) or (clean_id in cmdline):
                        port_match = re.search(r"--remote-debugging-port=(\d+)", cmdline)
                        if port_match:
                            port = int(port_match.group(1))
                            try:
                                with urllib.request.urlopen(
                                    f"http://127.0.0.1:{port}/json/version", timeout=2.0
                                ) as vresp:
                                    vdata = json.loads(vresp.read().decode("utf-8"))
                                    return {
                                        "remote_debugging_port": port,
                                        "selenium_remote_debug_address": f"127.0.0.1:{port}",
                                        "websocket_debugging_url": str(
                                            vdata.get("webSocketDebuggerUrl") or ""
                                        ).strip(),
                                        "profile_id": clean_id,
                                        "profile_path": profile_path,
                                        "process_id": proc.get("ProcessId"),
                                        "status": "already_open",
                                    }
                            except Exception:
                                pass
        except Exception as exc:
            logger.debug("Lỗi khi quét tiến trình GPM profile đang chạy: %s", exc)
    return None


def open_tab_in_running_gpm_process(
    profile_id: str,
    url: str,
    api_url: str | None = None,
) -> dict[str, Any]:
    """Open a URL in a new tab within an already running GPM profile on Windows.

    Supports both CDP-enabled browsers and manually opened browsers (via Chromium Single-Instance IPC).
    """
    clean_id = str(profile_id).strip()
    target_url = str(url).strip()
    if not clean_id or not target_url:
        raise ValueError("Profile ID và URL không được để trống.")

    profile_path = ""
    try:
        detail = get_gpm_profile_detail(clean_id, api_url=api_url)
        profile_path = str(detail.get("profile_path") or detail.get("id") or "").strip()
    except Exception:
        profile_path = clean_id

    if sys.platform != "win32":
        raise GpmError("Mở tab trên profile đang chạy chỉ hỗ trợ trên môi trường Windows.")

    ps_cmd = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -like '*--user-data-dir=*' -and ($_.Name -like '*chrome*' -or $_.Name -like '*gpm*') } | "
        "Select-Object ProcessId, ExecutablePath, CommandLine | ConvertTo-Json -Compress"
    )
    res = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        timeout=6.0,
    )
    stdout = res.stdout.strip()
    if not stdout:
        raise GpmError(f"Không tìm thấy tiến trình GPM Profile {clean_id} đang chạy.")

    try:
        data = json.loads(stdout)
        proc_list = [data] if isinstance(data, dict) else data

        matching_proc = None
        for proc in proc_list:
            cmdline = str(proc.get("CommandLine") or "")
            if (profile_path and profile_path in cmdline) or (clean_id in cmdline):
                matching_proc = proc
                if "--type=" not in cmdline:
                    break

        if not matching_proc:
            raise GpmError(f"Không tìm thấy cửa sổ trình duyệt cho GPM Profile {clean_id}.")

        cmdline = str(matching_proc.get("CommandLine") or "")
        exe_path = str(matching_proc.get("ExecutablePath") or "").strip()
        if not exe_path:
            match_exe = re.match(r'^\s*"([^"]+)"', cmdline) or re.match(r'^\s*([^\s]+)', cmdline)
            if match_exe:
                exe_path = match_exe.group(1)

        # 1. If CDP port exists, try /json/new
        port_match = re.search(r"--remote-debugging-port=(\d+)", cmdline)
        if port_match:
            port = int(port_match.group(1))
            try:
                encoded_url = urllib.parse.quote(target_url, safe="")
                new_tab_url = f"http://127.0.0.1:{port}/json/new?{encoded_url}"
                req = urllib.request.Request(new_tab_url, method="PUT")
                try:
                    with urllib.request.urlopen(req, timeout=3.0) as resp:
                        tab_data = json.loads(resp.read().decode("utf-8"))
                        tab_id = tab_data.get("id")
                        if tab_id:
                            activate_url = f"http://127.0.0.1:{port}/json/activate/{tab_id}"
                            with urllib.request.urlopen(activate_url, timeout=3.0):
                                pass
                        return {
                            "success": True,
                            "profile_id": clean_id,
                            "url": target_url,
                            "method": "cdp_http",
                            "message": f"Đã mở tab mới trong Profile GPM {clean_id}",
                        }
                except Exception:
                    get_req = urllib.request.Request(new_tab_url, method="GET")
                    with urllib.request.urlopen(get_req, timeout=3.0) as resp:
                        tab_data = json.loads(resp.read().decode("utf-8"))
                        tab_id = tab_data.get("id")
                        if tab_id:
                            activate_url = f"http://127.0.0.1:{port}/json/activate/{tab_id}"
                            with urllib.request.urlopen(activate_url, timeout=3.0):
                                pass
                        return {
                            "success": True,
                            "profile_id": clean_id,
                            "url": target_url,
                            "method": "cdp_http",
                            "message": f"Đã mở tab mới trong Profile GPM {clean_id}",
                        }
            except Exception as exc:
                logger.debug("Mở qua CDP port %s không thành công (%s), chuyển sang IPC...", port, exc)

        # 2. Open via Chromium single-instance IPC
        udd_match = re.search(r'--user-data-dir="([^"]+)"', cmdline) or re.search(r"--user-data-dir=([^\s]+)", cmdline)
        if exe_path and udd_match:
            user_data_dir = udd_match.group(1)
            subprocess.Popen(
                [exe_path, f"--user-data-dir={user_data_dir}", target_url],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {
                "success": True,
                "profile_id": clean_id,
                "url": target_url,
                "method": "chromium_ipc",
                "message": f"Đã mở tab mới trong Profile GPM {clean_id}",
            }

        raise GpmError(f"Không thể xác định đường dẫn thực thi trình duyệt cho Profile {clean_id}.")
    except Exception as exc:
        if isinstance(exc, GpmError):
            raise
        raise GpmError(f"Lỗi khi mở tab trong profile GPM đang chạy: {exc}") from exc


def get_gpm_profile_detail(profile_id: str, api_url: str | None = None) -> dict[str, Any]:
    """Get full details of a single GPM profile."""
    clean_id = str(profile_id).strip()
    if not clean_id:
        raise ValueError("Profile ID không được để trống.")
    resp = _request_gpm_api(f"/profiles/{clean_id}", api_url=api_url)
    data = resp.get("data") or {}
    if not isinstance(data, dict):
        raise GpmProfileNotFoundError(f"Không tìm thấy profile {clean_id}")
    return data


# Backwards compatibility alias
get_gpm_profile = get_gpm_profile_detail


def start_gpm_profile(
    profile_id: str,
    *,
    remote_debugging_port: int | None = None,
    window_scale: float | None = None,
    window_pos: str | None = None,
    window_size: str | None = None,
    skip_proxy_check: bool = False,
    addition_args: str | None = None,
    api_url: str | None = None,
) -> dict[str, Any]:
    """Start a GPM profile browser and return CDP connection coordinates."""
    clean_id = str(profile_id).strip()
    if not clean_id:
        raise ValueError("Profile ID không được để trống.")

    params: dict[str, Any] = {}
    if remote_debugging_port is not None and remote_debugging_port > 0:
        params["remote_debugging_port"] = remote_debugging_port
    if window_scale is not None:
        params["window_scale"] = window_scale
        params["win_scale"] = window_scale
    if window_pos:
        params["window_pos"] = window_pos
        params["win_pos"] = window_pos
    if window_size:
        params["window_size"] = window_size
        params["win_size"] = window_size
    if skip_proxy_check:
        params["skip_proxy_check"] = "true"
    if addition_args:
        params["addition_args"] = addition_args
        params["add_args"] = addition_args

    logger.info("Khởi chạy GPM Profile %s (params=%s)", clean_id, params)
    resp = _request_gpm_api(f"/profiles/start/{clean_id}", api_url=api_url, params=params, timeout=45.0)
    data = resp.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    # Robust normalization of debugging coordinates from GPM v3 / v1
    raw_addr = str(
        data.get("selenium_remote_debug_address")
        or data.get("remote_debugging_address")
        or ""
    ).strip()
    raw_port = data.get("remote_debugging_port") or data.get("remote_port") or data.get("port")
    if not raw_port and ":" in raw_addr:
        try:
            raw_port = int(raw_addr.split(":")[-1])
        except (ValueError, IndexError):
            pass
    elif raw_port:
        try:
            raw_port = int(raw_port)
        except (ValueError, TypeError):
            pass

    raw_ws = str(
        data.get("websocket_debugging_url")
        or data.get("wsUrl")
        or data.get("webSocketDebuggerUrl")
        or ""
    ).strip()

    if raw_port:
        data["remote_debugging_port"] = raw_port
    if raw_ws:
        data["websocket_debugging_url"] = raw_ws
    if raw_addr:
        data["selenium_remote_debug_address"] = raw_addr

    if not (data.get("websocket_debugging_url") or data.get("remote_debugging_port") or data.get("selenium_remote_debug_address")):
        running_info = find_running_gpm_profile_coordinates(clean_id, api_url=api_url)
        if running_info:
            logger.info("Phát hiện GPM Profile %s đang mở sẵn với port %s", clean_id, running_info.get("remote_debugging_port"))
            return running_info
        if str(resp.get("message") or "") == "ALREADY_OPEN":
            logger.info("GPM Profile %s đã mở sẵn (chế độ thường/không có CDP port)", clean_id)
            return {
                "success": True,
                "profile_id": clean_id,
                "status": "already_open",
                "already_running_no_cdp": True,
                "message": "Profile GPM đã mở sẵn",
            }
        msg = resp.get("message", "Unknown error")
        raise GpmProfileLaunchError(f"GPM không thể mở profile: {msg}")
    return data


def stop_gpm_profile(profile_id: str, api_url: str | None = None) -> bool:
    """Stop/close a running GPM profile browser."""
    clean_id = str(profile_id).strip()
    if not clean_id:
        return False
    logger.info("Đóng GPM Profile %s", clean_id)
    try:
        resp = _request_gpm_api(f"/profiles/stop/{clean_id}", api_url=api_url, timeout=15.0)
        return bool(resp.get("success", True))
    except Exception as exc:
        logger.warning("Lỗi khi đóng GPM profile %s: %s", clean_id, exc)
        return False


@asynccontextmanager
async def gpm_browser_session(
    profile_id: str,
    *,
    auto_stop: bool | None = None,
    skip_proxy_check: bool = False,
    addition_args: str | None = None,
    api_url: str | None = None,
) -> AsyncGenerator[Any, None]:
    """Async context manager that starts a GPM profile, connects Playwright via CDP,

    and guarantees safe cleanup and closing.
    """
    from playwright.async_api import async_playwright

    config = get_gpm_config()
    should_auto_stop = auto_stop if auto_stop is not None else config["auto_stop_on_finish"]

    launch_info = await asyncio.to_thread(
        start_gpm_profile,
        profile_id,
        skip_proxy_check=skip_proxy_check,
        addition_args=addition_args,
        api_url=api_url,
    )

    ws_url = str(launch_info.get("websocket_debugging_url") or "").strip()
    remote_port = launch_info.get("remote_debugging_port")
    if not remote_port and not ws_url:
        raise GpmProfileLaunchError(
            f"Không tìm thấy cổng kết nối CDP cho Profile GPM '{profile_id}'. "
            "Hãy đảm bảo Profile đã được bật hoặc khởi chạy qua hệ thống."
        )
    endpoint_url = ws_url if ws_url else f"http://127.0.0.1:{remote_port}"

    playwright_cm = async_playwright()
    playwright = await playwright_cm.start()
    browser = None
    try:
        browser = await playwright.chromium.connect_over_cdp(endpoint_url, timeout=10000)
        contexts = browser.contexts
        if contexts:
            context = contexts[0]
        else:
            context = await browser.new_context()

        yield context, browser

    finally:
        if browser is not None and should_auto_stop:
            try:
                await browser.close()
            except Exception as exc:
                logger.debug("Lỗi khi đóng kết nối CDP browser: %s", exc)
        try:
            await playwright.stop()
        except Exception:
            pass

        if should_auto_stop:
            await asyncio.to_thread(stop_gpm_profile, profile_id, api_url=api_url)
