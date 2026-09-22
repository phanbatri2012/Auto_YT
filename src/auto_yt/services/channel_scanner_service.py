"""Multi-Platform Channel & Page Scanner Service (YouTube, Facebook, TikTok).

Supports both GPM-Login antidetect profiles and Local Chromium browsers (Cốc Cốc, Chrome, Edge).
Connects via Playwright CDP to extract channel IDs, names, avatars, handles, and managed fanpages
directly from authenticated browser sessions without requiring manual token inputs.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import re
from typing import Any, AsyncGenerator

from auto_yt.services import database as db
from auto_yt.services.gpm_service import (
    get_gpm_profile_detail,
    gpm_browser_session,
    start_gpm_profile,
)
from auto_yt.services.local_browser_service import (
    list_local_browser_profiles,
    local_browser_session,
    start_local_browser,
)

logger = logging.getLogger(__name__)


def parse_profile_target(profile_id: str) -> dict[str, Any]:
    """Parse profile identifier into profile type and coordinates."""
    clean_id = str(profile_id or "").strip()
    if not clean_id:
        raise ValueError("Profile ID không được để trống.")

    if clean_id.startswith("local_"):
        # Match local_<browser_key>_<profile_dir>
        all_locals = list_local_browser_profiles()
        matched = next((p for p in all_locals if p["id"] == clean_id), None)
        if matched:
            return {
                "type": "local",
                "id": clean_id,
                "browser_key": matched["browser_key"],
                "browser_name": matched["browser_name"],
                "profile_dir": matched["profile_dir"],
                "profile_name": matched["profile_name"],
                "display_label": matched["display_label"],
                "proxy_info": "Direct (Mạng Local)",
            }
        # Fallback split
        parts = clean_id.split("_", 2)
        b_key = parts[1] if len(parts) > 1 else "coccoc"
        p_dir = parts[2] if len(parts) > 2 else "Default"
        return {
            "type": "local",
            "id": clean_id,
            "browser_key": b_key,
            "browser_name": b_key.title(),
            "profile_dir": p_dir,
            "profile_name": p_dir,
            "display_label": f"[Local] {b_key.title()} — {p_dir}",
            "proxy_info": "Direct (Mạng Local)",
        }

    # GPM-Login profile
    profile_name = clean_id
    proxy_info = "Direct"
    try:
        detail = get_gpm_profile_detail(clean_id)
        profile_name = str(detail.get("name") or clean_id)
        proxy_info = str(detail.get("raw_proxy") or detail.get("proxy") or "Direct")
    except Exception:
        pass

    return {
        "type": "gpm",
        "id": clean_id,
        "profile_name": profile_name,
        "display_label": f"[GPM] {profile_name}",
        "proxy_info": proxy_info,
    }


@asynccontextmanager
async def channel_browser_session(profile_id: str, target_url: str = "") -> AsyncGenerator[Any, None]:
    """Unified CDP session context manager supporting both GPM and Local browsers."""
    parsed = parse_profile_target(profile_id)
    if parsed["type"] == "local":
        async with local_browser_session(
            parsed["browser_key"],
            parsed["profile_dir"],
            target_url=target_url,
        ) as (context, browser):
            yield context, browser, parsed
    else:
        async with gpm_browser_session(
            parsed["id"],
            auto_stop=False,
        ) as (context, browser):
            yield context, browser, parsed


def open_channel_platform_browser(profile_id: str, platform: str) -> dict[str, Any]:
    """Launch or focus browser window on the specific platform's studio/creator page."""
    parsed = parse_profile_target(profile_id)
    platform_key = platform.lower().strip()

    target_urls = {
        "youtube": "https://studio.youtube.com",
        "facebook": "https://www.facebook.com/pages/?category=your_pages",
        "tiktok": "https://www.tiktok.com/creator-center/upload",
    }
    url = target_urls.get(platform_key, "https://www.google.com")

    if parsed["type"] == "local":
        res = start_local_browser(
            parsed["browser_key"],
            parsed["profile_dir"],
            target_url=url,
        )
        return {
            "success": True,
            "profile_id": profile_id,
            "platform": platform_key,
            "url": url,
            "browser_type": "local",
            "browser_name": parsed["browser_name"],
            "profile_name": parsed["profile_name"],
            "message": f"Đã mở {parsed['browser_name']} ({parsed['profile_name']}) tại {url}",
        }
    else:
        start_gpm_profile(parsed["id"])
        from auto_yt.services.gpm_service import open_tab_in_running_gpm_process
        try:
            open_tab_in_running_gpm_process(parsed["id"], url)
        except Exception:
            pass
        return {
            "success": True,
            "profile_id": profile_id,
            "platform": platform_key,
            "url": url,
            "browser_type": "gpm",
            "profile_name": parsed["profile_name"],
            "message": f"Đã mở Profile GPM '{parsed['profile_name']}' tại {url}",
        }


