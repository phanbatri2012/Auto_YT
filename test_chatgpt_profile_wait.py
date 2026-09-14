import unittest
from unittest.mock import Mock, patch

from auto_yt.services import chatgpt_browser_service, chatgpt_worker
from auto_yt.services.chatgpt_runtime import ChatGPTAttentionRequiredError


class ChatGPTBrowserServiceWorkerTests(unittest.TestCase):
    def test_worker_attaches_to_existing_browser_service(self):
        context = Mock()
        browser = Mock()
        browser.contexts = [context]
        browser_type = Mock()
        browser_type.connect_over_cdp.return_value = browser

        with patch.object(
            chatgpt_worker.chatgpt_browser_service,
            "get_browser_service_endpoint",
            return_value="http://127.0.0.1:43123",
        ):
            lease = chatgpt_worker.launch_chatgpt_context(
                browser_type, "profile", wait_timeout=1
            )

        self.assertIs(lease.pages, context.pages)
        browser_type.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:43123", timeout=1000
        )

    def test_worker_waits_briefly_for_service_startup(self):
        context = Mock()
        browser = Mock()
        browser.contexts = [context]
        browser_type = Mock()
        browser_type.connect_over_cdp.return_value = browser

        with (
            patch.object(
                chatgpt_worker.chatgpt_browser_service,
                "get_browser_service_endpoint",
                side_effect=["", "http://127.0.0.1:43123"],
            ),
            patch.object(chatgpt_worker.time, "sleep") as sleep,
        ):
            chatgpt_worker.launch_chatgpt_context(
                browser_type,
                "profile",
                wait_timeout=10,
                retry_interval=1,
            )

        sleep.assert_called_once_with(1)
        browser_type.connect_over_cdp.assert_called_once()

    def test_worker_never_starts_a_missing_browser_service(self):
        browser_type = Mock()

        with (
            patch.object(
                chatgpt_worker.chatgpt_browser_service,
                "get_browser_service_endpoint",
                return_value="",
            ),
            patch.object(
                chatgpt_worker.chatgpt_browser_service,
                "start_browser_service",
            ) as start_service,
            self.assertRaisesRegex(
                ChatGPTAttentionRequiredError,
                "Khởi động trình duyệt nền",
            ),
        ):
            chatgpt_worker.launch_chatgpt_context(
                browser_type, "profile", wait_timeout=0
            )

        start_service.assert_not_called()
        browser_type.connect_over_cdp.assert_not_called()

    def test_worker_rejects_service_without_persistent_context(self):
        browser = Mock()
        browser.contexts = []
        browser_type = Mock()
        browser_type.connect_over_cdp.return_value = browser

        with (
            patch.object(
                chatgpt_worker.chatgpt_browser_service,
                "get_browser_service_endpoint",
                return_value="http://127.0.0.1:43123",
            ),
            self.assertRaises(ChatGPTAttentionRequiredError),
        ):
            chatgpt_worker.launch_chatgpt_context(
                browser_type, "profile", wait_timeout=0
            )

    def test_worker_lease_cannot_close_shared_browser(self):
        context = Mock()
        browser = Mock()
        lease = chatgpt_worker.SharedBrowserContextLease(context, browser)

        lease.close()

        context.close.assert_not_called()
        browser.close.assert_not_called()

    def test_windows_hidden_service_uses_one_offscreen_headed_browser(self):
        options = chatgpt_browser_service.get_browser_service_launch_options(
            {"worker_headless": True, "game_mode": False},
            cdp_port=43123,
            platform_name="win32",
        )

        self.assertFalse(options["headless"])
        self.assertIn("--window-position=-32000,-32000", options["args"])
        self.assertIn("--remote-debugging-port=43123", options["args"])
        self.assertIn("--remote-debugging-address=127.0.0.1", options["args"])

    def test_non_windows_hidden_service_uses_true_headless(self):
        options = chatgpt_browser_service.get_browser_service_launch_options(
            {"worker_headless": True, "game_mode": False},
            cdp_port=43123,
            platform_name="linux",
        )

        self.assertTrue(options["headless"])

    def test_service_restores_only_saved_chatgpt_cookies(self):
        context = Mock()
        context.cookies.return_value = []
        saved = [
            {
                "name": "__Secure-next-auth.session-token",
                "value": "saved-session",
                "domain": ".chatgpt.com",
                "path": "/",
            },
            {
                "name": "SID",
                "value": "google-session",
                "domain": ".google.com",
                "path": "/",
            },
        ]

        with patch.object(
            chatgpt_browser_service.account_store,
            "load_account",
            return_value={"session_cookie": saved},
        ):
            restored = chatgpt_browser_service._restore_saved_chatgpt_session(
                context
            )

        self.assertTrue(restored)
        cookies = context.add_cookies.call_args.args[0]
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]["domain"], ".chatgpt.com")


if __name__ == "__main__":
    unittest.main()
