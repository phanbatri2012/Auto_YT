"""YouTube Studio web automation helpers executed inside dedicated GPM-Login profile sessions.

All operations execute strictly via Playwright CDP connected to the GPM profile,
guaranteeing 100% traffic isolation over the profile's dedicated proxy IP.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
import urllib.parse
import urllib.request

from auto_yt.services.gpm_service import (
    gpm_browser_session,
    open_tab_in_running_gpm_process,
    start_gpm_profile,
)

logger = logging.getLogger(__name__)


class GpmAutomationError(RuntimeError):
    """Raised when an automation action fails inside a GPM browser session."""


async def verify_youtube_login(profile_id: str, timeout_seconds: float = 20.0) -> dict[str, Any]:
    """Check if the given GPM profile has an active YouTube Studio session."""
    logger.info("Kiểm tra đăng nhập YouTube trên GPM profile %s", profile_id)
    async with gpm_browser_session(profile_id, auto_stop=True) as (context, _browser):
        page = await context.new_page()
        try:
            await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            await asyncio.sleep(2.0)
            current_url = page.url

            # If redirected to Google login page
            if "accounts.google.com" in current_url:
                return {
                    "logged_in": False,
                    "channel_title": "",
                    "channel_id": "",
                    "url": current_url,
                    "message": "Chưa đăng nhập Google trong Profile này. Vui lòng mở Profile trên GPM để đăng nhập.",
                }

            # Check if Studio loaded
            if "studio.youtube.com" in current_url:
                title = await page.title()
                channel_name = ""
                try:
                    name_elem = await page.query_selector("#entity-name, #channel-name, #header-channel-name")
                    if name_elem:
                        channel_name = (await name_elem.inner_text()).strip()
                except Exception:
                    pass

                return {
                    "logged_in": True,
                    "channel_title": channel_name or title,
                    "channel_id": "",
                    "url": current_url,
                    "message": "Đã đăng nhập YouTube Studio thành công trên Profile GPM.",
                }

            return {
                "logged_in": False,
                "channel_title": "",
                "channel_id": "",
                "url": current_url,
                "message": f"Trang hiện tại: {current_url}",
            }
        except Exception as exc:
            logger.error("Lỗi khi kiểm tra đăng nhập YouTube trên GPM: %s", exc)
            return {
                "logged_in": False,
                "channel_title": "",
                "channel_id": "",
                "url": "",
                "message": f"Không thể kết nối YouTube Studio: {exc}",
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass


async def post_comment_reply_via_gpm(
    profile_id: str,
    video_url: str,
    comment_text: str,
    comment_id: str = "",
    auto_heart: bool = True,
    auto_stop: bool | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Post a comment reply (and optionally give Creator Heart) to a video watch page using the channel's GPM profile."""
    clean_comment_id = str(comment_id or "").strip()
    target_url = str(video_url or "").strip()
    if clean_comment_id and f"lc={clean_comment_id}" not in target_url:
        delimiter = "&" if "?" in target_url else "?"
        target_url = f"{target_url}{delimiter}lc={clean_comment_id}"

    logger.info(
        "Đăng bình luận qua GPM profile %s trên URL %s (comment_id=%s, auto_heart=%s, auto_stop=%s)",
        profile_id,
        target_url,
        clean_comment_id,
        auto_heart,
        auto_stop,
    )
    hearted = False
    async with gpm_browser_session(profile_id, auto_stop=auto_stop) as (context, _browser):
        page = await context.new_page()
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            await asyncio.sleep(2.0)

            # Scroll down to load comments section
            await page.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(1.5)

            # If replying to a specific linked comment
            if clean_comment_id:
                # 1. Locate the highlighted or target comment thread
                target_thread = await page.wait_for_selector(
                    "ytd-comment-thread-renderer[is-highlighted], ytd-comment-view-model[is-highlighted], ytd-comment-thread-renderer",
                    timeout=10000,
                )

                # 2. Perform Creator Heart if auto_heart is requested
                if auto_heart and target_thread:
                    try:
                        heart_btn = await target_thread.query_selector(
                            "#creator-heart-button button, ytd-creator-heart-renderer button, button[aria-label*='tim' i], button[aria-label*='heart' i]"
                        )
                        if heart_btn:
                            aria_label = str(await heart_btn.get_attribute("aria-label") or "").casefold()
                            aria_pressed = str(await heart_btn.get_attribute("aria-pressed") or "").casefold()
                            # If not already hearted (e.g. not "Bỏ thả tim" / "Remove heart")
                            if aria_pressed != "true" and "bỏ" not in aria_label and "remove" not in aria_label and "unheart" not in aria_label:
                                await heart_btn.click()
                                await asyncio.sleep(0.5)
                                hearted = True
                                logger.info("Đã thả tim cho bình luận %s qua GPM CDP", clean_comment_id)
                            else:
                                hearted = True
                                logger.info("Bình luận %s đã được thả tim trước đó", clean_comment_id)
                    except Exception as heart_exc:
                        logger.warning("Không thể thả tim cho bình luận %s: %s", clean_comment_id, heart_exc)

                # 3. Click Reply button on the target thread
                reply_btn = None
                if target_thread:
                    reply_btn = await target_thread.query_selector(
                        "#reply-button-end button, ytd-button-renderer#reply-button-end button, button[aria-label*='Trả lời' i], button[aria-label*='Phản hồi' i], button[aria-label*='Reply' i]"
                    )
                if reply_btn:
                    await reply_btn.click()
                    await asyncio.sleep(0.5)

                # 4. Find editable reply box in the active reply dialog or page
                editable = await page.wait_for_selector(
                    "ytd-comment-reply-dialog-renderer #contenteditable-root[contenteditable='true'], #reply-dialog #contenteditable-root[contenteditable='true'], #contenteditable-root[contenteditable='true']",
                    timeout=5000,
                )
                if not editable:
                    raise GpmAutomationError("Không kích hoạt được ô soạn thảo câu trả lời bình luận.")

                await editable.fill(comment_text)
                await asyncio.sleep(0.5)

                submit_button = await page.wait_for_selector(
                    "ytd-comment-reply-dialog-renderer #submit-button button, #reply-dialog #submit-button button, #submit-button button, ytd-button-renderer#submit-button",
                    timeout=5000,
                )
                if not submit_button:
                    raise GpmAutomationError("Không tìm thấy nút gửi câu trả lời bình luận.")

                await submit_button.click()
                await asyncio.sleep(2.0)

            else:
                # Top-level comment fallback when comment_id is empty
                input_box = await page.wait_for_selector(
                    "#simplebox-placeholder, #contenteditable-root",
                    timeout=10000,
                )
                if not input_box:
                    raise GpmAutomationError("Không tìm thấy ô nhập bình luận trên trang YouTube.")

                await input_box.click()
                await asyncio.sleep(0.5)

                editable = await page.wait_for_selector("#contenteditable-root[contenteditable='true']", timeout=5000)
                if not editable:
                    raise GpmAutomationError("Không kích hoạt được ô soạn thảo bình luận.")

                await editable.fill(comment_text)
                await asyncio.sleep(0.5)

                submit_button = await page.wait_for_selector("#submit-button button, ytd-button-renderer#submit-button", timeout=5000)
                if not submit_button:
                    raise GpmAutomationError("Không tìm thấy nút gửi bình luận.")

                await submit_button.click()
                await asyncio.sleep(2.0)

            return {
                "success": True,
                "video_url": target_url,
                "comment_text": comment_text,
                "comment_id": clean_comment_id,
                "hearted": hearted,
                "message": "Đã đăng câu trả lời và thả tim thành công qua Profile GPM." if hearted else "Đã đăng câu trả lời thành công qua Profile GPM.",
            }
        except Exception as exc:
            logger.error("Lỗi khi đăng bình luận qua GPM: %s", exc)
            raise GpmAutomationError(f"Không thể đăng bình luận qua GPM: {exc}") from exc
        finally:
            try:
                await page.close()
            except Exception:
                pass


