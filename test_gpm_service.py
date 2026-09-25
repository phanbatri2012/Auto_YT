"""Unit and integration tests for GPM-Login v3 Local API service and database mapping."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from auto_yt.services import gpm_service, database as db, api_security
from auto_yt.main import app

client = TestClient(app)

# Authenticate test client for loopback API
session_res = client.get("/api/security/session", headers={"Origin": "http://127.0.0.1:5173"})
if session_res.status_code == 200:
    csrf_token = session_res.json()["csrf_token"]
    client.headers.update({
        "Origin": "http://127.0.0.1:5173",
        api_security.CSRF_HEADER_NAME: csrf_token,
    })


def test_gpm_config_load_and_save(tmp_path=None):
    from pathlib import Path
    import tempfile
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        temp_config_path = tmp_path / "gpm_config.json"
        with patch("auto_yt.services.gpm_service.GPM_CONFIG_PATH", temp_config_path), \
             patch("auto_yt.services.gpm_service.DATA_DIR", tmp_path):
            
            # Test default config
            cfg = gpm_service.get_gpm_config()
            assert cfg["api_url"] == gpm_service.DEFAULT_GPM_API_URL
            assert cfg["auto_stop_on_finish"] is True
            assert cfg["timeout_seconds"] == gpm_service.DEFAULT_TIMEOUT_SECONDS

            # Test save config
            saved = gpm_service.save_gpm_config(
                api_url="http://127.0.0.1:19995",
                auto_stop_on_finish=False,
                timeout_seconds=20.0,
            )
            assert saved["api_url"] == "http://127.0.0.1:19995"
            assert saved["auto_stop_on_finish"] is False
            assert saved["timeout_seconds"] == 20.0

            # Verify persisted on disk
            loaded = gpm_service.get_gpm_config()
            assert loaded["api_url"] == "http://127.0.0.1:19995"
            assert loaded["auto_stop_on_finish"] is False
    print("✓ test_gpm_config_load_and_save passed")


def test_gpm_check_connection_online():
    mock_response_data = {
        "success": True,
        "data": {
            "current_page": 1,
            "per_page": 1,
            "total": 5,
            "data": [{"id": "profile-1", "name": "Channel 1"}]
        },
        "sender": "GPMLogin Global v3.0.2"
    }

    with patch("auto_yt.services.gpm_service._request_gpm_api", return_value=mock_response_data):
        res = gpm_service.check_gpm_connection("http://127.0.0.1:19995")
        assert res["online"] is True
        assert res["total_profiles"] == 5
        assert res["sender"] == "GPMLogin Global v3.0.2"
    print("✓ test_gpm_check_connection_online passed")


def test_gpm_check_connection_offline():
    with patch("auto_yt.services.gpm_service._request_gpm_api", side_effect=gpm_service.GpmConnectionError("Offline")):
        res = gpm_service.check_gpm_connection("http://127.0.0.1:19995")
        assert res["online"] is False
        assert res["total_profiles"] == 0
    print("✓ test_gpm_check_connection_offline passed")


def test_gpm_list_profiles():
    mock_response = {
        "success": True,
        "data": {
            "current_page": 1,
            "per_page": 100,
            "total": 2,
            "last_page": 1,
            "data": [
                {
                    "id": "uuid-1",
                    "name": "Kênh Review Phim",
                    "raw_proxy": "socks5://103.1.2.3:1080",
                    "browser": {"name": "chrome", "version": "137.0"},
                    "os": "windows",
                    "note": "Dedicated IP",
                    "created_at": "2026-09-18",
                    "tags": ["review", "vn"]
                },
                {
                    "id": "uuid-2",
                    "name": "Kênh Tin Tức",
                    "raw_proxy": "http://103.4.5.6:8080",
                    "browser": {"name": "chrome", "version": "137.0"},
                    "os": "windows",
                    "note": "",
                    "created_at": "2026-09-18",
                    "tags": []
                }
            ]
        }
    }

    with patch("auto_yt.services.gpm_service._request_gpm_api", return_value=mock_response):
        result = gpm_service.list_gpm_profiles(search="Kênh", page=1, page_size=100)
        assert result["total"] == 2
        assert len(result["items"]) == 2
        p1 = result["items"][0]
        assert p1["id"] == "uuid-1"
        assert p1["name"] == "Kênh Review Phim"
        assert p1["raw_proxy"] == "socks5://103.1.2.3:1080"
    print("✓ test_gpm_list_profiles passed")


def test_gpm_start_and_stop_profile():
    mock_start_data = {
        "success": True,
        "data": {
            "profile_id": "uuid-1",
            "remote_debugging_port": 40444,
            "websocket_debugging_url": "ws://127.0.0.1:40444/devtools/browser/abc",
            "driver_path": "C:\\chromedriver.exe"
        }
    }
    with patch("auto_yt.services.gpm_service._request_gpm_api", return_value=mock_start_data):
        data = gpm_service.start_gpm_profile("uuid-1", skip_proxy_check=True)
        assert data["remote_debugging_port"] == 40444
        assert "ws://127.0.0.1:40444" in data["websocket_debugging_url"]

    mock_stop_data = {"success": True, "data": None, "message": "OK"}
    with patch("auto_yt.services.gpm_service._request_gpm_api", return_value=mock_stop_data):
        success = gpm_service.stop_gpm_profile("uuid-1")
        assert success is True
    print("✓ test_gpm_start_and_stop_profile passed")


def test_database_gpm_channel_mapping():
    channel = db.save_youtube_channel(
        channel_id="UC_TEST_GPM_CHANNEL_1",
        title="Test GPM Channel",
        thumbnail_url="http://example.com/icon.png",
        access_token_encrypted="",
        refresh_token_encrypted="",
        token_expiry="",
        scope="",
    )
    channel_db_id = channel["id"]

    updated = db.update_youtube_channel(
        channel_db_id,
        gpm_profile_id="gpm-uuid-999",
        gpm_profile_name="Profile Kênh 1",
        gpm_proxy_info="socks5://proxy-user:proxy-pass@1.2.3.4:1080",
        interaction_mode="gpm_browser",
        auto_heart=1,
    )
    assert updated["gpm_profile_id"] == "gpm-uuid-999"
    assert updated["gpm_profile_name"] == "Profile Kênh 1"
    assert updated["gpm_proxy_info"] == "socks5://proxy-user:proxy-pass@1.2.3.4:1080"
    assert updated["interaction_mode"] == "gpm_browser"
    assert updated["auto_heart"] == 1

    fetched = db.get_youtube_channel(channel_db_id)
    assert fetched["gpm_profile_id"] == "gpm-uuid-999"
    assert fetched["gpm_profile_name"] == "Profile Kênh 1"
    assert fetched["auto_heart"] == 1
    internal = db.get_youtube_channel(channel_db_id, include_tokens=True)
    assert internal["gpm_proxy_info"] == "socks5://proxy-user:proxy-pass@1.2.3.4:1080"

    # Test toggling auto_heart to 0
    updated_off = db.update_youtube_channel(channel_db_id, auto_heart=0)
    assert updated_off["auto_heart"] == 0

    db.delete_youtube_channel(channel_db_id)
    print("✓ test_database_gpm_channel_mapping passed")


def test_fastapi_gpm_endpoints():
    # Test GET /api/gpm/config
    res = client.get("/api/gpm/config")
    assert res.status_code == 200
    assert "api_url" in res.json()

    # Test POST /api/gpm/config
    res = client.post("/api/gpm/config", json={"api_url": "http://127.0.0.1:19995", "auto_stop_on_finish": True})
    assert res.status_code == 200
    assert res.json()["api_url"] == "http://127.0.0.1:19995"

    # Test GET /api/gpm/status with mock
    with patch("auto_yt.services.gpm_service.check_gpm_connection", return_value={"online": True, "total_profiles": 3}):
        res = client.get("/api/gpm/status")
        assert res.status_code == 200
        assert res.json()["online"] is True
        assert res.json()["total_profiles"] == 3

    # Test GET /api/gpm/profiles with mock
    mock_list = {
        "total": 1,
        "items": [{"id": "p1", "name": "Profile 1", "raw_proxy": "127.0.0.1:8080:user:pass"}]
    }
    with patch("auto_yt.services.gpm_service.list_gpm_profiles", return_value=mock_list):
        res = client.get("/api/gpm/profiles")
        assert res.status_code == 200
        assert len(res.json()["items"]) == 1
        assert "raw_proxy" not in res.json()["items"][0]
        assert res.json()["items"][0]["proxy_display"] == "127.0.0.1:8080"

    # Test POST /api/gpm/profiles/{id}/start
    mock_start = {
        "profile_id": "p1",
        "remote_debugging_port": 12345,
        "websocket_debugging_url": "ws://127.0.0.1:12345/devtools"
    }
    with patch("auto_yt.services.gpm_service.start_gpm_profile", return_value=mock_start):
        res = client.post("/api/gpm/profiles/p1/start", json={})
        assert res.status_code == 200
        assert res.json()["remote_debugging_port"] == 12345

    # Test POST /api/gpm/profiles/{id}/stop
    with patch("auto_yt.services.gpm_service.stop_gpm_profile", return_value=True):
        res = client.post("/api/gpm/profiles/p1/stop")
        assert res.status_code == 200
        assert res.json()["success"] is True

    # Test POST /api/gpm/profiles/{id}/open-url
    mock_open_result = {
        "success": True,
        "profile_id": "p1",
        "url": "https://accounts.google.com/o/oauth2/auth",
        "message": "Đã mở URL trong GPM Profile p1",
    }
    with patch(
        "auto_yt.services.gpm_youtube_automation.open_url_in_gpm_profile",
        return_value=mock_open_result,
    ):
        res = client.post(
            "/api/gpm/profiles/p1/open-url",
            json={"url": "https://accounts.google.com/o/oauth2/auth"},
        )
        assert res.status_code == 200
        assert res.json()["success"] is True
        assert res.json()["url"] == "https://accounts.google.com/o/oauth2/auth"
    print("✓ test_fastapi_gpm_endpoints passed")


def test_gpm_already_open_handling():
    """Test start_gpm_profile handles ALREADY_OPEN gracefully."""
    mock_resp = {"success": False, "data": None, "message": "ALREADY_OPEN"}
    with patch("auto_yt.services.gpm_service._request_gpm_api", return_value=mock_resp), \
         patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=None):
        info = gpm_service.start_gpm_profile("p_already_open")
        assert info["success"] is True
        assert info["status"] == "already_open"
        assert info["already_running_no_cdp"] is True
    print("✓ test_gpm_already_open_handling passed")


def test_open_url_preserves_query_parameters_in_cdp_json_new():
    """Test that open_url_in_gpm_profile fully encodes URLs with query params for /json/new."""
    import asyncio
    import urllib.parse
    from auto_yt.services import gpm_youtube_automation

    complex_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        "client_id=123.apps.googleusercontent.com&"
        "redirect_uri=http%3A%2F%2F127.0.0.1%3A8080%2Fapi%2Fyoutube-comments%2Foauth%2Fcallback&"
        "response_type=code&"
        "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube.force-ssl&"
        "access_type=offline&state=xyz"
    )

    recorded_urls = []

    class DummyResponse:
        def __init__(self, data):
            self.data = data
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def dummy_urlopen(req, timeout=5.0):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        if "/json/new" in url:
            return DummyResponse(json.dumps({"id": "tab-123"}).encode("utf-8"))
        return DummyResponse(b"")

    mock_launch = {"remote_debugging_port": 19999}
    with patch("auto_yt.services.gpm_youtube_automation.start_gpm_profile", return_value=mock_launch), \
         patch("urllib.request.urlopen", side_effect=dummy_urlopen):
        result = asyncio.run(gpm_youtube_automation.open_url_in_gpm_profile("p1", complex_url))
        assert result["success"] is True
        assert result["url"] == complex_url

        # Verify that the URL sent to /json/new has the entire target_url percent-encoded
        new_tab_calls = [u for u in recorded_urls if "/json/new?" in u]
        assert len(new_tab_calls) >= 1
        expected_encoded = urllib.parse.quote(complex_url, safe="")
        assert f"/json/new?{expected_encoded}" in new_tab_calls[0]
        # Crucial invariant: there should be NO unencoded '&' in the /json/new URL (only 1 query param)
        query_part = new_tab_calls[0].split("/json/new?")[1]
        assert "&" not in query_part, "Query parameters must be percent-encoded to prevent DevTools parameter splitting"
    print("✓ test_open_url_preserves_query_parameters_in_cdp_json_new passed")


def test_open_tab_in_running_gpm_process_preserves_query_parameters():
    """Test that open_tab_in_running_gpm_process encodes complex URLs for /json/new properly."""
    import urllib.parse
    complex_url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=abc&response_type=code&scope=yt"
    recorded_urls = []

    class DummyResponse:
        def __init__(self, data):
            self.data = data
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def dummy_urlopen(req, timeout=3.0):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        recorded_urls.append(url)
        if "/json/new" in url:
            return DummyResponse(json.dumps({"id": "tab-456"}).encode("utf-8"))
        return DummyResponse(b"")

    mock_process_output = json.dumps([{
        "CommandLine": r'"C:\chrome.exe" --remote-debugging-port=18888 --user-data-dir="C:\Profiles\p1"',
        "ExecutablePath": r"C:\chrome.exe",
        "ProcessId": 1234
    }])
    mock_run_result = MagicMock(stdout=mock_process_output)

    with patch("subprocess.run", return_value=mock_run_result), \
         patch("urllib.request.urlopen", side_effect=dummy_urlopen):
        result = gpm_service.open_tab_in_running_gpm_process("p1", complex_url)
        assert result["success"] is True
        assert result["method"] == "cdp_http"

        new_tab_calls = [u for u in recorded_urls if "/json/new?" in u]
        assert len(new_tab_calls) >= 1
        expected_encoded = urllib.parse.quote(complex_url, safe="")
        assert f"/json/new?{expected_encoded}" in new_tab_calls[0]
        query_part = new_tab_calls[0].split("/json/new?")[1]
        assert "&" not in query_part
    print("✓ test_open_tab_in_running_gpm_process_preserves_query_parameters passed")


def test_post_comment_reply_via_gpm_with_auto_heart():
    """Test post_comment_reply_via_gpm targets linked comment, clicks heart and submits reply."""
    from auto_yt.services import gpm_youtube_automation

    class AsyncMockObject:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    mock_heart_btn = AsyncMock()
    mock_heart_btn.get_attribute = AsyncMock(side_effect=lambda attr: "false" if attr == "aria-pressed" else "Thả tim")
    mock_heart_btn.click = AsyncMock()

    mock_reply_btn = AsyncMock()
    mock_reply_btn.click = AsyncMock()

    mock_thread = AsyncMock()
    async def thread_query_selector(sel):
        if "heart" in sel or "tim" in sel:
            return mock_heart_btn
        if "reply" in sel or "Trả lời" in sel or "Phản hồi" in sel:
            return mock_reply_btn
        return None
    mock_thread.query_selector = AsyncMock(side_effect=thread_query_selector)

    mock_editable = AsyncMock()
    mock_editable.fill = AsyncMock()

    mock_submit_btn = AsyncMock()
    mock_submit_btn.click = AsyncMock()

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock()
    async def page_wait_for_selector(sel, timeout=5000):
        if "comment-thread" in sel:
            return mock_thread
        if "contenteditable" in sel:
            return mock_editable
        if "submit-button" in sel:
            return mock_submit_btn
        return None
    mock_page.wait_for_selector = AsyncMock(side_effect=page_wait_for_selector)
    mock_page.close = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def dummy_session(profile_id, auto_stop=True):
        yield (mock_context, AsyncMock())

    with patch("auto_yt.services.gpm_youtube_automation.gpm_browser_session", side_effect=dummy_session):
        res = asyncio.run(
            gpm_youtube_automation.post_comment_reply_via_gpm(
                profile_id="p1",
                video_url="https://www.youtube.com/watch?v=TEST_VID_1",
                comment_text="Cảm ơn bạn đã theo dõi!",
                comment_id="Ugx_TEST_CMT_1",
                auto_heart=True,
            )
        )
        assert res["success"] is True
        assert res["comment_id"] == "Ugx_TEST_CMT_1"
        assert res["hearted"] is True
        assert "lc=Ugx_TEST_CMT_1" in res["video_url"]
        mock_heart_btn.click.assert_called_once()
        mock_reply_btn.click.assert_called_once()
        mock_editable.fill.assert_called_once_with("Cảm ơn bạn đã theo dõi!")
        mock_submit_btn.click.assert_called_once()

    print("✓ test_post_comment_reply_via_gpm_with_auto_heart passed")


def test_gpm_session_reuses_running_profile_without_duplicate_window():
    """Verify that if a profile is already open, gpm_browser_session reuses it and never kills it on exit."""
    mock_running_coords = {
        "remote_debugging_port": 19998,
        "websocket_debugging_url": "ws://127.0.0.1:19998/devtools/browser/xyz",
        "selenium_remote_debug_address": "127.0.0.1:19998",
        "profile_id": "gkvs-profile",
        "status": "already_open",
        "already_running": True,
    }

    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)
    mock_browser.contexts = [mock_context]

    api_calls = []
    def fake_request_api(endpoint, *args, **kwargs):
        api_calls.append(endpoint)
        return {"success": True, "data": {}}

    with patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=mock_running_coords), \
         patch("auto_yt.services.gpm_service._request_gpm_api", side_effect=fake_request_api), \
         patch("playwright.async_api.async_playwright") as mock_pw_factory:

        mock_pw_cm = MagicMock()
        mock_pw_cm.start = AsyncMock(return_value=mock_playwright)
        mock_pw_factory.return_value = mock_pw_cm

        async def run_session():
            async with gpm_service.gpm_browser_session("gkvs-profile", auto_stop=True) as (ctx, br):
                assert ctx is mock_context

        asyncio.run(run_session())

        # Assert no /profiles/start and no /profiles/stop API calls were made to GPM
        assert not any("start" in call for call in api_calls), f"Unexpected start call in {api_calls}"
        assert not any("stop" in call for call in api_calls), f"Unexpected stop call in {api_calls}"
        # Assert browser was NOT closed because it was already running before
        mock_browser.close.assert_not_called()

    print("✓ test_gpm_session_reuses_running_profile_without_duplicate_window passed")


def test_gpm_session_stops_profile_when_started_by_session():
    """Verify that if a profile was started by Auto_YT, it is cleanly stopped when auto_stop=True."""
    mock_launch_data = {
        "success": True,
        "data": {
            "remote_debugging_port": 19997,
            "websocket_debugging_url": "ws://127.0.0.1:19997/devtools/browser/abc",
            "selenium_remote_debug_address": "127.0.0.1:19997",
            "profile_id": "temp-profile",
        }
    }

    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_playwright = AsyncMock()
    mock_playwright.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)
    mock_browser.contexts = [mock_context]

    api_calls = []
    def fake_request_api(endpoint, *args, **kwargs):
        api_calls.append(endpoint)
        if "start" in endpoint:
            return mock_launch_data
        return {"success": True, "data": {}}

    with patch("auto_yt.services.gpm_service.find_running_gpm_profile_coordinates", return_value=None), \
         patch("auto_yt.services.gpm_service._request_gpm_api", side_effect=fake_request_api), \
         patch("playwright.async_api.async_playwright") as mock_pw_factory:

        mock_pw_cm = MagicMock()
        mock_pw_cm.start = AsyncMock(return_value=mock_playwright)
        mock_pw_factory.return_value = mock_pw_cm

        async def run_session():
            async with gpm_service.gpm_browser_session("temp-profile", auto_stop=True) as (ctx, br):
                assert ctx is mock_context

        asyncio.run(run_session())

        # Assert start was called, browser was closed, and stop was called
        assert any("start" in call for call in api_calls), f"Expected start call in {api_calls}"
        assert any("stop" in call for call in api_calls), f"Expected stop call in {api_calls}"
        mock_browser.close.assert_called_once()

    print("✓ test_gpm_session_stops_profile_when_started_by_session passed")


if __name__ == "__main__":
    test_gpm_config_load_and_save()
    test_gpm_check_connection_online()
    test_gpm_check_connection_offline()
    test_gpm_list_profiles()
    test_gpm_start_and_stop_profile()
    test_gpm_already_open_handling()
    test_open_url_preserves_query_parameters_in_cdp_json_new()
    test_open_tab_in_running_gpm_process_preserves_query_parameters()
    test_database_gpm_channel_mapping()
    test_fastapi_gpm_endpoints()
    test_post_comment_reply_via_gpm_with_auto_heart()
    test_gpm_session_reuses_running_profile_without_duplicate_window()
    test_gpm_session_stops_profile_when_started_by_session()
    print("\n🎉 ALL GPM TESTS PASSED SUCCESSFULLY!")
