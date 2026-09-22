import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

from auto_yt.services import local_browser_service


class LocalBrowserServiceTests(unittest.TestCase):
    @patch("auto_yt.services.local_browser_service.is_browser_process_running")
    @patch("auto_yt.services.local_browser_service.find_running_local_browser_port")
    @patch("auto_yt.services.local_browser_service.resolve_browser_paths")
    @patch("subprocess.Popen")
    def test_start_local_browser_when_running_no_cdp_opens_tab_without_killing(
        self, mock_popen, mock_paths, mock_find_port, mock_is_running
    ):
        mock_paths.return_value = (Path(r"C:\Program Files\CocCoc\browser.exe"), Path(r"C:\User Data"))
        mock_find_port.return_value = None
        mock_is_running.return_value = True

        # Should raise descriptive error indicating tab opened and NOT call taskkill / terminate
        with self.assertRaises(RuntimeError) as ctx:
            local_browser_service.start_local_browser("coccoc", "Default", target_url="https://facebook.com")

        self.assertIn("chưa bật cổng tự động", str(ctx.exception))
        # Verify subprocess.Popen was called to open the target tab
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        self.assertIn("https://facebook.com", args)

    @patch("auto_yt.services.local_browser_service.start_local_browser")
    @patch("playwright.async_api.async_playwright")
    def test_local_browser_session_does_not_close_browser(self, mock_playwright_fn, mock_start_browser):
        async def run():
            mock_start_browser.return_value = {
                "success": True,
                "port": 9222,
                "ws_url": "ws://127.0.0.1:9222/devtools",
            }
            mock_pw = AsyncMock()
            mock_browser = AsyncMock()
            mock_context = AsyncMock()
            mock_browser.contexts = [mock_context]
            mock_pw.chromium.connect_over_cdp.return_value = mock_browser

            mock_cm = AsyncMock()
            mock_cm.start.return_value = mock_pw
            mock_playwright_fn.return_value = mock_cm

            async with local_browser_service.local_browser_session("coccoc", "Default") as (context, browser):
                self.assertEqual(context, mock_context)
                self.assertEqual(browser, mock_browser)

            # Assert browser.close() was NEVER called
            mock_browser.close.assert_not_called()
            # Assert playwright.stop() was called
            mock_pw.stop.assert_called_once()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
