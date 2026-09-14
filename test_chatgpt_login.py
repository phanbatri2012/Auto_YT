import unittest
from unittest.mock import AsyncMock, Mock, patch

from auto_yt.services import chatgpt_login


class FakeLocator:
    def __init__(self, text="", visible=False):
        self.first = self
        self.text = text
        self.visible = visible

    async def is_visible(self, timeout=0):
        return self.visible

    async def inner_text(self, timeout=0):
        return self.text

    async def wait_for(self, **_kwargs):
        if not self.visible:
            raise TimeoutError()

    async def click(self, **_kwargs):
        return None

    async def dispatch_event(self, *_args, **_kwargs):
        return None


class ChatGptLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_menu_live_region_is_not_treated_as_auth_error(self):
        page = Mock()
        page.url = "https://chatgpt.com/"

        def locate(selector):
            if selector == '[aria-live="polite"]':
                return FakeLocator(
                    "High Create an image or sticker Write or edit Search the web",
                    visible=True,
                )
            return FakeLocator()

        page.locator.side_effect = locate

        self.assertIsNone(await chatgpt_login._get_visible_error_text(page))

    async def test_auth_page_still_reports_generic_alert(self):
        page = Mock()
        page.url = "https://auth.openai.com/log-in/password"

        def locate(selector):
            if selector == '[role="alert"]':
                return FakeLocator("Something went wrong", visible=True)
            return FakeLocator()

        page.locator.side_effect = locate

        self.assertEqual(
            await chatgpt_login._get_visible_error_text(page),
            "Something went wrong",
        )

    async def test_logged_out_bootstrap_does_not_skip_logged_in_dom_fallback(self):
        page = Mock()
        page.url = "https://chatgpt.com/"
        page.locator.return_value = FakeLocator(visible=True)

        with patch.object(
            chatgpt_login,
            "_get_bootstrap_data",
            new=AsyncMock(return_value={"authStatus": "logged_out"}),
        ):
            result = await chatgpt_login._verify_logged_in(page)

        self.assertTrue(result["logged_in"])

    async def test_password_form_timeout_accepts_completed_session_redirect(self):
        page = Mock()
        page.url = "https://chatgpt.com/"
        page.goto = AsyncMock()
        page.title = AsyncMock(return_value="ChatGPT")
        page.context.cookies = AsyncMock(return_value=[{"name": "session"}])

        email_input = FakeLocator(visible=True)
        page.locator.side_effect = lambda selector: (
            email_input
            if selector == chatgpt_login.SEL_EMAIL_INPUT
            else FakeLocator()
        )

        async def fill_and_submit(_page, selector, _value, timeout_ms=0):
            if selector == chatgpt_login.SEL_PASSWORD_INPUT:
                raise TimeoutError("password field disappeared after redirect")

        with (
            patch.object(chatgpt_login, "_wait_past_cloudflare", new=AsyncMock()),
            patch.object(chatgpt_login, "_dismiss_cookie_banner", new=AsyncMock()),
            patch.object(
                chatgpt_login,
                "_raise_known_auth_error",
                new=AsyncMock(),
            ),
            patch.object(
                chatgpt_login,
                "_fill_and_submit",
                side_effect=fill_and_submit,
            ),
            patch.object(
                chatgpt_login,
                "_verify_logged_in",
                new=AsyncMock(side_effect=[
                    {"logged_in": False, "user": None},
                    {
                        "logged_in": True,
                        "user": {"email": "user@example.com", "name": "", "plan": ""},
                    },
                ]),
            ),
            patch.object(chatgpt_login.asyncio, "sleep", new=AsyncMock()),
        ):
            result = await chatgpt_login.login_gpt_auto(
                {"email": "user@example.com", "password": "secret"},
                page,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["user"]["email"], "user@example.com")


if __name__ == "__main__":
    unittest.main()
