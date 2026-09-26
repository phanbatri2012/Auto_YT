import asyncio
import datetime as dt
import logging
import re
from pathlib import Path

from PIL import Image
from playwright.async_api import Page, Locator

from auto_yt.services.google_flow_login import FLOW_HOME_URL

logger = logging.getLogger(__name__)

DEBUG_LOG_DIR = Path("data/logs")

# ---------------------------------------------------------------------------
# SynthID / Google Flow watermark removal
# ---------------------------------------------------------------------------
# Google Flow images carry a small SynthID badge in the bottom-right corner.
# We crop ~4 % from every edge (slightly more from the bottom-right) and
# resize back to the original dimensions so downstream code is unaffected.
CROP_RATIO_LEFT = 0.02
CROP_RATIO_TOP = 0.02
CROP_RATIO_RIGHT = 0.05
CROP_RATIO_BOTTOM = 0.05


def _strip_watermark(image_path: Path) -> None:
    """Remove the SynthID watermark by cropping edges and resizing back."""
    try:
        img = Image.open(image_path)
        w, h = img.size
        left = int(w * CROP_RATIO_LEFT)
        top = int(h * CROP_RATIO_TOP)
        right = w - int(w * CROP_RATIO_RIGHT)
        bottom = h - int(h * CROP_RATIO_BOTTOM)
        cropped = img.crop((left, top, right, bottom))
        # Resize back to original dimensions with high-quality resampling
        result = cropped.resize((w, h), Image.Resampling.LANCZOS)
        result.save(image_path)
        logger.info("Stripped watermark from %s (crop %dx%d -> resize %dx%d)", image_path, right - left, bottom - top, w, h)
    except Exception as e:
        logger.warning("Failed to strip watermark from %s: %s", image_path, e)


def _is_valid_flow_error_text(text: str) -> bool:
    """Verify that detected text is an actual system error and not user prompt or UI placeholder."""
    if not text or len(text.strip()) < 3:
        return False
    import unicodedata
    norm = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("utf-8").lower()
    ignored_keywords = [
        "ban muon tao gi",
        "keyboard_return",
        "avoid:",
        "cinematic",
        "narrative scene",
        "documentary realism",
        "35mm photography",
        "widescreen still photograph",
    ]
    if any(kw in norm for kw in ignored_keywords):
        return False
    return True