async def open_url_in_gpm_profile(
    profile_id: str,
    url: str,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Open a URL in a new tab within the specified GPM profile browser.

    The browser remains open so the user can interact with the page (e.g. Google OAuth or YouTube Studio).
    If the profile is already running, opens a new tab in the running session without error.
    """
    clean_id = str(profile_id).strip()
    target_url = str(url).strip()
    if not clean_id:
        raise ValueError("Profile ID không được để trống.")
    if not target_url:
        raise ValueError("URL không được để trống.")

    logger.info("Mở URL %s trong GPM profile %s", target_url, clean_id)

    # 1. Start the profile or obtain connection coordinates
    try:
        launch_info = await asyncio.to_thread(start_gpm_profile, clean_id)
    except Exception as exc:
        logger.warning("start_gpm_profile thông báo: %s. Thử mở qua tiến trình đang chạy...", exc)
        return await asyncio.to_thread(open_tab_in_running_gpm_process, clean_id, target_url)

    if launch_info.get("already_running_no_cdp"):
        return await asyncio.to_thread(open_tab_in_running_gpm_process, clean_id, target_url)

    remote_port = launch_info.get("remote_debugging_port")
    ws_url = str(launch_info.get("websocket_debugging_url") or "").strip()
    if not remote_port and not ws_url:
        addr = str(
            launch_info.get("selenium_remote_debug_address")
            or launch_info.get("remote_debugging_address")
            or ""
        ).strip()
        if ":" in addr:
            try:
                remote_port = int(addr.split(":")[-1])
            except ValueError:
                pass

    # 2. Try HTTP DevTools /json/new first if remote_port exists
    if remote_port:
        try:
            encoded_url = urllib.parse.quote(target_url, safe="")
            new_tab_url = f"http://127.0.0.1:{remote_port}/json/new?{encoded_url}"
            req = urllib.request.Request(new_tab_url, method="PUT")
            try:
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    tab_data = json.loads(resp.read().decode("utf-8"))
                    tab_id = tab_data.get("id")
                    if tab_id:
                        activate_url = f"http://127.0.0.1:{remote_port}/json/activate/{tab_id}"
                        with urllib.request.urlopen(activate_url, timeout=5.0) as act_resp:
                            pass
                    return {
                        "success": True,
                        "profile_id": clean_id,
                        "url": target_url,
                        "message": f"Đã mở URL trong GPM Profile {clean_id}",
                    }
            except Exception:
                get_req = urllib.request.Request(new_tab_url, method="GET")
                with urllib.request.urlopen(get_req, timeout=5.0) as resp:
                    tab_data = json.loads(resp.read().decode("utf-8"))
                    tab_id = tab_data.get("id")
                    if tab_id:
                        activate_url = f"http://127.0.0.1:{remote_port}/json/activate/{tab_id}"
                        with urllib.request.urlopen(activate_url, timeout=5.0) as act_resp:
                            pass
                    return {
                        "success": True,
                        "profile_id": clean_id,
                        "url": target_url,
                        "message": f"Đã mở URL trong GPM Profile {clean_id}",
                    }
        except Exception as http_exc:
            logger.debug("Không thể mở qua HTTP /json/new: %s. Chuyển sang Playwright CDP...", http_exc)

    # 3. Fallback to Playwright CDP
    if ws_url or remote_port:
        from playwright.async_api import async_playwright
        endpoint_url = ws_url if ws_url else f"http://127.0.0.1:{remote_port}"
        playwright_cm = async_playwright()
        playwright = await playwright_cm.start()
        try:
            browser = await playwright.chromium.connect_over_cdp(endpoint_url)
            contexts = browser.contexts
            context = contexts[0] if contexts else await browser.new_context()
            page = await context.new_page()
            await page.goto(target_url, wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            await page.bring_to_front()
            return {
                "success": True,
                "profile_id": clean_id,
                "url": target_url,
                "message": f"Đã mở URL trong GPM Profile {clean_id}",
            }
        except Exception as exc:
            logger.warning("CDP mở tab thất bại: %s. Chuyển sang IPC...", exc)
        finally:
            try:
                await playwright.stop()
            except Exception:
                pass

    # 4. Fallback to opening tab via running process IPC
    return await asyncio.to_thread(open_tab_in_running_gpm_process, clean_id, target_url)

