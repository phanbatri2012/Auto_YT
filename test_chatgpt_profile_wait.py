import unittest
from unittest.mock import Mock, patch

from auto_yt.services import chatgpt_worker


class ChatGPTProfileWaitTests(unittest.TestCase):
    def test_retries_when_profile_is_busy(self):
        expected_context = object()
        browser_type = Mock()
        browser_type.launch_persistent_context.side_effect = [
            RuntimeError(
                "Opening in existing browser session. "
                "The profile is already in use."
            ),
            expected_context,
        ]

        with patch.object(chatgpt_worker.time, "sleep") as sleep:
            context = chatgpt_worker.launch_chatgpt_context(
                browser_type,
                "profile",
                wait_timeout=10,
                retry_interval=1,
            )

        self.assertIs(context, expected_context)
        self.assertEqual(browser_type.launch_persistent_context.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_does_not_retry_unrelated_browser_error(self):
        browser_type = Mock()
        browser_type.launch_persistent_context.side_effect = RuntimeError(
            "Browser executable is missing"
        )

        with (
            patch.object(chatgpt_worker.time, "sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "Browser executable is missing"),
        ):
            chatgpt_worker.launch_chatgpt_context(
                browser_type,
                "profile",
                wait_timeout=10,
                retry_interval=1,
            )

        self.assertEqual(browser_type.launch_persistent_context.call_count, 1)
        sleep.assert_not_called()

    def test_reports_timeout_for_busy_profile(self):
        browser_type = Mock()
        browser_type.launch_persistent_context.side_effect = RuntimeError(
            "Opening in existing browser session"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "profile is still busy after waiting 0 seconds",
        ):
            chatgpt_worker.launch_chatgpt_context(
                browser_type,
                "profile",
                wait_timeout=0,
                retry_interval=1,
            )


if __name__ == "__main__":
    unittest.main()
