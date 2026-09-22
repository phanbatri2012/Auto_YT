import sys
import urllib.parse
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

import auto_yt.services.database as db
from auto_yt.services import fb_token_service
from auto_yt.services.proxy_utils import ProxyConfigurationError


class FBTokenServiceUnitTests(unittest.TestCase):
    _original_db_path = None
    _test_db_path = None

    @classmethod
    def setUpClass(cls):
        cls._original_db_path = db.DB_PATH
        cls._test_db_path = Path(__file__).parent / "data" / "test_token_temp.db"
        if cls._test_db_path.exists():
            cls._test_db_path.unlink()
        db.DB_PATH = cls._test_db_path
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls._original_db_path
        if cls._test_db_path and cls._test_db_path.exists():
            try:
                cls._test_db_path.unlink()
            except Exception:
                pass

    @patch("auto_yt.services.fb_token_service._build_opener_for_profile")
    def test_fetch_permanent_page_tokens_from_user_token(self, mock_opener_builder):
        mock_opener = MagicMock()
        mock_opener_builder.return_value = mock_opener

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'''{
            "data": [
                {
                    "id": "61585373181110",
                    "name": "Goc Khuat Viet Su",
                    "access_token": "EAAGTESTPERMANENTTOKEN123",
                    "category": "Education",
                    "link": "https://www.facebook.com/61585373181110"
                },
                {
                    "id": "1122334455",
                    "name": "Music Page",
                    "access_token": "EAAGTESTPERMANENTTOKEN456",
                    "category": "Music"
                }
            ]
        }'''
        mock_resp.__enter__.return_value = mock_resp
        mock_opener.open.return_value = mock_resp

        pages = fb_token_service.fetch_permanent_page_tokens_from_user_token(
            user_token="EAAGUSERTOKENXYZ",
            profile_id="local_coccoc_default",
        )

        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0]["page_id"], "61585373181110")
        self.assertEqual(pages[0]["access_token"], "EAAGTESTPERMANENTTOKEN123")
        self.assertEqual(pages[1]["name"], "Music Page")
        request = mock_opener.open.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertNotIn("access_token", query)
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer EAAGUSERTOKENXYZ",
        )

    @patch("auto_yt.services.fb_token_service.fetch_permanent_page_tokens_from_user_token")
    @patch("auto_yt.services.fb_token_service.channel_browser_session")
    async def _async_test_extract_permanent_fb_tokens(self, mock_session, mock_fetch_tokens):
        mock_fetch_tokens.return_value = [
            {
                "page_id": "61585373181110",
                "name": "Goc Khuat Viet Su",
                "access_token": "EAAGEXTRACTEDTOKEN999",
                "category": "Education",
                "is_permanent": True,
            }
        ]

        mock_page = AsyncMock()
        mock_page.url = "https://developers.facebook.com/tools/explorer/"
        mock_page.evaluate.return_value = {"token": "EAAGUSERSHORT123", "source": "dom_input"}

        mock_context = AsyncMock()
        mock_context.new_page.return_value = mock_page

        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = (mock_context, None, {"type": "local", "id": "local_coccoc_default"})
        mock_cm.__aexit__.return_value = None
        mock_session.return_value = mock_cm

        res = await fb_token_service.extract_permanent_fb_tokens(
            profile_id="local_coccoc_default",
            target_page_id="61585373181110",
        )

        self.assertTrue(res["success"])
        self.assertTrue(res["logged_in"])
        self.assertEqual(res["total_pages"], 1)
        self.assertIsNotNone(res["matched_page"])
        self.assertNotIn("access_token", res["matched_page"])
        self.assertTrue(res["matched_page"]["token_configured"])
        self.assertEqual(
            db.get_fb_crossposter_runtime_settings("61585373181110")["target_access_token"],
            "EAAGEXTRACTEDTOKEN999",
        )

    def test_extract_tokens_async_wrapper(self):
        import asyncio
        asyncio.run(self._async_test_extract_permanent_fb_tokens())

    @patch("auto_yt.services.fb_token_service._build_opener_for_profile")
    def test_exchange_to_permanent_token(self, mock_opener_builder):
        mock_opener = MagicMock()
        mock_opener_builder.return_value = mock_opener

        # Mock /me/accounts response
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'''{
            "data": [
                {
                    "id": "61585373181110",
                    "name": "Goc Khuat Viet Su",
                    "access_token": "EAAGPERMANENTTOKENABC",
                    "category": "Education"
                }
            ]
        }'''
        mock_resp.__enter__.return_value = mock_resp
        mock_opener.open.return_value = mock_resp

        result = fb_token_service.exchange_to_permanent_token(
            input_token="EAAGSHORTTOKEN",
            profile_id="local_coccoc_default",
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["total_pages"], 1)
        self.assertEqual(result["pages"][0]["access_token"], "EAAGPERMANENTTOKENABC")

    @patch("auto_yt.services.fb_token_service._build_opener_for_profile")
    def test_exchange_credentials_are_sent_in_post_body_not_url(self, mock_opener_builder):
        mock_opener = MagicMock()
        mock_opener_builder.return_value = mock_opener

        exchange_response = MagicMock()
        exchange_response.read.return_value = b'{"access_token":"EAALongLivedToken123"}'
        exchange_response.__enter__.return_value = exchange_response
        pages_response = MagicMock()
        pages_response.read.return_value = b'{"data":[]}'
        pages_response.__enter__.return_value = pages_response
        direct_response = MagicMock()
        direct_response.read.return_value = b'{"id":"page-id","name":"Page"}'
        direct_response.__enter__.return_value = direct_response
        mock_opener.open.side_effect = [
            exchange_response,
            pages_response,
            direct_response,
        ]

        fb_token_service.exchange_to_permanent_token(
            input_token="EAAShortToken123",
            app_id="app-id",
            app_secret="app-secret",
            profile_id="local_coccoc_default",
        )

        request = mock_opener.open.call_args_list[0].args[0]
        self.assertEqual(request.full_url, f"{fb_token_service.GRAPH_API_BASE}/oauth/access_token")
        self.assertNotIn("app-secret", request.full_url)
        post_fields = urllib.parse.parse_qs(request.data.decode("utf-8"))
        self.assertEqual(post_fields["client_secret"], ["app-secret"])

    @patch("auto_yt.services.fb_token_service.exchange_to_permanent_token")
    def test_exchange_endpoint_stores_but_does_not_return_token(self, mock_exchange):
        from auto_yt import main

        token = "EAAPageTokenStoredOnly1234567890"
        mock_exchange.return_value = {
            "success": True,
            "pages": [{
                "page_id": "page-secure",
                "name": "Secure Page",
                "category": "Education",
                "access_token": token,
            }],
        }
        response = main.exchange_fb_crossposter_token(
            main.FBCrossPosterExchangeTokenPayload(
                token="EAAInputToken1234567890",
                target_page_id="page-secure",
                profile_id="local_coccoc_default",
            )
        )

        self.assertNotIn(token, str(response))
        self.assertTrue(response["page"]["token_configured"])
        self.assertEqual(
            db.get_fb_crossposter_runtime_settings("page-secure")["target_access_token"],
            token,
        )

    @patch("auto_yt.services.fb_token_service.parse_profile_target")
    def test_gpm_token_requests_fail_closed_without_proxy(self, mock_parse):
        mock_parse.return_value = {
            "type": "gpm",
            "id": "gpm-profile",
            "proxy_info": "Direct",
        }
        with self.assertRaises(ProxyConfigurationError):
            fb_token_service._build_opener_for_profile("gpm-profile")


if __name__ == "__main__":
    unittest.main()
