import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from auto_yt import main
from auto_yt.services import google_flow_account, google_flow_browser_service
from auto_yt.services.google_flow_login import (
    FLOW_HOME_URL,
    FLOW_WORKSPACE_BUTTON_PATTERN,
    is_google_flow_url,
)


class GoogleFlowAccountTests(unittest.TestCase):
    def test_credentials_and_cookies_are_encrypted_at_rest(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            account_path = Path(temporary_directory) / "google_flow_account.json"
            with patch.object(
                google_flow_account,
                "GOOGLE_FLOW_ACCOUNT_PATH",
                account_path,
            ):
                google_flow_account.save_account(
                    {
                        "email": "user@example.com",
                        "password": "plain-password-for-test",
                        "totp_secret": "totp-secret-for-test",
                        "session_cookies": [
                            {
                                "name": "__Secure-1PSID",
                                "value": "cookie-for-test",
                                "domain": ".google.com",
                                "path": "/",
                            }
                        ],
                    }
                )
                raw = account_path.read_text(encoding="utf-8")
                restored = google_flow_account.load_account()

        self.assertNotIn("plain-password-for-test", raw)
        self.assertNotIn("totp-secret-for-test", raw)
        self.assertNotIn("cookie-for-test", raw)
        self.assertEqual(restored["password"], "plain-password-for-test")
        self.assertEqual(restored["session_cookies"][0]["value"], "cookie-for-test")

    def test_credentials_require_a_valid_email(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            account_path = Path(temporary_directory) / "google_flow_account.json"
            with patch.object(
                google_flow_account,
                "GOOGLE_FLOW_ACCOUNT_PATH",
                account_path,
            ):
                with self.assertRaises(ValueError):
                    google_flow_account.save_credentials(
                        {"email": "", "password": "secret"}
                    )
                with self.assertRaises(ValueError):
                    google_flow_account.save_credentials(
                        {"email": "not-an-email", "password": "secret"}
                    )

    def test_google_cookies_are_selected_and_detected(self):
        authentication_cookie = {
            "name": "__Secure-1PSID",
            "value": "encrypted-session",
            "domain": ".google.com",
            "path": "/",
        }
        preference_cookie = {
            "name": "PREF",
            "value": "preferences",
            "domain": ".google.com",
            "path": "/",
        }
        unrelated_cookie = {
            "name": "session",
            "value": "unrelated",
            "domain": ".example.com",
            "path": "/",
        }

        self.assertTrue(
            google_flow_account.has_google_flow_auth_cookie(
                [authentication_cookie]
            )
        )
        selected = google_flow_account.select_restorable_google_flow_cookies(
            [authentication_cookie, preference_cookie, unrelated_cookie],
            [],
        )
        self.assertEqual(selected, [authentication_cookie, preference_cookie])
        self.assertEqual(
            google_flow_account.select_restorable_google_flow_cookies(
                [authentication_cookie],
                [authentication_cookie],
            ),
            [],
        )


class GoogleFlowBrowserServiceTests(unittest.TestCase):
    def test_service_opens_the_current_google_flow_url(self):
        self.assertEqual(
            google_flow_browser_service.SERVICE_HOME_URL,
            FLOW_HOME_URL,
        )
        self.assertTrue(is_google_flow_url("https://labs.google/fx/flow"))
        self.assertTrue(is_google_flow_url("https://flow.google.com/"))
        self.assertFalse(is_google_flow_url("https://accounts.google.com/"))
        self.assertIsNotNone(FLOW_WORKSPACE_BUTTON_PATTERN.search("New project"))
        self.assertIsNotNone(FLOW_WORKSPACE_BUTTON_PATTERN.search("Dự án mới"))

    def test_service_persists_cookies_in_google_flow_account_store(self):
        authentication_cookie = {
            "name": "__Secure-1PSID",
            "value": "encrypted-session",
            "domain": ".google.com",
            "path": "/",
        }
        context = Mock()
        context.cookies.return_value = [authentication_cookie]

        with (
            patch.object(
                google_flow_browser_service.google_flow_account,
                "load_account",
                return_value={"email": "user@example.com"},
            ),
            patch.object(
                google_flow_browser_service.google_flow_account,
                "save_account",
            ) as save_account,
        ):
            google_flow_browser_service._save_current_google_flow_session(context)

        saved = save_account.call_args.args[0]
        self.assertEqual(saved["session_cookies"], [authentication_cookie])


class GoogleFlowEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_check_reuses_the_connected_browser(self):
        account = {"email": "user@example.com", "session_cookies": []}
        verification = {
            "success": True,
            "message": "Phiên Google hợp lệ và Google Flow đã mở.",
        }
        with (
            patch.object(
                main.google_flow_account,
                "load_account",
                return_value=account,
            ),
            patch.object(
                main.google_flow_browser_service,
                "get_browser_service_status",
                return_value={"connected": True},
            ),
            patch.object(
                main,
                "_verify_google_flow_browser_session",
                new=AsyncMock(return_value=verification),
            ) as verify_session,
        ):
            result = await main.check_flow_login()

        self.assertTrue(result["success"])
        verify_session.assert_awaited_once_with(account)

    async def test_start_endpoint_does_not_report_false_success(self):
        with (
            patch.object(
                main.google_flow_browser_service,
                "start_browser_service",
                return_value={"connected": False, "message": "profile is busy"},
            ),
            self.assertRaises(HTTPException) as error,
        ):
            main.manage_flow_browser("start")

        self.assertEqual(error.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
