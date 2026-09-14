import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException

from auto_yt import main
from auto_yt.services import chatgpt_browser_service


class ChatGPTBrowserServiceManagerTests(unittest.TestCase):
    def test_blank_service_page_navigates_to_chatgpt(self):
        page = Mock()
        page.url = "about:blank"
        context = Mock()
        context.pages = [page]

        chatgpt_browser_service._initialize_service_page(context)

        page.goto.assert_called_once_with(
            chatgpt_browser_service.SERVICE_HOME_URL,
            wait_until="domcontentloaded",
            timeout=chatgpt_browser_service.SERVICE_NAVIGATION_TIMEOUT_MS,
        )

    def test_existing_service_page_is_not_replaced(self):
        page = Mock()
        page.url = "https://chatgpt.com/c/existing-chat"
        context = Mock()
        context.pages = [page]

        chatgpt_browser_service._initialize_service_page(context)

        page.goto.assert_not_called()

    def test_navigation_failure_does_not_stop_browser_service(self):
        page = Mock()
        page.url = "about:blank"
        page.goto.side_effect = RuntimeError("temporary navigation failure")
        context = Mock()
        context.pages = [page]

        chatgpt_browser_service._initialize_service_page(context)

        page.goto.assert_called_once()

    def test_status_requires_both_owner_process_and_loopback_cdp(self):
        with (
            patch.object(
                chatgpt_browser_service,
                "_read_json",
                return_value={
                    "pid": 4321,
                    "cdp_url": "http://127.0.0.1:43123",
                    "started_at": "now",
                },
            ),
            patch.object(chatgpt_browser_service, "_pid_is_alive", return_value=True),
            patch.object(chatgpt_browser_service, "_cdp_is_ready", return_value=True),
        ):
            status = chatgpt_browser_service.get_browser_service_status()

        self.assertTrue(status["connected"])
        self.assertEqual(status["state"], "connected")
        self.assertEqual(status["pid"], 4321)

    def test_show_existing_browser_updates_state_without_launching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            state_path.write_text(
                '{"pid": 4321, "cdp_url": "http://127.0.0.1:43123", '
                '"window_visible": false}',
                encoding="utf-8",
            )
            with (
                patch.object(
                    chatgpt_browser_service, "SERVICE_STATE_PATH", state_path
                ),
                patch.object(chatgpt_browser_service, "_pid_is_alive", return_value=True),
                patch.object(chatgpt_browser_service, "_cdp_is_ready", return_value=True),
                patch.object(
                    chatgpt_browser_service, "_set_cdp_window_visibility"
                ) as set_visibility,
            ):
                result = chatgpt_browser_service.set_browser_service_window_visibility(
                    True
                )

            self.assertTrue(result["window_visible"])
            set_visibility.assert_called_once_with(
                "http://127.0.0.1:43123", True
            )

    def test_show_endpoint_is_available_while_chatgpt_job_is_busy(self):
        visible = {
            "connected": True,
            "process_alive": True,
            "window_visible": True,
        }
        with patch.object(
            main.chatgpt_browser_service,
            "set_browser_service_window_visibility",
            return_value=visible,
        ) as set_visibility:
            result = main.show_chatgpt_browser_service_window()

        self.assertTrue(result["window_visible"])
        set_visibility.assert_called_once_with(True)

    def test_endpoint_rejects_non_loopback_cdp(self):
        with (
            patch.object(
                chatgpt_browser_service,
                "_read_json",
                return_value={"pid": 4321, "cdp_url": "http://0.0.0.0:43123"},
            ),
            patch.object(chatgpt_browser_service, "_pid_is_alive", return_value=True),
        ):
            endpoint = chatgpt_browser_service.get_browser_service_endpoint()

        self.assertEqual(endpoint, "")

    def test_start_spawns_once_and_waits_for_ready_state(self):
        stopped = {
            "connected": False,
            "process_alive": False,
            "state": "stopped",
        }
        connected = {
            "connected": True,
            "process_alive": True,
            "state": "connected",
        }
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                chatgpt_browser_service,
                "SERVICE_STATE_PATH",
                Path(temp_dir) / "state.json",
            ),
            patch.object(
                chatgpt_browser_service,
                "SERVICE_STOP_PATH",
                Path(temp_dir) / "stop.json",
            ),
            patch.object(
                chatgpt_browser_service,
                "SERVICE_ERROR_PATH",
                Path(temp_dir) / "error.log",
            ),
            patch.object(
                chatgpt_browser_service,
                "get_browser_service_status",
                side_effect=[stopped, connected],
            ),
            patch.object(chatgpt_browser_service, "_spawn_service_process") as spawn,
        ):
            result = chatgpt_browser_service.start_browser_service(timeout=1)

        self.assertTrue(result["connected"])
        spawn.assert_called_once_with()

    def test_stop_uses_instance_scoped_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            stop_path = Path(temp_dir) / "stop.json"
            state_path.write_text(
                '{"pid": 4321, "instance_id": "owner-a"}',
                encoding="utf-8",
            )
            stopped = {
                "connected": False,
                "process_alive": False,
                "state": "stopped",
            }
            with (
                patch.object(
                    chatgpt_browser_service, "SERVICE_STATE_PATH", state_path
                ),
                patch.object(
                    chatgpt_browser_service, "SERVICE_STOP_PATH", stop_path
                ),
                patch.object(
                    chatgpt_browser_service,
                    "_pid_is_alive",
                    side_effect=[True, False],
                ),
                patch.object(
                    chatgpt_browser_service,
                    "get_browser_service_status",
                    return_value=stopped,
                ),
            ):
                result = chatgpt_browser_service.stop_browser_service(timeout=1)

            self.assertFalse(result["connected"])
            self.assertIn('"instance_id": "owner-a"', stop_path.read_text())

    def test_start_endpoint_does_not_report_false_success(self):
        with (
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(
                main.chatgpt_browser_service,
                "start_browser_service",
                return_value={
                    "connected": False,
                    "message": "profile is busy",
                },
            ),
            self.assertRaises(HTTPException) as error,
        ):
            main.start_chatgpt_browser_service()

        self.assertEqual(error.exception.status_code, 503)

    def test_stop_endpoint_rejects_owner_that_did_not_exit(self):
        with (
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(
                main.chatgpt_browser_service,
                "stop_browser_service",
                return_value={
                    "process_alive": True,
                    "message": "service is still stopping",
                },
            ),
            self.assertRaises(HTTPException) as error,
        ):
            main.stop_chatgpt_browser_service()

        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
