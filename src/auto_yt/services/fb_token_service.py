"""Facebook Page Access Token Auto-Extractor & Exchanger Service.

Uses Playwright CDP via channel_browser_session to automatically access Meta Graph API Explorer
or Meta Business Suite from the user's authenticated browser profile (Cốc Cốc, Chrome, Edge, GPM),
extract the User Access Token, and exchange it with Meta Graph API for Page Access Tokens
without requiring manual copy-paste.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from auto_yt.services import database as db, security_logging
from auto_yt.services.channel_scanner_service import channel_browser_session, parse_profile_target
from auto_yt.services.fb_crossposter_service import GRAPH_API_BASE, sanitize_fb_token
from auto_yt.services.proxy_utils import create_proxy_opener

logger = logging.getLogger(__name__)

def _build_opener_for_profile(profile_id: str) -> urllib.request.OpenerDirector:
    """Build an urllib opener configured with proxy if profile uses one."""
    parsed = parse_profile_target(profile_id)
    if parsed.get("type") == "local":
        return urllib.request.build_opener()
    raw_proxy = parsed.get("proxy_info")
    return create_proxy_opener(raw_proxy, require_proxy=True)


def _public_page_summary(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "page_id": str(page.get("page_id") or ""),
        "name": str(page.get("name") or ""),
        "category": str(page.get("category") or ""),
        "link": str(page.get("link") or ""),
        "picture": str(page.get("picture") or ""),
        "token_configured": bool(page.get("access_token")),
    }


async def extract_permanent_fb_tokens(
    profile_id: str,
    target_page_id: str = "",
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Extract a token and store it only when it belongs to the requested Page."""
    logger.info("Bắt đầu trích xuất Page Access Token từ Profile: %s", profile_id)
    target_pid = (target_page_id or "").strip()
    if not target_pid:
        return {
            "success": False,
            "logged_in": False,
            "pages": [],
            "message": "Vui lòng nhập Fanpage Page ID trước khi lấy Page Access Token.",
        }

    try:
        async with channel_browser_session(profile_id, target_url="https://developers.facebook.com/tools/explorer/") as (context, _browser, p_info):
            page = await context.new_page()
            try:
                # Navigate to Graph API Explorer
                await page.goto(
                    "https://developers.facebook.com/tools/explorer/",
                    wait_until="domcontentloaded",
                    timeout=int(timeout_seconds * 1000),
                )
                await asyncio.sleep(3.5)

                current_url = page.url
                if "login" in current_url or "checkpoint" in current_url:
                    return {
                        "success": False,
                        "logged_in": False,
                        "pages": [],
                        "message": "Chưa đăng nhập Facebook trên Profile này. Vui lòng mở trình duyệt và đăng nhập tài khoản Facebook trước.",
                    }

                # 1. Try extracting access token from Graph Explorer DOM / Scripts
                js_extract_token = """
                () => {
                    // Method 1: Look for textarea / input containing EAAG or EAA token
                    const inputs = Array.from(document.querySelectorAll('input, textarea'));
                    for (const el of inputs) {
                        const val = (el.value || '').trim();
                        if (val.startsWith('EAAG') || val.startsWith('EAA') || (val.length > 50 && val.startsWith('EA'))) {
                            return { token: val, source: 'dom_input' };
                        }
                    }

                    // Method 2: Look for token in React fiber or window global scripts
                    const scripts = Array.from(document.querySelectorAll('script')).map(s => s.innerText);
                    for (const s of scripts) {
                        const m = s.match(/"accessToken":\s*"([A-Za-z0-9]+)"/) || s.match(/access_token=([A-Za-z0-9]+)/);
                        if (m && m[1] && m[1].length > 50 && m[1].startsWith('EA')) {
                            return { token: m[1], source: 'script_regex' };
                        }
                    }

                    // Method 3: Check localStorage / sessionStorage
                    for (let i = 0; i < localStorage.length; i++) {
                        const k = localStorage.key(i);
                        const v = localStorage.getItem(k) || '';
                        if (v.startsWith('EAAG') || (v.length > 50 && v.startsWith('EA'))) {
                            return { token: v, source: 'localStorage' };
                        }
                    }

                    return null;
                }
                """
                token_result = await page.evaluate(js_extract_token)

                # If not immediately visible, try clicking 'Generate Access Token' if button exists
                if not token_result:
                    try:
                        # Look for Generate Token or Submit button
                        gen_btn = page.locator("button:has-text('Generate Access Token'), button:has-text('Submit'), button:has-text('Lấy mã'), button:has-text('Tạo mã')").first
                        if await gen_btn.is_visible():
                            await gen_btn.click()
                            await asyncio.sleep(2.5)
                            token_result = await page.evaluate(js_extract_token)
                    except Exception as click_err:
                        logger.debug("Could not click generate button: %s", click_err)

                # Method 4: Fallback to Meta Business Suite / Account Session if Graph Explorer token is not accessible
                if not token_result or not token_result.get("token"):
                    # Try navigating to business settings / pages to check login
                    await page.goto("https://business.facebook.com/latest/home", wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(2.0)
                    if "login" in page.url:
                        return {
                            "success": False,
                            "logged_in": False,
                            "pages": [],
                            "message": "Chưa đăng nhập Facebook trên Profile này. Hãy mở trình duyệt và đăng nhập trước.",
                        }

                user_token = (token_result and token_result.get("token")) or ""
                if not user_token:
                    return {
                        "success": False,
                        "logged_in": True,
                        "pages": [],
                        "message": "Đã kết nối Facebook nhưng chưa kích hoạt Meta Graph API Explorer. Bạn chỉ cần mở link https://developers.facebook.com/tools/explorer/ trên trình duyệt 1 lần duy nhất để tạo Access Token.",
                    }

                logger.info("Trích xuất thành công Token từ trình duyệt (Độ dài: %d ký tự)", len(user_token))

                # 2. First check if extracted token is ALREADY a direct Page Access Token
                direct_info = inspect_token_direct(user_token, profile_id=profile_id)
                direct_page_id = str(direct_info.get("id") or "")
                direct_page_name = str(direct_info.get("name") or "")

                if direct_page_id and direct_page_id == target_pid:
                    logger.info("Token trích xuất được thuộc Fanpage %s (%s)", direct_page_name, direct_page_id)
                    matched_page = {
                        "page_id": direct_page_id,
                        "name": direct_page_name,
                        "access_token": user_token,
                        "category": direct_info.get("category", ""),
                        "link": direct_info.get("link", f"https://www.facebook.com/{direct_page_id}"),
                    }
                    existing_settings = db.get_fb_crossposter_settings(direct_page_id)
                    db.save_fb_crossposter_settings({
                        **existing_settings,
                        "target_access_token": user_token,
                        "target_fb_page_name": direct_page_name or existing_settings.get("target_fb_page_name"),
                        "target_fb_page_id": direct_page_id,
                        "target_gpm_profile_id": profile_id,
                    }, page_id=direct_page_id)

                    return {
                        "success": True,
                        "logged_in": True,
                        "total_pages": 1,
                        "matched_page": _public_page_summary(matched_page),
                        "pages": [_public_page_summary(matched_page)],
                        "message": f"✅ Đã lấy và lưu Page Access Token cho Fanpage '{direct_page_name}'!",
                    }

                # 3. Exchange User Token with Meta Graph API for Permanent Page Access Tokens
                pages_list = fetch_permanent_page_tokens_from_user_token(
                    user_token=user_token,
                    profile_id=profile_id,
                    target_page_id=target_pid,
                )

                if not pages_list:
                    tip_msg = (
                        "Ứng dụng Meta 'chatbo0t' hiện chưa có quyền 'pages_show_list' hoặc đang bị tắt quyền API trên Meta Developers. "
                        "Trên màn hình Graph Explorer đang mở, bạn chỉ cần bấm vào ô 'Mã người dùng' -> Chọn 'Nhận mã truy cập Trang' -> Chọn Fanpage của bạn, "
                        "rồi bấm lại nút '1-Click Lấy Token'."
                    )
                    return {
                        "success": False,
                        "logged_in": True,
                        "pages": [],
                        "message": tip_msg,
                    }

                matched_page = next(
                    (p for p in pages_list if str(p.get("page_id")) == target_pid),
                    None,
                )

                if not matched_page:
                    return {
                        "success": False,
                        "logged_in": True,
                        "total_pages": len(pages_list),
                        "pages": [],
                        "message": "Token không thuộc Fanpage Page ID đã chọn.",
                    }

                # Auto-save matched token to database if target_page_id matched
                if matched_page.get("access_token"):
                    clean_page_token = sanitize_fb_token(matched_page["access_token"])
                    existing_settings = db.get_fb_crossposter_settings(target_pid)
                    db.save_fb_crossposter_settings({
                        **existing_settings,
                        "target_access_token": clean_page_token,
                        "target_fb_page_name": matched_page.get("name") or existing_settings.get("target_fb_page_name"),
                        "target_fb_page_id": target_pid,
                        "target_gpm_profile_id": profile_id,
                    }, page_id=target_pid)
                    logger.info("Đã tự động lưu Permanent Token cho Fanpage %s (%s)", matched_page.get("name"), target_pid)

                return {
                    "success": True,
                    "logged_in": True,
                    "total_pages": 1,
                    "matched_page": _public_page_summary(matched_page),
                    "pages": [_public_page_summary(matched_page)],
                    "message": "✅ Page Access Token đã được lưu an toàn trong hệ thống.",
                }

            finally:
                try:
                    await page.close()
                except Exception:
                    pass

    except Exception as exc:
        safe_error = security_logging.redact_sensitive(exc)
        logger.error("Lỗi trong quá trình trích xuất Facebook token: %s", safe_error)
        return {
            "success": False,
            "error": safe_error,
            "message": safe_error,
        }


def inspect_token_direct(token: str, profile_id: str = "") -> dict[str, Any]:
    """Inspect if the token is already a valid Page Access Token using Graph API /me."""
    if not token:
        return {}
    opener = _build_opener_for_profile(profile_id)
    url = f"{GRAPH_API_BASE}/me?fields=id,name,category,link"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NexusStudio/1.0",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with opener.open(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug(
            "Failed /me inspect for token: %s",
            security_logging.redact_sensitive(exc),
        )
        return {}


