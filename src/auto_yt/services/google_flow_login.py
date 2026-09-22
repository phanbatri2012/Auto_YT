import asyncio
import logging
import re
from playwright.async_api import Page

from auto_yt.services import google_flow_account

logger = logging.getLogger(__name__)
FLOW_HOME_URL = "https://labs.google/fx/flow"
FLOW_URL_PREFIXES = (FLOW_HOME_URL, "https://flow.google.com/")
FLOW_WORKSPACE_BUTTON_PATTERN = re.compile(
    r"New project|Dự án mới|Tạo dự án",
    re.IGNORECASE,
)


def is_google_flow_url(url: str) -> bool:
    return any(str(url or "").startswith(prefix) for prefix in FLOW_URL_PREFIXES)

class GoogleFlowPage:
    def __init__(self, page: Page):
        self.page = page

    async def wait_for_load(self):
        await self.page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)

    async def login(self, email: str, password: str, totp_secret: str) -> dict:
        await self.page.goto("https://accounts.google.com/")
        await self.wait_for_load()

        if await self.is_logged_in():
            return await self.verify_flow_access()

        email_input = self.page.locator("input[type='email']")
        if await email_input.is_visible(timeout=5000):
            if not email:
                raise RuntimeError(
                    "Hãy nhập email hoặc đăng nhập thủ công trong trình duyệt Flow."
                )
            await email_input.fill(email)
            await self.page.keyboard.press("Enter")
            await self.wait_for_load()

        password_input = self.page.locator("input[type='password']")
        if await password_input.is_visible(timeout=5000):
            if not password:
                raise RuntimeError(
                    "Google yêu cầu mật khẩu. Hãy lưu mật khẩu hoặc đăng nhập thủ công."
                )
            await password_input.fill(password)
            await self.page.keyboard.press("Enter")
            await self.wait_for_load()

        totp_input = self.page.locator("input[type='tel'], input[name='Pin']")
        if await totp_input.is_visible(timeout=5000):
            if not totp_secret:
                raise RuntimeError(
                    "Google yêu cầu 2FA. Hãy nhập TOTP Secret hoặc xác minh thủ công."
                )
            import pyotp
            totp = pyotp.TOTP(totp_secret).now()
            await totp_input.fill(totp)
            await self.page.keyboard.press("Enter")
            await self.wait_for_load()

        if not await self.is_logged_in():
            raise RuntimeError(
                "Google yêu cầu CAPTCHA, passkey hoặc bước xác minh thủ công."
            )

        return await self.verify_flow_access()

    async def is_logged_in(self) -> bool:
        cookies = await self.page.context.cookies()
        if google_flow_account.has_google_flow_auth_cookie(cookies):
            return True
        try:
            account_control = self.page.locator(
                "a[href*='SignOutOptions'], "
                "a[aria-label*='Google Account'], "
                "a[aria-label*='Tài khoản Google']"
            ).first
            return await account_control.is_visible(timeout=3000)
        except Exception:
            return False

    async def verify_flow_access(self) -> dict:
        await self.page.goto(FLOW_HOME_URL, wait_until="domcontentloaded")
        await self.wait_for_load()
        current_url = str(self.page.url or "")
        if not is_google_flow_url(current_url):
            raise RuntimeError(
                "Chưa mở được Google Flow. Hãy đăng nhập thủ công trong trình duyệt Flow."
            )
        if not await self.is_logged_in():
            raise RuntimeError("Phiên Google chưa đăng nhập hoặc đã hết hạn.")
        new_project_button = self.page.get_by_role(
            "button",
            name=FLOW_WORKSPACE_BUTTON_PATTERN,
        ).first
        if not await new_project_button.is_visible(timeout=5000):
            raise RuntimeError(
                "Google Flow đã mở nhưng workspace chưa sẵn sàng. "
                "Hãy hoàn tất bước Get started hoặc kiểm tra gói Google AI."
            )
        return {
            "success": True,
            "message": "Phiên Google hợp lệ và Google Flow đã mở.",
            "cookies": await self.page.context.cookies(),
        }

async def login_google_flow(account: dict, page: Page) -> dict:
    flow_page = GoogleFlowPage(page)
    try:
        return await flow_page.verify_flow_access()
    except RuntimeError:
        return await flow_page.login(
            account.get("email", ""),
            account.get("password", ""),
            account.get("totp_secret", ""),
        )

async def restore_session(cookies: list, page: Page) -> dict:
    await page.context.add_cookies(cookies)
    flow_page = GoogleFlowPage(page)
    return await flow_page.verify_flow_access()
