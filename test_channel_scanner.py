"""Unit and integration tests for Channel Scanner and Local Browser Service."""

import unittest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from auto_yt.main import app
from auto_yt.services import api_security
from auto_yt.services.local_browser_service import (
    list_local_browser_profiles,
)
from auto_yt.services.channel_scanner_service import (
    parse_profile_target,
)


class TestChannelScanner(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        session_res = self.client.get("/api/security/session", headers={"Origin": "http://127.0.0.1:5173"})
        if session_res.status_code == 200:
            csrf_token = session_res.json().get("csrf_token")
            self.client.headers.update({
                "Origin": "http://127.0.0.1:5173",
                api_security.CSRF_HEADER_NAME: csrf_token,
            })

    def test_local_browser_discovery(self):
        """Verify local Chromium browsers are detected and profiles parsed."""
        profiles = list_local_browser_profiles()
        self.assertIsInstance(profiles, list)
        for p in profiles:
            self.assertIn("id", p)
            self.assertIn("browser_name", p)
            self.assertIn("profile_dir", p)
            self.assertIn("display_label", p)
            self.assertEqual(p["type"], "local")

    def test_parse_profile_target(self):
        """Verify parsing of GPM vs Local profile identifiers."""
        local_target = parse_profile_target("local_coccoc_default")
        self.assertEqual(local_target["type"], "local")
        self.assertEqual(local_target["browser_key"], "coccoc")

        gpm_target = parse_profile_target("gpm_uuid_12345")
        self.assertEqual(gpm_target["type"], "gpm")
        self.assertEqual(gpm_target["id"], "gpm_uuid_12345")

    def test_api_local_profiles_endpoint(self):
        """GET /api/channels/local-profiles should return list of local profiles."""
        response = self.client.get("/api/channels/local-profiles")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("items", data)
        self.assertIn("total", data)
        self.assertIsInstance(data["items"], list)

    @patch("auto_yt.services.channel_scanner_service.open_channel_platform_browser")
    def test_api_open_browser_endpoint(self, mock_open):
        """POST /api/channels/open-browser should trigger browser opening."""
        mock_open.return_value = {
            "success": True,
            "profile_id": "local_coccoc_default",
            "platform": "facebook",
            "url": "https://www.facebook.com/pages",
            "message": "Đã mở Cốc Cốc",
        }

        response = self.client.post(
            "/api/channels/open-browser",
            json={"profile_id": "local_coccoc_default", "platform": "facebook"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["platform"], "facebook")

    @patch("auto_yt.services.channel_scanner_service.scan_youtube_channel")
    def test_api_scan_youtube_endpoint(self, mock_scan):
        """POST /api/channels/scan/youtube should return scanned channel details."""
        mock_scan.return_value = {
            "success": True,
            "logged_in": True,
            "channel": {
                "channel_id": "UC_TEST_123456",
                "title": "Kênh Đánh Giá Công Nghệ",
                "thumbnail_url": "https://yt3.ggpht.com/avatar.jpg",
                "handle": "@review_tech",
                "gpm_profile_id": "local_coccoc_default",
            },
            "message": "Đã quét kênh thành công",
        }

        response = self.client.post(
            "/api/channels/scan/youtube",
            json={"profile_id": "local_coccoc_default", "auto_save": True},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["channel"]["channel_id"], "UC_TEST_123456")
        self.assertEqual(data["channel"]["title"], "Kênh Đánh Giá Công Nghệ")

    @patch("auto_yt.services.channel_scanner_service.scan_facebook_pages")
    def test_api_scan_facebook_endpoint(self, mock_scan):
        """POST /api/channels/scan/facebook should return discovered fanpages."""
        mock_scan.return_value = {
            "success": True,
            "logged_in": True,
            "pages": [
                {
                    "id": "fb_1029384756",
                    "name": "TechReview Official Page",
                    "page_id": "1029384756",
                    "avatar_url": "https://fb.com/avatar.png",
                    "link": "https://facebook.com/1029384756",
                }
            ],
            "message": "Tìm thấy 1 Fanpage",
        }

        response = self.client.post(
            "/api/channels/scan/facebook",
            json={"profile_id": "local_coccoc_default"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["logged_in"])
        self.assertEqual(len(data["pages"]), 1)
        self.assertEqual(data["pages"][0]["name"], "TechReview Official Page")

    @patch("auto_yt.services.channel_scanner_service.scan_tiktok_account")
    def test_api_scan_tiktok_endpoint(self, mock_scan):
        """POST /api/channels/scan/tiktok should return scanned TikTok account."""
        mock_scan.return_value = {
            "success": True,
            "logged_in": True,
            "account": {
                "id": "tt_techreview_official",
                "name": "Tech Review VN",
                "handle": "@techreview_official",
                "avatar_url": "https://tiktok.com/avatar.jpg",
            },
            "message": "Quét thành công",
        }

        response = self.client.post(
            "/api/channels/scan/tiktok",
            json={"profile_id": "local_coccoc_default"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["logged_in"])
        self.assertEqual(data["account"]["handle"], "@techreview_official")


if __name__ == "__main__":
    unittest.main()