def fetch_permanent_page_tokens_from_user_token(
    user_token: str,
    profile_id: str = "",
    target_page_id: str = "",
) -> list[dict[str, Any]]:
    """Call Meta Graph API /me/accounts to retrieve Page Access Tokens.
    
    If target_page_id is provided and not returned by /me/accounts (e.g. New Pages Experience),
    directly queries /{target_page_id}?fields=id,name,access_token,category,link.
    """
    clean_token = sanitize_fb_token(user_token)
    if not clean_token:
        return []

    opener = _build_opener_for_profile(profile_id)
    url = f"{GRAPH_API_BASE}/me/accounts?fields=id,name,access_token,category,link,picture&limit=100"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "NexusStudio/1.0",
            "Authorization": f"Bearer {clean_token}",
        },
    )

    results: list[dict[str, Any]] = []
    try:
        with opener.open(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("data") or []
            for item in items:
                page_id = str(item.get("id") or "").strip()
                page_name = item.get("name") or ""
                p_token = sanitize_fb_token(item.get("access_token"))
                if page_id and p_token:
                    results.append({
                        "page_id": page_id,
                        "name": page_name,
                        "access_token": p_token,
                        "category": item.get("category", ""),
                        "link": item.get("link", f"https://www.facebook.com/{page_id}"),
                        "picture": item.get("picture", {}).get("data", {}).get("url", ""),
                    })
    except urllib.error.HTTPError as err:
        err_msg = err.read().decode("utf-8", errors="ignore")
        logger.error(
            "Failed to fetch /me/accounts (%s): %s",
            err.code,
            security_logging.redact_sensitive(err_msg),
        )
    except Exception as exc:
        logger.error(
            "Error connecting to Meta Graph API for accounts: %s",
            security_logging.redact_sensitive(exc),
        )

    # Fallback: If target_page_id was requested but not found in /me/accounts, try querying /{target_page_id} directly
    clean_target_pid = str(target_page_id or "").strip()
    if clean_target_pid and not any(r.get("page_id") == clean_target_pid for r in results):
        try:
            direct_url = f"{GRAPH_API_BASE}/{clean_target_pid}?fields=id,name,access_token,category,link,picture"
            direct_req = urllib.request.Request(
                direct_url,
                headers={
                    "User-Agent": "NexusStudio/1.0",
                    "Authorization": f"Bearer {clean_token}",
                },
            )
            with opener.open(direct_req, timeout=15) as d_resp:
                d_data = json.loads(d_resp.read().decode("utf-8"))
                d_pid = str(d_data.get("id") or "").strip()
                d_tok = sanitize_fb_token(d_data.get("access_token") or clean_token)
                if d_pid:
                    results.append({
                        "page_id": d_pid,
                        "name": d_data.get("name") or f"Fanpage {d_pid}",
                        "access_token": d_tok,
                        "category": d_data.get("category", ""),
                        "link": d_data.get("link", f"https://www.facebook.com/{d_pid}"),
                        "picture": d_data.get("picture", {}).get("data", {}).get("url", ""),
                    })
                    logger.info("Retrieved Page token directly for %s (%s)", d_data.get("name"), d_pid)
        except Exception as d_exc:
            logger.debug(
                "Direct page fetch fallback failed for %s: %s",
                clean_target_pid,
                security_logging.redact_sensitive(d_exc),
            )

    return results


def exchange_to_permanent_token(
    input_token: str,
    app_id: str = "",
    app_secret: str = "",
    profile_id: str = "",
) -> dict[str, Any]:
    """Exchange a user token and retrieve its available Page Access Tokens."""
    clean_token = sanitize_fb_token(input_token)
    if not clean_token:
        raise ValueError("Vui lòng cung cấp Access Token hợp lệ.")

    opener = _build_opener_for_profile(profile_id)

    # 1. If App ID and App Secret are provided, upgrade to 60-day Long-Lived User Token first
    effective_user_token = clean_token
    if app_id and app_secret:
        exchange_url = f"{GRAPH_API_BASE}/oauth/access_token"
        exchange_body = urllib.parse.urlencode({
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": clean_token,
        }).encode("utf-8")
        req = urllib.request.Request(
            exchange_url,
            data=exchange_body,
            headers={
                "User-Agent": "NexusStudio/1.0",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with opener.open(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("access_token"):
                    effective_user_token = data["access_token"]
                    logger.info("Đã nâng cấp lên Long-Lived User Token thành công")
        except Exception as exc:
            logger.warning(
                "Could not exchange with app credentials, trying direct /me/accounts: %s",
                security_logging.redact_sensitive(exc),
            )

    # 2. Get Permanent Page Access Tokens via /me/accounts
    pages = fetch_permanent_page_tokens_from_user_token(effective_user_token, profile_id=profile_id)
    if not pages:
        # Check if the token itself is already a direct Page Access Token
        test_url = f"{GRAPH_API_BASE}/me?fields=id,name"
        try:
            test_request = urllib.request.Request(
                test_url,
                headers={"Authorization": f"Bearer {clean_token}"},
            )
            with opener.open(test_request, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "success": True,
                    "is_direct_page_token": True,
                    "pages": [{
                        "page_id": str(data.get("id")),
                        "name": data.get("name"),
                        "access_token": clean_token,
                    }],
                    "message": f"✅ Token hợp lệ cho Fanpage: {data.get('name')}",
                }
        except Exception:
            pass
        raise RuntimeError("Token không hợp lệ hoặc tài khoản không có quyền quản trị Fanpage nào.")

    return {
        "success": True,
        "total_pages": len(pages),
        "pages": pages,
        "message": f"✅ Đã lấy thành công {len(pages)} Page Access Token!",
    }