class GoogleFlowWorker:
    _session_uploaded_references: set[str] = set()
    _session_uploaded_image_urls: set[str] = set()

    def __init__(self, page: Page):
        self.page = page
        self._uploaded_references = self._session_uploaded_references
        self._uploaded_image_urls = self._session_uploaded_image_urls

    async def wait_for_load(self, timeout_ms: int = 15000):
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass
        await asyncio.sleep(1.5)

    async def dismiss_blocking_dialogs(self):
        """Automatically dismiss welcome, terms, or onboarding dialogs and overlay backdrops if present."""
        # 1. Dismiss Angular CDK overlay backdrops/drawers/menus
        try:
            backdrop = self.page.locator(".cdk-overlay-backdrop-showing, .cdk-overlay-backdrop").first
            if await backdrop.is_visible(timeout=300):
                logger.info("Found active cdk-overlay-backdrop, pressing Escape to dismiss...")
                await self.page.keyboard.press("Escape")
                await asyncio.sleep(0.4)
                if await backdrop.is_visible(timeout=200):
                    logger.info("Backdrop still visible after Escape, clicking backdrop directly...")
                    try:
                        await backdrop.click(force=True, timeout=1000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)
        except Exception:
            pass

        # 2. Dismiss standard dialog buttons (scoped to dialog containers to avoid touching prompt bar)
        dismiss_selectors = [
            ".cdk-overlay-pane button:has-text('Got it')",
            ".cdk-overlay-pane button:has-text('Dismiss')",
            ".cdk-overlay-pane button:has-text('I understand')",
            ".cdk-overlay-pane button:has-text('Accept')",
            ".cdk-overlay-pane button:has-text('Đồng ý')",
            ".cdk-overlay-pane button:has-text('Đóng')",
            ".cdk-overlay-pane button:has-text('Close')",
            ".mat-mdc-dialog-container button:has-text('Close')",
            "button:has-text('Got it')",
            "button:has-text('I understand')",
            "button:has-text('Accept')",
        ]
        for sel in dismiss_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=300):
                    logger.info("Dismissing blocking dialog with selector: %s", sel)
                    await btn.click()
                    await asyncio.sleep(0.3)
            except Exception:
                continue

        # 3. Check and auto-configure Agent Settings dialog if open
        await self.handle_agent_settings_dialog()

        # 4. Check and approve credit or assistant confirmation prompts
        await self.handle_confirmation_prompts()

    async def handle_agent_settings_dialog(self) -> bool:
        """If Agent Settings dialog ('Cài đặt tác nhân') is open, select 'Không bao giờ' (Never) and close."""
        try:
            settings_dialog = self.page.locator(
                ":is(.cdk-overlay-pane, mat-dialog-container, div):has-text('Cài đặt tác nhân'), "
                ":is(.cdk-overlay-pane, mat-dialog-container, div):has-text('Agent settings')"
            ).first
            if await settings_dialog.is_visible(timeout=300):
                never_radio = self.page.locator(
                    "mat-radio-button:has-text('Không bao giờ'), "
                    "[role='radio']:has-text('Không bao giờ'), "
                    "label:has-text('Không bao giờ'), "
                    "div:has-text('Không bao giờ'):not(:has(*)), "
                    "mat-radio-button:has-text('Never'), "
                    "[role='radio']:has-text('Never')"
                ).first
                if await never_radio.is_visible(timeout=300):
                    logger.info("Found 'Cài đặt tác nhân' dialog, selecting 'Không bao giờ' (Never ask confirmation)...")
                    await never_radio.click(force=True, timeout=2000)
                    await asyncio.sleep(0.4)

                close_btn = self.page.locator(
                    "button:has-text('close'), button mat-icon:has-text('close'), "
                    "button[aria-label*='close' i], button[aria-label*='đóng' i], "
                    "button.close-button"
                ).first
                if await close_btn.is_visible(timeout=400):
                    await close_btn.click(force=True, timeout=1500)
                    await asyncio.sleep(0.3)
                return True
        except Exception:
            pass
        return False

    async def handle_confirmation_prompts(self) -> bool:
        """Automatically approve credit spend or assistant confirmation prompts, strictly prioritizing 'Always approve'."""
        # Priority 1: Always approve (Luôn phê duyệt) - remembers permission for entire session
        always_approve_selectors = [
            "[role='button']:has-text('Luôn phê duyệt')",
            "div.chat-action-button:has-text('Luôn phê duyệt')",
            "div:has-text('Luôn phê duyệt')",
            "button:has-text('Luôn phê duyệt')",
            "[aria-label*='Luôn phê duyệt' i]",
            "[role='button']:has-text('Always approve')",
            "div.chat-action-button:has-text('Always approve')",
            "div:has-text('Always approve')",
            "button:has-text('Always approve')",
            "[aria-label*='Always approve' i]",
        ]

        # Priority 2: Single approve (Phê duyệt) - fallback if 'Always approve' is not present
        single_approve_selectors = [
            "[role='button']:has-text('Phê duyệt')",
            "div:has-text('Phê duyệt')",
            "button:has-text('Phê duyệt')",
            "button:has-text('Xác nhận')",
            "[aria-label*='Phê duyệt' i]",
            "[role='button']:has-text('Approve')",
            "div:has-text('Approve')",
            "button:has-text('Approve')",
            "[aria-label*='Approve' i]",
        ]

        for sel in always_approve_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=300):
                    logger.info("Found 'Always approve' prompt ('%s'), clicking to grant persistent permission...", sel)
                    try:
                        await btn.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    await btn.click(force=True, timeout=2000)
                    await asyncio.sleep(0.5)
                    return True
            except Exception:
                continue

        for sel in single_approve_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=300):
                    logger.info("Found single confirmation prompt ('%s'), clicking to approve...", sel)
                    try:
                        await btn.scroll_into_view_if_needed(timeout=1000)
                    except Exception:
                        pass
                    await btn.click(force=True, timeout=2000)
                    await asyncio.sleep(0.5)
                    return True
            except Exception:
                continue

        return False

    async def wait_for_editor(self, timeout: float = 30.0) -> Locator:
        """Wait for the prompt editor element to become visible and interactive."""
        start_time = asyncio.get_event_loop().time()
        candidate_selectors = [
            ".ProseMirror",
            "div[contenteditable='true']",
            "textarea[placeholder*='create' i]",
            "textarea[placeholder*='prompt' i]",
            "textarea[aria-label*='create' i]",
            "textarea[aria-label*='prompt' i]",
            "textarea",
            "input[placeholder*='create' i]",
            "[role='textbox']",
        ]

        while asyncio.get_event_loop().time() - start_time < timeout:
            await self.dismiss_blocking_dialogs()

            for sel in candidate_selectors:
                try:
                    loc = self.page.locator(sel).first
                    if await loc.is_visible(timeout=500):
                        try:
                            backdrop = self.page.locator(".cdk-overlay-backdrop-showing, .cdk-overlay-backdrop").first
                            if await backdrop.is_visible(timeout=200):
                                await self.dismiss_blocking_dialogs()
                        except Exception:
                            pass
                        return loc
                except Exception:
                    continue

            # If inside project but page seems stuck on loading / blank screen after 10s, reload once
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > 10 and "/project/" in str(self.page.url or ""):
                title = await self.page.title()
                if "loading" in title.lower():
                    logger.info("Page stuck in loading state ('%s'), reloading...", title)
                    try:
                        await self.page.reload(wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(2)
                    except Exception:
                        pass

            await asyncio.sleep(1)

        # If timeout reached, capture debug screenshot
        await self._save_debug_screenshot("editor_not_found")
        raise RuntimeError(
            f"Không tìm thấy ô nhập prompt trên Google Flow sau {int(timeout)} giây. "
            f"URL hiện tại: {self.page.url}"
        )

    async def _get_page_title(self) -> str:
        """Safely retrieve page title whether mocked or real."""
        try:
            t_attr = getattr(self.page, "title", None)
            if callable(t_attr):
                res = t_attr()
                if hasattr(res, "__await__"):
                    return str(await res or "")
                if isinstance(res, str):
                    return res
                return ""
            if isinstance(t_attr, str):
                return t_attr
            return ""
        except Exception:
            return ""

    async def _find_project_link(self, project_name: str) -> Locator | None:
        """Find link/card corresponding to project_name on the Flow home page."""
        # 1. Direct link or title matching project_name
        try:
            link = self.page.locator(f"a[href*='/project/']:has-text('{project_name}')").first
            if await link.is_visible(timeout=1000):
                return link
        except Exception:
            pass

        # 2. Check card container with project_name that contains a project link
        try:
            card = self.page.locator(f"div:has-text('{project_name}') >> a[href*='/project/']").first
            if await card.is_visible(timeout=1000):
                return card
        except Exception:
            pass

        # 3. XPath ancestor lookup
        try:
            xpath_loc = self.page.locator(
                f"xpath=//*[contains(text(), '{project_name}')]/ancestor-or-self::*[.//a[contains(@href, '/project/')]]//a[contains(@href, '/project/')]"
            ).first
            if await xpath_loc.is_visible(timeout=1000):
                return xpath_loc
        except Exception:
            pass

        return None

    async def ensure_project(self, project_name: str, force_new: bool = False) -> str:
        """Ensure that the browser is inside an active project session matching project_name.
        If force_new is True, always create a brand-new project on Google Flow."""
        current_url = str(self.page.url or "")
        current_title = (await self._get_page_title()).lower()

        # 1. Check if on 404/error page
        is_error_page = "/404" in current_url or "not found" in current_title

        # 2. If already inside a project URL, verify if it's the right project and editor is ready (only when NOT force_new)
        if not force_new and "/project/" in current_url and not is_error_page:
            # Check for conflict: if current title explicitly has auto_yt_<other_id>
            has_conflict = False
            match = re.search(r"auto_yt_\d+", current_title)
            if match and project_name.lower().startswith("auto_yt_"):
                if match.group(0).lower() != project_name.lower():
                    has_conflict = True

            if not has_conflict:
                try:
                    await self.wait_for_editor(timeout=10.0)
                    return self.page.url
                except Exception:
                    logger.warning("Project editor not ready in current project, trying reload...")
                    try:
                        await self.page.reload(wait_until="domcontentloaded", timeout=15000)
                        await self.wait_for_editor(timeout=10.0)
                        return self.page.url
                    except Exception:
                        logger.warning("Reload failed to restore editor; returning to home...")
            else:
                logger.info("Current project '%s' conflicts with '%s', returning to home...", current_title, project_name)

        # 3. Navigate to Flow home or ensure Flow URL
        try:
            await self.page.goto(FLOW_HOME_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            await self.page.goto("https://flow.google.com/", wait_until="domcontentloaded", timeout=30000)
        await self.wait_for_load()
        if force_new:
            self._uploaded_references.clear()
            self._uploaded_image_urls.clear()
        await self.dismiss_blocking_dialogs()

        # 4. Check if existing project link with project_name exists on home page (only when NOT force_new)
        if not force_new:
            project_link = await self._find_project_link(project_name)
            if project_link:
                logger.info("Found existing project '%s', opening...", project_name)
                try:
                    await project_link.click()
                    await self.wait_for_load()
                    await self.wait_for_editor(timeout=25.0)
                    return self.page.url
                except Exception as e:
                    logger.warning("Could not open existing project link: %s. Will try creating new project.", e)

        # 5. Click "New project" button
        new_btn_selectors = [
            "button:has-text('New project')",
            "span:has-text('New project')",
            "[aria-label*='New project' i]",
            "button:has-text('Dự án mới')",
            "span:has-text('Dự án mới')",
            "[aria-label*='Dự án mới' i]",
            "button:has-text('Create project')",
            "[aria-label*='Create' i]",
            "button.mat-mdc-unelevated-button:has-text('New')",
        ]
        clicked = False
        for sel in new_btn_selectors:
            try:
                btn = self.page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    clicked = True
                    break
            except Exception:
                continue

        if not clicked:
            # Maybe the page already loaded straight into an untitled project session
            if "/project/" in str(self.page.url or ""):
                await self.wait_for_editor(timeout=20.0)
                return self.page.url
            await self._save_debug_screenshot("new_project_btn_missing")
            raise RuntimeError(
                f"Không thể bấm nút 'New project' trên Google Flow. URL: {self.page.url}"
            )

        await self.wait_for_load()
        await self.wait_for_editor(timeout=30.0)

        # Optionally set title if title input is available
        try:
            title_input = self.page.locator("input.editable-text-input").first
            if await title_input.is_visible(timeout=4000):
                await title_input.click()
                await title_input.fill(project_name)
                await self.page.keyboard.press("Enter")
                await asyncio.sleep(1)
                await self.dismiss_blocking_dialogs()
        except Exception:
            pass

        await self.dismiss_blocking_dialogs()
        return self.page.url

    async def upload_reference(self, reference_path: str, label: str):
        if not reference_path or not Path(reference_path).is_file():
            return
        ref_key = label or str(Path(reference_path).stem)
        if ref_key in self._uploaded_references or str(reference_path) in self._uploaded_references:
            logger.info("Reference '%s' already uploaded in this session, skipping re-upload.", ref_key)
            return

        add_btn = self.page.locator("button[aria-label*='Add ingredients' i], button.add-menu-trigger").first
        if await add_btn.is_visible(timeout=3000):
            try:
                await add_btn.click()
                await asyncio.sleep(1)
                async with self.page.expect_file_chooser(timeout=5000) as fc_info:
                    file_opt = self.page.locator(
                        "button:has-text('Upload media'), .sidebar-upload-btn, button:has-text('Upload'), button:has-text('File'), [role='menuitem']:has-text('File'), [role='menuitem']:has-text('Upload')"
                    ).first
                    if await file_opt.is_visible(timeout=3000):
                        await file_opt.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(reference_path)
                await self.wait_for_load()
                # Wait 2.5s for uploaded image to render and dismiss dialogs
                await asyncio.sleep(2.5)
                uploaded_snapshot = await self._get_existing_images()
                self._uploaded_image_urls.update(uploaded_snapshot)
                self._uploaded_references.add(ref_key)
                self._uploaded_references.add(str(reference_path))
                logger.info("Successfully uploaded reference asset: %s (%s)", ref_key, reference_path)
            except Exception as e:
                logger.warning("Could not upload reference: %s", e)
            finally:
                await self.dismiss_blocking_dialogs()

    async def _get_existing_images(self) -> set[str]:
        """Collect all flow-content image URLs currently rendered on the page, strictly excluding Google account avatars."""
        try:
            imgs = await self.page.evaluate('''() => {
                return Array.from(
                    document.querySelectorAll("img[src*='flow-content.google/image'], img.image, img[src*='googleusercontent.com']")
                ).map(i => ({
                    src: i.src,
                    alt: i.alt || '',
                    className: i.className || '',
                    parentRole: (i.parentElement && i.parentElement.getAttribute('role')) || '',
                    parentAria: (i.parentElement && i.parentElement.getAttribute('aria-label')) || ''
                })).filter(item => {
                    if (!item.src || item.src.startsWith('data:')) return false;
                    const s = item.src.toLowerCase();
                    const alt = item.alt.toLowerCase();
                    const aria = item.parentAria.toLowerCase();
                    // Blacklist all Google Account avatars and profile icons
                    if (s.includes('s32-c-mo') || s.includes('s96-c') || s.includes('pr_32px') || s.includes('/a/acg8oc') || s.includes('/a/')) return false;
                    if (alt.includes('google account') || alt.includes('tài khoản google') || alt.includes('profile')) return false;
                    if (aria.includes('google account') || aria.includes('tài khoản google') || aria.includes('profile')) return false;
                    if (item.className.includes('avatar') || item.className.includes('profile')) return false;
                    return true;
                }).map(item => item.src);
            }''')
            return set(imgs) if isinstance(imgs, list) else set()
        except Exception:
            return set()

    async def _get_existing_error_texts(self) -> set[str]:
        """Snapshot all existing error tile texts currently rendered on the page."""
        err_texts = set()
        canvas_err_selectors = [
            "flow-error-tile",
            ".canvas flow-error-tile",
            "flow-media-tile.error",
            "flow-media-tile[data-error='true']",
            "[data-tile-state='error']",
            ".error-tile",
        ]
        for sel in canvas_err_selectors:
            try:
                locs = self.page.locator(sel)
                count = await locs.count()
                for i in range(min(count, 10)):
                    t = (await locs.nth(i).inner_text() or "").strip()
                    if t:
                        err_texts.add(t)
            except Exception:
                pass
        return err_texts

    async def clear_ingredient_chips(self) -> None:
        """Clear all ingredient chips currently attached to the prompt box."""
        chip_selectors = [
            "flow-ingredient-chip",
            ".ingredient-chip",
            "mat-chip",
            ".mat-mdc-chip",
            "[data-chip]",
            ".chip-item",
            ".reference-chip",
            "flow-prompt-box flow-ingredient-chip",
        ]

        clear_btn_selectors = [
            "button.clear-button",
            "button[aria-label*='Clear prompt' i]",
            "button[aria-label*='Clear all' i]",
            "button[aria-label*='Xóa câu lệnh' i]",
            "button[aria-label*='Xóa tất cả' i]",
        ]

        for c_sel in clear_btn_selectors:
            try:
                c_btn = self.page.locator(c_sel).first
                if await c_btn.is_visible(timeout=500):
                    await c_btn.click()
                    await asyncio.sleep(0.3)
                    break
            except Exception:
                pass

        # Dismiss / remove individual chips
        for _ in range(8):
            found_any = False
            for sel in chip_selectors:
                try:
                    chips = self.page.locator(sel)
                    count = await chips.count()
                    if count > 0:
                        found_any = True
                        for i in range(count):
                            c = chips.nth(i)
                            if await c.is_visible(timeout=300):
                                await c.hover()
                                await asyncio.sleep(0.1)
                                del_btn_selectors = [
                                    "mat-icon:has-text('cancel')",
                                    "mat-icon:has-text('close')",
                                    "mat-icon:has-text('clear')",
                                    ".hover-icon-overlay",
                                    "button.delete-button",
                                    "button[aria-label*='Remove' i]",
                                    "button[aria-label*='Delete' i]",
                                    "button[aria-label*='Xóa' i]",
                                    ".remove-chip-button",
                                ]
                                for d_sel in del_btn_selectors:
                                    try:
                                        d_btn = c.locator(d_sel).first
                                        if await d_btn.is_visible(timeout=300):
                                            await d_btn.click()
                                            await asyncio.sleep(0.2)
                                            break
                                    except Exception:
                                        pass
                except Exception:
                    pass
            if not found_any:
                break

    async def sync_reference_ingredients(self, reference_ids: list[str] | None) -> None:
        """Ensure the prompt box has the desired reference image attached as an ingredient chip."""
        ref_ids = [str(r).strip() for r in (reference_ids or []) if str(r).strip()]

        if not ref_ids:
            await self.clear_ingredient_chips()
            return

        target_ref = ref_ids[0]

        # Clear existing chips to avoid mixing wrong references
        await self.clear_ingredient_chips()
        await self.dismiss_blocking_dialogs()

        add_btn = self.page.locator("button.add-menu-trigger, button[aria-label*='Add ingredients' i]").first
        if not await add_btn.is_visible(timeout=4000):
            logger.warning("Add ingredients button not visible; continuing without attached chip.")
            return

        try:
            await add_btn.click()
            await asyncio.sleep(0.8)
        except Exception as exc:
            logger.warning("Could not click add ingredients button: %s", exc)
            await self.dismiss_blocking_dialogs()
            return

        # Switch to Uploads tab inside the overlay
        uploads_tab = self.page.locator(
            ".cdk-overlay-container mat-list-item:has-text('Uploads'), "
            ".side-nav-list-item:has-text('Uploads'), "
            "button:has-text('Uploads')"
        ).first
        try:
            if await uploads_tab.is_visible(timeout=3000):
                await uploads_tab.click()
                await asyncio.sleep(0.5)
        except Exception as exc:
            logger.warning("Could not switch to Uploads tab in add menu: %s", exc)

        clean_ref = target_ref.replace(".jpg", "").replace(".png", "").replace(".webp", "").strip()

        # Select the asset card matching clean_ref or target_ref
        asset_btn = self.page.locator(
            f".cdk-overlay-container button.asset-item:has-text('{clean_ref}'), "
            f".cdk-overlay-container span.asset-title:has-text('{clean_ref}'), "
            f".cdk-overlay-container button.asset-item:has-text('{target_ref}')"
        ).first

        found = False
        try:
            if await asset_btn.is_visible(timeout=2500):
                await asset_btn.click()
                await asyncio.sleep(0.5)
                found = True
        except Exception:
            pass

        # If not directly visible in viewport, try using search input
        if not found:
            try:
                search_input = self.page.locator(
                    ".cdk-overlay-container input[placeholder*='Search' i], "
                    ".cdk-overlay-container input[aria-label*='Search' i]"
                ).first
                if await search_input.is_visible(timeout=1500):
                    await search_input.fill(clean_ref)
                    await self.page.keyboard.press("Enter")
                    await asyncio.sleep(0.6)
                    if await asset_btn.is_visible(timeout=2000):
                        await asset_btn.click()
                        await asyncio.sleep(0.5)
                        found = True
            except Exception:
                pass

        if not found:
            logger.warning("Asset item for reference '%s' not found in Uploads menu. Skipping reference attachment without fallback.", target_ref)
            await self.dismiss_blocking_dialogs()
            return

        # Click "Add to prompt" / "Thêm vào câu lệnh"
        add_to_prompt_btn = self.page.locator(
            ".cdk-overlay-container button.detail-add-to-prompt-btn, "
            "button:has-text('Thêm vào câu lệnh'), "
            "[role='menuitem']:has-text('Thêm vào câu lệnh'), "
            "button:has-text('Add to prompt'), "
            "[role='menuitem']:has-text('Add to prompt'), "
            ".cdk-overlay-container mat-icon:has-text('add')"
        ).first
        try:
            if await add_to_prompt_btn.is_visible(timeout=3000):
                await add_to_prompt_btn.click()
                await asyncio.sleep(0.8)
                logger.info("Successfully attached reference ingredient '%s' to prompt bar.", target_ref)
            else:
                logger.warning("'Add to prompt' / 'Thêm vào câu lệnh' button not visible.")
        except Exception as exc:
            logger.warning("Failed to click 'Add to prompt': %s", exc)
        finally:
            await self.dismiss_blocking_dialogs()

    async def generate_scene(self, prompt: str, avoid_prompt: str, reference_ids: list[str]) -> str:
        # Clean any URL / bracket tags from prompt
        clean_prompt = re.sub(r"\[IMAGE_URL:[^\]]*\]", "", prompt)
        clean_prompt = re.sub(r"https?://\S+", "", clean_prompt)
        clean_prompt = re.sub(r"/api/thumbnails/\S+", "", clean_prompt).strip()

        # Snapshot existing generated images and error tiles on the page before submitting prompt
        existing_imgs = await self._get_existing_images()
        initial_error_texts = await self._get_existing_error_texts()

        # Synchronize reference image ingredients with the prompt bar
        await self.sync_reference_ingredients(reference_ids)

        strict_avoid = "text, letters, words, typography, watermark, logo, headline, caption, subtitle, poster text, overlay, title banner"
        if avoid_prompt and avoid_prompt.strip():
            combined_avoid = f"{avoid_prompt.strip()}, {strict_avoid}"
        else:
            combined_avoid = strict_avoid

        full_prompt = f"{clean_prompt}. Avoid: {combined_avoid}"

        # Locate prompt editor
        editor = await self.wait_for_editor(timeout=25.0)
        
        # Click editor with bounded retry and backdrop recovery
        for click_attempt in range(3):
            await self.dismiss_blocking_dialogs()
            try:
                await editor.click(timeout=4000)
                break
            except Exception as e:
                err_msg = str(e).lower()
                if "intercept" in err_msg or "timeout" in err_msg:
                    logger.warning("Editor click attempt %d intercepted or timed out, dismissing backdrop and retrying: %s", click_attempt + 1, e)
                    try:
                        await self.page.keyboard.press("Escape")
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                    continue
                if click_attempt == 2:
                    raise
        await asyncio.sleep(0.3)

        # Clear existing text cleanly
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Backspace")
        await asyncio.sleep(0.2)

        # Fill prompt text
        try:
            await editor.fill(full_prompt)
        except Exception:
            # Fallback for complex contenteditable elements
            await self.page.keyboard.insert_text(full_prompt)

        # Verify text was entered
        current_text = await editor.evaluate("el => el.innerText || el.value || ''")
        if not current_text.strip():
            logger.info("Editor empty after fill, retrying with keyboard.insert_text...")
            await self.dismiss_blocking_dialogs()
            try:
                await editor.click(timeout=3000)
            except Exception:
                pass
            await self.page.keyboard.insert_text(full_prompt)

        await asyncio.sleep(0.5)

        # Trigger generation: Press Enter in editor first (most reliable for Google Flow / ProseMirror)
        try:
            focus_res = editor.focus()
            if asyncio.iscoroutine(focus_res):
                await focus_res
        except Exception:
            pass
        await self.page.keyboard.press("Enter")
        await asyncio.sleep(1.0)

        gen_btn_selectors = [
            "button.generate-icon-button",
            "button[aria-label*='Start generation' i]",
            "button[aria-label*='Generate' i]",
            "button:has-text('Generate')",
            "button:has-text('arrow_forward')",
        ]
        stop_btn_selectors = [
            "button:has-text('Stop')",
            "button[aria-label*='Stop' i]",
            "button.stop-icon-button",
        ]

        # Check if generation already started from Enter
        generation_started = False
        for s_sel in stop_btn_selectors:
            try:
                if await self.page.locator(s_sel).first.is_visible(timeout=500):
                    generation_started = True
                    break
            except Exception:
                continue

        if not generation_started:
            # Try clicking generate button if visible
            for sel in gen_btn_selectors:
                try:
                    candidate = self.page.locator(sel).first
                    if await candidate.is_visible(timeout=1000):
                        await self.dismiss_blocking_dialogs()
                        await candidate.click(timeout=3000, force=True)
                        generation_started = True
                        break
                except Exception:
                    continue

        if not generation_started:
            # Fallback: focus editor and press Enter again
            try:
                focus_res = editor.focus()
                if asyncio.iscoroutine(focus_res):
                    await focus_res
            except Exception:
                pass
            await self.page.keyboard.press("Enter")

        # Wait for generation to start (Stop button or progress indicator)
        for _ in range(15):
            await asyncio.sleep(1)
            await self.handle_confirmation_prompts()
            for s_sel in stop_btn_selectors:
                try:
                    if await self.page.locator(s_sel).first.is_visible(timeout=500):
                        generation_started = True
                        break
                except Exception:
                    continue
            if generation_started:
                logger.info("Google Flow generation started (Stop button appeared).")
                break

        # Specific canvas / workspace error tile selectors (excluding sidebar prompt cards and history)
        canvas_err_selectors = [
            "flow-error-tile",
            ".canvas flow-error-tile",
            "flow-media-tile.error",
            "flow-media-tile[data-error='true']",
            "[data-tile-state='error']",
            ".error-tile",
        ]

        # Wait for generation to complete (Stop button disappears and new image is available)
        deadline = asyncio.get_event_loop().time() + 240.0
        new_src = None
        existing_bases = {u.split("?")[0] for u in existing_imgs}
        uploaded_bases = {u.split("?")[0] for u in self._uploaded_image_urls}

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2)
            await self.handle_confirmation_prompts()

            is_generating = False
            for s_sel in stop_btn_selectors:
                try:
                    if await self.page.locator(s_sel).first.is_visible(timeout=300):
                        is_generating = True
                        generation_started = True
                        break
                except Exception:
                    continue

            # 1. ALWAYS check for brand-new images FIRST before any error tile evaluation
            try:
                current_imgs_info = await self.page.evaluate('''() => {
                    return Array.from(
                        document.querySelectorAll("img[src*='flow-content.google/image'], img.image, img[src*='googleusercontent.com']")
                    ).map(i => ({
                        src: i.src,
                        className: i.className || '',
                        width: i.naturalWidth || i.width || 0,
                        height: i.naturalHeight || i.height || 0
                    })).filter(item => item.src && !item.src.includes('s32-c-mo') && !item.src.includes('pr_32px'));
                }''')
            except Exception:
                current_imgs_info = []

            if not isinstance(current_imgs_info, list):
                current_imgs_info = []

            # Find brand new images excluding existing and uploaded reference portraits
            brand_new = [
                img for img in current_imgs_info
                if isinstance(img, dict) and img.get("src")
                and img.get("src") not in existing_imgs
                and img.get("src").split("?")[0] not in existing_bases
                and img.get("src") not in self._uploaded_image_urls
                and img.get("src").split("?")[0] not in uploaded_bases
            ]

            # Priority 1: High-resolution full images (16:9 widescreen or width >= 1024, height >= 400)
            for item in brand_new:
                w = item.get("width", 0)
                h = item.get("height", 0)
                is_widescreen = (w >= 1024) or (w >= 800 and w > h * 1.15) or (w > 0 and h > 0 and (w / h) >= 1.5)
                if "thumbnail" not in item.get("className", "") and is_widescreen and h >= 400:
                    new_src = item["src"]
                    break

            # Priority 2: Any brand-new image with width >= 800
            if not new_src:
                for item in brand_new:
                    if item.get("width", 0) >= 800:
                        new_src = item["src"]
                        break

            # Priority 3: Fallback brand-new image
            if not new_src and brand_new:
                new_src = brand_new[-1]["src"]

            if new_src:
                logger.info("Found newly generated Google Flow image: %s", new_src)
                break

            # If generation is actively running (Stop button visible), keep waiting
            if is_generating:
                continue

            # 2. Check for NEW canvas error tiles ONLY when generation stopped and NO new image was found
            current_error_texts = await self._get_existing_error_texts()
            new_errors = [
                e for e in current_error_texts
                if e not in initial_error_texts and _is_valid_flow_error_text(e)
            ]
            if new_errors and generation_started:
                logger.error("Google Flow new error tile detected: %s", new_errors[0])
                await self._save_debug_screenshot("flow_error_tile")
                raise RuntimeError(f"Google Flow báo lỗi khi tạo ảnh: {new_errors[0]}")

            # 3. Check for active snackbar error
            try:
                error_loc = self.page.locator(".mat-mdc-snack-bar-container [role='alert'], .mat-mdc-snack-bar-container, flow-toast-notification").first
                if await error_loc.is_visible(timeout=200):
                    txt = (await error_loc.inner_text() or "").strip()
                    if _is_valid_flow_error_text(txt) and txt not in initial_error_texts:
                        logger.error("Google Flow snackbar alert detected: %s", txt)
                        await self._save_debug_screenshot("flow_snackbar_error")
                        raise RuntimeError(f"Google Flow báo lỗi khi tạo ảnh: {txt}")
            except RuntimeError:
                raise
            except Exception:
                pass

            # 4. If generation started and stop button disappeared, check again with diff
            if generation_started and not is_generating:
                await asyncio.sleep(3)
                # Re-query
                updated_imgs = await self._get_existing_images()
                diff = [
                    u for u in updated_imgs
                    if u not in existing_imgs
                    and u.split("?")[0] not in existing_bases
                    and u not in self._uploaded_image_urls
                    and u.split("?")[0] not in uploaded_bases
                ]
                if diff:
                    new_src = diff[-1]
                    break

        if not new_src:
            # Check for error alert/snackbars on page (excluding prompt sidebar)
            error_text = ""
            try:
                error_loc = self.page.locator(".mat-mdc-snack-bar-container [role='alert'], .mat-mdc-snack-bar-container, flow-toast-notification").first
                if await error_loc.is_visible(timeout=1000):
                    txt = (await error_loc.inner_text() or "").strip()
                    if _is_valid_flow_error_text(txt):
                        error_text = txt
            except Exception:
                pass

            await self._save_debug_screenshot("generation_timeout")
            if error_text:
                raise RuntimeError(f"Google Flow báo lỗi khi tạo ảnh: {error_text}")
            raise RuntimeError("Google Flow không trả về ảnh mới sau 240 giây.")

        return new_src

    async def download_image(self, asset_url: str, save_path: str):
        target = Path(save_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        last_error = None
        for attempt in range(3):
            try:
                response = await self.page.request.get(asset_url, timeout=30000)
                if response.status == 200:
                    body = await response.body()
                    if body and len(body) > 0:
                        with open(target, "wb") as f:
                            f.write(body)
                        logger.info("Downloaded image successfully (%d bytes) to %s", len(body), target)
                        _strip_watermark(target)
                        return
                    raise RuntimeError(f"Tải ảnh từ Google Flow rỗng (0 bytes). Status: {response.status}")
                else:
                    raise RuntimeError(f"HTTP {response.status} khi tải ảnh từ Google Flow: {asset_url}")
            except Exception as e:
                last_error = e
                logger.warning("Attempt %d/3 download_image failed: %s", attempt + 1, e)
                await asyncio.sleep(2)

        raise RuntimeError(f"Không thể tải ảnh sau 3 lần thử: {last_error}")

    async def _get_existing_videos(self) -> set[str]:
        """Snapshot all existing video elements and URLs on the page."""
        try:
            vids = await self.page.evaluate('''() => {
                const results = [];
                document.querySelectorAll("video, flow-video-player video, a[href*='.mp4'], [data-video-url]").forEach(el => {
                    const src = el.currentSrc || el.src || el.href || el.getAttribute('data-video-url') || '';
                    if (src && !src.startsWith('data:')) {
                        results.push(src);
                    }
                });
                return results;
            }''')
            return set(vids) if isinstance(vids, list) else set()
        except Exception:
            return set()

    async def generate_scene_video(
        self,
        prompt: str,
        avoid_prompt: str,
        start_frame_path: Path | None = None,
        end_frame_path: Path | None = None,
        reference_ids: list[str] | None = None,
    ) -> str:
        """Generate a video clip from start (and optional end) frame using Veo on Google Flow."""
        existing_vids = await self._get_existing_videos()

        # 1. Upload start frame and end frame if provided
        upload_refs = []
        if start_frame_path and Path(start_frame_path).is_file():
            start_ref = f"start_{Path(start_frame_path).stem}"
            await self.upload_reference(str(start_frame_path), start_ref)
            upload_refs.append(start_ref)

        if end_frame_path and Path(end_frame_path).is_file():
            end_ref = f"end_{Path(end_frame_path).stem}"
            await self.upload_reference(str(end_frame_path), end_ref)
            upload_refs.append(end_ref)

        # Merge with other references if any
        all_refs = upload_refs + [r for r in (reference_ids or []) if r not in upload_refs]
        await self.sync_reference_ingredients(all_refs)

        # 2. Add strict negative prompt for text/watermarks and static still frames
        strict_avoid = "still frame, static image, cartoon, text, letters, words, typography, watermark, logo, headline, caption, subtitle, poster text"
        combined_avoid = f"{avoid_prompt}, {strict_avoid}" if avoid_prompt else strict_avoid
        clean_prompt = re.sub(r"\[IMAGE_URL:[^\]]*\]", "", prompt)
        clean_prompt = re.sub(r"https?://\S+", "", clean_prompt)
        clean_prompt = re.sub(r"/api/thumbnails/\S+", "", clean_prompt).strip()
        clean_prompt = re.sub(
            r"(?i)\b(?:still photograph|photography|35mm photography|photo|film still|raw photo|movie still|still photo)\b",
            "cinematic video",
            clean_prompt,
        )
        clean_prompt = re.sub(r"\s+", " ", clean_prompt).strip()
        full_prompt = f"{clean_prompt}. Avoid: {combined_avoid}"

        # 3. Locate prompt editor
        editor = await self.wait_for_editor(timeout=25.0)
        for click_attempt in range(3):
            await self.dismiss_blocking_dialogs()
            try:
                await editor.click(timeout=4000)
                break
            except Exception as e:
                if click_attempt == 2:
                    raise
                await asyncio.sleep(0.5)

        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Backspace")
        await asyncio.sleep(0.2)

        try:
            await editor.fill(full_prompt)
        except Exception:
            await self.page.keyboard.insert_text(full_prompt)

        await asyncio.sleep(0.5)

        # Try to switch mode to Video if video mode toggle exists
        video_mode_selectors = [
            "mat-button-toggle:has-text('Video')",
            "button:has-text('Video')",
            "[aria-label*='video' i]:not(video)",
            "button.mode-toggle-video",
        ]
        for v_sel in video_mode_selectors:
            try:
                v_btn = self.page.locator(v_sel).first
                if await v_btn.is_visible(timeout=500):
                    await v_btn.click(timeout=1000)
                    await asyncio.sleep(0.3)
                    break
            except Exception:
                continue

        # Trigger generation: Press Enter
        try:
            focus_res = editor.focus()
            if asyncio.iscoroutine(focus_res):
                await focus_res
        except Exception:
            pass
        await self.page.keyboard.press("Enter")
        await asyncio.sleep(1.0)

        gen_btn_selectors = [
            "button.generate-icon-button",
            "button[aria-label*='Start generation' i]",
            "button[aria-label*='Generate' i]",
            "button:has-text('Generate')",
            "button:has-text('arrow_forward')",
        ]
        stop_btn_selectors = [
            "button:has-text('Stop')",
            "button[aria-label*='Stop' i]",
            "button.stop-icon-button",
        ]

        generation_started = False
        for s_sel in stop_btn_selectors:
            try:
                if await self.page.locator(s_sel).first.is_visible(timeout=500):
                    generation_started = True
                    break
            except Exception:
                continue

        if not generation_started:
            for sel in gen_btn_selectors:
                try:
                    candidate = self.page.locator(sel).first
                    if await candidate.is_visible(timeout=1000):
                        await self.dismiss_blocking_dialogs()
                        await candidate.click(timeout=3000, force=True)
                        generation_started = True
                        break
                except Exception:
                    continue

        if not generation_started:
            try:
                focus_res = editor.focus()
                if asyncio.iscoroutine(focus_res):
                    await focus_res
            except Exception:
                pass
            await self.page.keyboard.press("Enter")

        # Wait for video generation (up to 180s for Veo video)
        deadline = asyncio.get_event_loop().time() + 180.0
        new_video_src = None

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2)
            await self.handle_confirmation_prompts()

            is_generating = False
            for s_sel in stop_btn_selectors:
                try:
                    if await self.page.locator(s_sel).first.is_visible(timeout=300):
                        is_generating = True
                        break
                except Exception:
                    continue

            if is_generating:
                continue

            # Query video elements
            try:
                current_videos = await self.page.evaluate('''() => {
                    const results = [];
                    document.querySelectorAll("video, flow-video-player video, a[href*='.mp4'], [data-video-url]").forEach(el => {
                        const src = el.currentSrc || el.src || el.href || el.getAttribute('data-video-url') || '';
                        if (src && !src.startsWith('data:')) {
                            results.push(src);
                        }
                    });
                    return results;
                }''')
            except Exception:
                current_videos = []

            if isinstance(current_videos, list):
                for v_src in current_videos:
                    if v_src and v_src not in existing_vids:
                        new_video_src = v_src
                        break

            if new_video_src:
                break

        if not new_video_src:
            error_text = ""
            try:
                error_loc = self.page.locator(".mat-mdc-snack-bar-container [role='alert'], .mat-mdc-snack-bar-container, flow-toast-notification").first
                if await error_loc.is_visible(timeout=1000):
                    txt = (await error_loc.inner_text() or "").strip()
                    if _is_valid_flow_error_text(txt):
                        error_text = txt
            except Exception:
                pass

            await self._save_debug_screenshot("video_generation_timeout")
            if error_text:
                raise RuntimeError(f"Google Flow Veo báo lỗi khi tạo video: {error_text}")
            raise RuntimeError("Google Flow Veo không trả về video mới sau 180 giây.")

        return new_video_src

    async def download_video(self, asset_url: str, save_path: str) -> None:
        """Download generated video file from URL/Blob to local path."""
        target = Path(save_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        last_error = None
        for attempt in range(3):
            try:
                if asset_url.startswith("blob:"):
                    # Extract blob data using in-page fetch
                    base64_data = await self.page.evaluate(
                        '''async (blobUrl) => {
                            const response = await fetch(blobUrl);
                            const blob = await response.blob();
                            return new Promise((resolve, reject) => {
                                const reader = new FileReader();
                                reader.onloadend = () => resolve(reader.result);
                                reader.onerror = reject;
                                reader.readAsDataURL(blob);
                            });
                        }''',
                        asset_url,
                    )
                    import base64
                    if base64_data and "base64," in base64_data:
                        raw_bytes = base64.b64decode(base64_data.split("base64,")[1])
                        target.write_bytes(raw_bytes)
                        if target.stat().st_size > 1000:
                            logger.info("Downloaded video from blob successfully (%d bytes) to %s", len(raw_bytes), target)
                            return
                else:
                    response = await self.page.request.get(asset_url, timeout=60000)
                    if response.status == 200:
                        body = await response.body()
                        if body and len(body) > 1000:
                            target.write_bytes(body)
                            logger.info("Downloaded video successfully (%d bytes) to %s", len(body), target)
                            return
                        raise RuntimeError(f"Tải video từ Google Flow rỗng hoặc quá nhỏ ({len(body) if body else 0} bytes).")
                    raise RuntimeError(f"HTTP {response.status} khi tải video từ Google Flow: {asset_url}")
            except Exception as e:
                last_error = e
                logger.warning("Attempt %d/3 download_video failed: %s", attempt + 1, e)
                await asyncio.sleep(2)

        raise RuntimeError(f"Không thể tải video sau 3 lần thử: {last_error}")

    async def _save_debug_screenshot(self, prefix: str):
        """Save a timestamped screenshot to assist in diagnosing UI issues."""
        try:
            DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = DEBUG_LOG_DIR / f"{prefix}_{timestamp}.png"
            await self.page.screenshot(path=str(path))
            logger.info("Saved debug screenshot to %s", path)
        except Exception as e:
            logger.warning("Failed to save debug screenshot: %s", e)

