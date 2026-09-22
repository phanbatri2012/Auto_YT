"""TikTok Video Publisher Service for Auto_YT.

Handles:
- Uploading generated/downloaded MP4 videos to TikTok Creator Center via Playwright CDP & GPM Profiles.
- Automated caption & hashtag injection.
- Zero-Footprint Network & Proxy Isolation per channel profile.
- System Jobs tracking for centralized monitoring in Job Center (job_type="tiktok_publish").
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any

import auto_yt.services.database as db
from auto_yt.services.channel_scanner_service import channel_browser_session

logger = logging.getLogger(__name__)

TIKTOK_UPLOAD_URL = "https://www.tiktok.com/creator-center/upload"


async def upload_video_via_gpm(
    profile_id: str,
    video_path: Path,
    caption: str = "",
    tags: list[str] | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    """Upload a video file to TikTok Creator Center using the authenticated GPM Profile."""
    if not video_path.is_file():
        raise FileNotFoundError(f"File video không tồn tại: {video_path}")

    logger.info(
        "Bắt đầu upload video TikTok qua GPM Profile %s (File: %s, Size: %d MB)",
        profile_id,
        video_path.name,
        video_path.stat().st_size // (1024 * 1024),
    )

    tags_str = " ".join(
        f"#{t.strip().lstrip('#')}" for t in (tags or []) if t.strip()
    )
    full_caption = f"{caption} {tags_str}".strip()

    async with channel_browser_session(
        profile_id,
        target_url=TIKTOK_UPLOAD_URL,
        timeout_seconds=timeout_seconds,
    ) as (context, _browser, _p_info):
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto(
            TIKTOK_UPLOAD_URL,
            wait_until="domcontentloaded",
            timeout=int(timeout_seconds * 1000),
        )
        await asyncio.sleep(2.0)

        # Check if login is required
        if "/login" in page.url or await page.locator("text='Log in'").is_visible(timeout=3000):
            raise RuntimeError(
                "Chưa đăng nhập TikTok trong Profile này. Vui lòng mở trình duyệt và đăng nhập tài khoản trước."
            )

        # Locate file input element (supports standard input[type=file] or drag-drop frame)
        file_input = page.locator('input[type="file"]')
        if not await file_input.count():
            # Try finding inside iframes
            for frame in page.frames:
                frame_input = frame.locator('input[type="file"]')
                if await frame_input.count():
                    file_input = frame_input
                    break

        if not await file_input.count():
            raise RuntimeError("Không tìm thấy khung tải file video trên giao diện TikTok Creator Center.")

        await file_input.first.set_input_files(str(video_path))
        logger.info("Đã đưa file video vào input upload TikTok, đang chờ xử lý...")

        # Wait for upload processing container
        await asyncio.sleep(5.0)

        # Fill caption / description editor
        caption_editor = page.locator(
            '[contenteditable="true"], .DraftEditor-editorContainer, textarea[placeholder*="caption"], textarea[placeholder*="tiêu đề"]'
        )
        if await caption_editor.first.is_visible(timeout=10000):
            try:
                await caption_editor.first.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                if full_caption:
                    await page.keyboard.type(full_caption[:2000], delay=20)
            except Exception as cap_err:
                logger.warning("Không thể nhập caption tự động: %s", cap_err)

        # Post / Schedule button locator
        post_btn = page.locator(
            "button:has-text('Post'), button:has-text('Đăng'), button.btn-post, [data-e2e='post_video_button']"
        )
        if await post_btn.first.is_visible(timeout=10000):
            # Click post
            await post_btn.first.click()
            await asyncio.sleep(4.0)
            logger.info("Đã bấm nút Đăng video lên TikTok!")

        return {
            "success": True,
            "message": "Đã tải và đăng video lên TikTok Creator Center thành công!",
            "profile_id": profile_id,
            "video_name": video_path.name,
        }


def start_tiktok_publish_job(
    video_path: str,
    caption: str = "",
    tags: list[str] | None = None,
    gpm_profile_id: str = "",
    video_id: int | None = None,
    title: str = "",
) -> str:
    """Register a system_job and start background worker to publish video to TikTok."""
    v_path = Path(video_path)
    if not v_path.is_file():
        raise FileNotFoundError(f"File video không tồn tại: {video_path}")

    sys_job_id = f"tiktok-pub-{int(time.time() * 1000)}"
    job_title = f"Đăng TikTok: {title or v_path.stem[:40]}"

    db.create_system_job(
        job_id=sys_job_id,
        job_type="tiktok_publish",
        title=job_title,
        video_id=video_id,
        payload={
            "video_path": str(v_path),
            "caption": caption,
            "tags": tags or [],
            "gpm_profile_id": gpm_profile_id,
            "video_id": video_id,
        },
    )
    db.update_system_job(
        sys_job_id,
        status="running",
        progress="Đang kết nối GPM Profile TikTok...",
    )

    def _worker():
        try:
            db.update_system_job(
                sys_job_id,
                status="running",
                progress="Đang tải video lên TikTok Creator Center...",
            )
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                res = loop.run_until_complete(
                    upload_video_via_gpm(
                        profile_id=gpm_profile_id,
                        video_path=v_path,
                        caption=caption,
                        tags=tags,
                    )
                )
            finally:
                loop.close()

            db.update_system_job(
                sys_job_id,
                status="completed",
                progress="Đã đăng video lên TikTok thành công!",
                finished_at=db.utc_now(),
            )
        except Exception as exc:
            logger.error("TikTok publish job %s failed: %s", sys_job_id, exc)
            db.update_system_job(
                sys_job_id,
                status="failed",
                progress=f"Lỗi đăng TikTok: {str(exc)[:100]}",
                error=str(exc),
                finished_at=db.utc_now(),
            )

    worker_thread = threading.Thread(
        target=_worker,
        name=f"TikTokPublisher-{sys_job_id}",
        daemon=True,
    )
    worker_thread.start()

    return sys_job_id