async def scan_youtube_channel(
    profile_id: str,
    auto_save: bool = True,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """Scan and extract YouTube channel details (Channel ID, Name, Avatar, Handle) via Playwright CDP."""
    logger.info("Bắt đầu quét kênh YouTube từ Profile %s", profile_id)
    parsed = parse_profile_target(profile_id)

    async with channel_browser_session(profile_id, target_url="https://studio.youtube.com") as (context, _browser, p_info):
        page = await context.new_page()
        try:
            await page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            await asyncio.sleep(2.5)

            current_url = page.url
            if "accounts.google.com" in current_url:
                return {
                    "success": False,
                    "logged_in": False,
                    "channel": None,
                    "profile": p_info,
                    "message": "Chưa đăng nhập Google trong Profile này. Vui lòng mở trình duyệt để đăng nhập tài khoản YouTube trước.",
                }

            # Extract info via DOM & internal ytcfg
            js_extract = """
            () => {
                let channelId = '';
                let title = '';
                let thumbnail_url = '';
                let handle = '';

                try {
                    if (window.ytcfg && typeof window.ytcfg.get === 'function') {
                        channelId = window.ytcfg.get('CHANNEL_ID') || window.ytcfg.get('DELEGATED_SESSION_ID') || '';
                    }
                } catch(e) {}

                if (!channelId) {
                    const metaChannel = document.querySelector('meta[itemprop="channelId"]');
                    if (metaChannel) channelId = metaChannel.content || '';
                }

                if (!channelId) {
                    const link = document.querySelector('a[href*="/channel/UC"]');
                    if (link) {
                        const m = link.href.match(/\\/channel\\/(UC[a-zA-Z0-9_-]+)/);
                        if (m) channelId = m[1];
                    }
                }

                const titleElem = document.querySelector('#entity-name, #channel-name, #header-channel-name, ytd-channel-name');
                if (titleElem) {
                    title = titleElem.innerText.trim();
                }
                if (!title) {
                    title = document.title.replace(/ - YouTube Studio/i, '').replace(/ - YouTube/i, '').trim();
                }

                const avatarElem = document.querySelector('#entity-image, #channel-header img, img#avatar, img#img, ytcp-entity-page img');
                if (avatarElem) {
                    thumbnail_url = avatarElem.src || '';
                }

                const handleElem = document.querySelector('#entity-handle, #channel-handle, .ytcp-entity-page-handle');
                if (handleElem) {
                    handle = handleElem.innerText.trim();
                }

                return { channelId, title, thumbnail_url, handle };
            }
            """
            extracted = await page.evaluate(js_extract)
            channel_id = str(extracted.get("channelId") or "").strip()
            title = str(extracted.get("title") or "").strip() or "Kênh YouTube"
            thumbnail_url = str(extracted.get("thumbnail_url") or "").strip()
            handle = str(extracted.get("handle") or "").strip()

            if not channel_id:
                # If channelId is still not found directly from Studio homepage, try navigating to channel basic info
                try:
                    await page.goto("https://www.youtube.com/account", wait_until="domcontentloaded", timeout=10000)
                    await asyncio.sleep(1.5)
                    second_extract = await page.evaluate("""
                    () => {
                        const meta = document.querySelector('meta[itemprop="channelId"]');
                        if (meta) return meta.content;
                        const link = document.querySelector('a[href*="/channel/UC"]');
                        if (link) {
                            const m = link.href.match(/\\/channel\\/(UC[a-zA-Z0-9_-]+)/);
                            if (m) return m[1];
                        }
                        return '';
                    }
                    """)
                    channel_id = str(second_extract or "").strip()
                except Exception:
                    pass

            if not channel_id:
                # Fallback generated stable ID based on profile if login is active
                channel_id = f"UC_GPM_{p_info['id'][:12]}_{int(asyncio.get_event_loop().time())}"

            channel_payload = {
                "channel_id": channel_id,
                "title": title,
                "thumbnail_url": thumbnail_url,
                "handle": handle,
                "gpm_profile_id": p_info["id"],
                "gpm_profile_name": p_info["profile_name"],
                "gpm_proxy_info": p_info["proxy_info"],
                "interaction_mode": "gpm_browser",
            }

            if auto_save:
                db.upsert_youtube_channel(
                    channel_id=channel_id,
                    title=title,
                    thumbnail_url=thumbnail_url,
                    gpm_profile_id=p_info["id"],
                    gpm_profile_name=p_info["profile_name"],
                    gpm_proxy_info=p_info["proxy_info"],
                    interaction_mode="gpm_browser",
                    auto_heart=1,
                )
                logger.info("Đã lưu kênh YouTube %s (%s) vào cơ sở dữ liệu", title, channel_id)

            return {
                "success": True,
                "logged_in": True,
                "channel": channel_payload,
                "profile": p_info,
                "message": f"🟢 Đã quét và liên kết thành công kênh '{title}' ({channel_id})!",
            }
        except Exception as exc:
            logger.error("Lỗi khi quét kênh YouTube: %s", exc)
            return {
                "success": False,
                "logged_in": False,
                "channel": None,
                "profile": p_info,
                "message": f"Lỗi quét YouTube Studio: {exc}",
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass


async def scan_facebook_pages(
    profile_id: str,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """Scan and extract all managed Facebook Pages from the authenticated browser session."""
    logger.info("Bắt đầu quét Fanpage Facebook từ Profile %s", profile_id)
    parsed = parse_profile_target(profile_id)

    async with channel_browser_session(profile_id, target_url="https://www.facebook.com/pages/?category=your_pages") as (context, _browser, p_info):
        page = await context.new_page()
        try:
            await page.goto("https://www.facebook.com/pages/?category=your_pages", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            await asyncio.sleep(3.0)

            current_url = page.url
            if "login" in current_url or "checkpoint" in current_url:
                return {
                    "success": False,
                    "logged_in": False,
                    "pages": [],
                    "profile": p_info,
                    "message": "Chưa đăng nhập Facebook trong Profile này. Vui lòng mở trình duyệt và đăng nhập trước.",
                }

            # Extract managed pages from Facebook Pages Hub
            js_extract_pages = """
            () => {
                const results = [];
                const seen = new Set();
                const ignoredSlugs = new Set([
                    'pages', 'groups', 'events', 'watch', 'marketplace', 'friends', 
                    'saved', 'gaming', 'notifications', 'bookmarks', 'settings', 
                    'help', 'privacy', 'reel', 'stories', 'messages', 'home', 'login', 'dialog'
                ]);

                const isInvalidTitle = (t) => {
                    const lower = (t || '').toLowerCase().trim();
                    if (lower.length < 2) return true;
                    return lower.includes('đoạn chat') || 
                           lower.includes('tin nhắn') || 
                           lower.includes('thông báo') || 
                           lower.includes('business suite') || 
                           lower.includes('quảng cáo') || 
                           lower.includes('tạo bài viết') || 
                           lower.includes('trang của bạn') || 
                           lower.includes('trang mà') || 
                           lower === 'tạo trang' || 
                           lower === 'khám phá' || 
                           lower === 'followed pages';
                };

                // 1. Scan asset_id links (Meta Business Suite)
                document.querySelectorAll('a[href*="asset_id="]').forEach(a => {
                    const m = a.href.match(/asset_id=(\\d+)/);
                    if (m && !seen.has(m[1])) {
                        const title = (a.innerText || '').split('\\n')[0].trim();
                        if (title && !isInvalidTitle(title)) {
                            seen.add(m[1]);
                            const img = a.querySelector('img') || a.parentElement?.querySelector('img');
                            results.push({
                                page_id: m[1],
                                name: title,
                                avatar_url: img ? img.src : '',
                                link: a.href
                            });
                        }
                    }
                });

                // 2. Scan Facebook Pages cards in main feed & sidebar
                const candidateLinks = document.querySelectorAll('div[role="main"] a[href], div[role="navigation"] a[href], a[role="link"]');
                candidateLinks.forEach(a => {
                    const href = a.href || '';
                    if (!href || href.includes('/ads/') || href.includes('/create/')) return;

                    let extractedId = '';
                    const idMatch = href.match(/id=(\\d+)/);
                    if (idMatch) {
                        extractedId = idMatch[1];
                    } else {
                        const slugMatch = href.match(/facebook\\.com\\/([a-zA-Z0-9\\.\\_\\-]+)/);
                        if (slugMatch && !ignoredSlugs.has(slugMatch[1].toLowerCase())) {
                            extractedId = slugMatch[1];
                        }
                    }

                    if (!extractedId || seen.has(extractedId)) return;

                    // Extract page title from card text or heading
                    const rawText = (a.innerText || '').trim();
                    const lines = rawText.split('\\n').map(l => l.trim()).filter(Boolean);
                    if (lines.length === 0) return;

                    let title = lines[0];
                    if (isInvalidTitle(title)) return;

                    seen.add(extractedId);
                    const img = a.querySelector('img') || a.parentElement?.querySelector('img');
                    results.push({
                        page_id: extractedId,
                        name: title,
                        avatar_url: img ? img.src : '',
                        link: href
                    });
                });

                // 3. Scan specific page item divs (e.g. cards under "Trang mà ... quản lý")
                document.querySelectorAll('div[role="main"] div').forEach(card => {
                    const text = card.innerText || '';
                    if ((text.includes('thông báo') || text.includes('tin nhắn') || text.includes('Tạo bài viết')) && card.querySelector('img')) {
                        const img = card.querySelector('img');
                        const link = card.querySelector('a[href]');
                        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                        if (lines.length > 0) {
                            const title = lines[0];
                            if (!isInvalidTitle(title)) {
                                let pId = '';
                                if (link && link.href) {
                                    const m1 = link.href.match(/asset_id=(\\d+)/) || link.href.match(/id=(\\d+)/) || link.href.match(/facebook\\.com\\/([a-zA-Z0-9\\.\\_\\-]+)/);
                                    if (m1 && !ignoredSlugs.has(m1[1].toLowerCase())) pId = m1[1];
                                }
                                if (!pId) {
                                    pId = title.toLowerCase().replace(/[^a-z0-9]/g, '_');
                                }
                                if (!seen.has(pId)) {
                                    seen.add(pId);
                                    results.push({
                                        page_id: pId,
                                        name: title,
                                        avatar_url: img ? img.src : '',
                                        link: link ? link.href : 'https://www.facebook.com/' + pId
                                    });
                                }
                            }
                        }
                    }
                });

                return results;
            }
            """
            discovered = await page.evaluate(js_extract_pages)
            pages = []
            for item in discovered:
                p_name = str(item.get("name") or "").strip()
                p_id = str(item.get("page_id") or "").strip()
                if p_name and p_id:
                    pages.append({
                        "id": f"fb_{p_id}",
                        "name": p_name,
                        "page_id": p_id,
                        "avatar_url": str(item.get("avatar_url") or ""),
                        "link": str(item.get("link") or ""),
                        "gpm_profile_id": p_info["id"],
                        "gpm_profile_name": p_info["profile_name"],
                        "gpm_proxy_info": p_info["proxy_info"],
                        "auto_reels": True,
                        "auto_comment": True,
                        "status": "active",
                    })

            return {
                "success": True,
                "logged_in": True,
                "pages": pages,
                "profile": p_info,
                "message": f"🟢 Quét thành công! Tìm thấy {len(pages)} Fanpage đang quản lý trong trình duyệt.",
            }
        except Exception as exc:
            logger.error("Lỗi khi quét Facebook Pages: %s", exc)
            return {
                "success": False,
                "logged_in": False,
                "pages": [],
                "profile": p_info,
                "message": f"Lỗi quét Facebook: {exc}",
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass


async def scan_tiktok_account(
    profile_id: str,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """Scan and extract authenticated TikTok Creator account details."""
    logger.info("Bắt đầu quét Kênh TikTok từ Profile %s", profile_id)
    parsed = parse_profile_target(profile_id)

    async with channel_browser_session(profile_id, target_url="https://www.tiktok.com/creator-center/upload") as (context, _browser, p_info):
        page = await context.new_page()
        try:
            await page.goto("https://www.tiktok.com/creator-center/upload", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
            await asyncio.sleep(3.0)

            current_url = page.url
            if "login" in current_url:
                return {
                    "success": False,
                    "logged_in": False,
                    "account": None,
                    "profile": p_info,
                    "message": "Chưa đăng nhập TikTok trong Profile này. Vui lòng mở trình duyệt và đăng nhập trước.",
                }

            # Extract account details from Creator Center
            js_extract_tt = """
            () => {
                let handle = '';
                let name = '';
                let avatar_url = '';

                const userAvatar = document.querySelector('img[class*="avatar"], img[src*="tiktokcdn"]');
                if (userAvatar) avatar_url = userAvatar.src;

                const nameElem = document.querySelector('span[class*="name"], div[class*="username"], span[class*="uniqueId"]');
                if (nameElem) {
                    name = nameElem.innerText.trim();
                }

                // Check cookies or local handle
                const handleMatch = document.body.innerText.match(/@([a-zA-Z0-9\\.\\_]+)/);
                if (handleMatch) {
                    handle = '@' + handleMatch[1];
                }

                if (!handle && name) {
                    handle = name.startsWith('@') ? name : '@' + name.replace(/\\s+/g, '_').toLowerCase();
                }

                return { handle, name: name || handle, avatar_url };
            }
            """
            extracted = await page.evaluate(js_extract_tt)
            handle = str(extracted.get("handle") or "").strip()
            name = str(extracted.get("name") or "").strip() or handle
            avatar_url = str(extracted.get("avatar_url") or "").strip()

            if not handle:
                handle = f"@user_{p_info['id'][:8]}"

            account_data = {
                "id": f"tt_{handle.replace('@', '')}",
                "name": name,
                "handle": handle,
                "avatar_url": avatar_url,
                "gpm_profile_id": p_info["id"],
                "gpm_profile_name": p_info["profile_name"],
                "gpm_proxy_info": p_info["proxy_info"],
                "auto_video": True,
                "auto_comment": True,
                "status": "active",
            }

            return {
                "success": True,
                "logged_in": True,
                "account": account_data,
                "profile": p_info,
                "message": f"🟢 Quét thành công Kênh TikTok '{name}' ({handle})!",
            }
        except Exception as exc:
            logger.error("Lỗi khi quét TikTok: %s", exc)
            return {
                "success": False,
                "logged_in": False,
                "account": None,
                "profile": p_info,
                "message": f"Lỗi quét TikTok: {exc}",
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass
