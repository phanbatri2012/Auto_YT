"""Unit tests for proxy parsing, urllib OpenerDirector builder, and YouTube API proxy routing."""

import unittest
from unittest.mock import MagicMock, patch

from auto_yt.services import proxy_utils
from auto_yt.services import youtube_comments
from auto_yt.services import youtube_publisher


class TestProxyUtils(unittest.TestCase):
    def test_parse_proxy_url_four_parts(self):
        # host:port:user:pass
        raw = "171.238.21.12:34313:DDPT:DDPT"
        expected = "http://DDPT:DDPT@171.238.21.12:34313"
        self.assertEqual(proxy_utils.parse_proxy_url(raw), expected)

    def test_parse_proxy_url_with_special_characters_in_credentials(self):
        raw = "10.0.0.1:8080:user@domain.com:p@ss:word"
        # Since it splits by colon, standard 4-part handles normal alphanumeric passwords
        raw_clean = "10.0.0.1:8080:user_1:pass_1"
        self.assertEqual(
            proxy_utils.parse_proxy_url(raw_clean),
            "http://user_1:pass_1@10.0.0.1:8080",
        )

    def test_parse_proxy_url_two_parts(self):
        # host:port
        raw = "42.114.0.14:35369"
        expected = "http://42.114.0.14:35369"
        self.assertEqual(proxy_utils.parse_proxy_url(raw), expected)

    def test_parse_proxy_url_standard_schemes(self):
        http_proxy = "http://user:pass@1.2.3.4:8080"
        self.assertEqual(proxy_utils.parse_proxy_url(http_proxy), http_proxy)

        socks5_proxy = "socks5://user:pass@1.2.3.4:1080"
        self.assertEqual(proxy_utils.parse_proxy_url(socks5_proxy), socks5_proxy)

        https_proxy = "https://1.2.3.4:8080"
        self.assertEqual(proxy_utils.parse_proxy_url(https_proxy), https_proxy)

    def test_parse_proxy_url_empty_and_invalid(self):
        self.assertIsNone(proxy_utils.parse_proxy_url(None))
        self.assertIsNone(proxy_utils.parse_proxy_url(""))
        self.assertIsNone(proxy_utils.parse_proxy_url("   "))
        self.assertIsNone(proxy_utils.parse_proxy_url("invalid_proxy_format"))
        self.assertIsNone(proxy_utils.parse_proxy_url("ww://UK-fccc5ee9:True"))

    def test_create_proxy_opener_with_proxy(self):
        opener = proxy_utils.create_proxy_opener("171.238.21.12:34313:DDPT:DDPT")
        self.assertIsNotNone(opener)
        # Verify proxy handler is installed
        handlers = [h.__class__.__name__ for h in opener.handlers]
        self.assertIn("ProxyHandler", handlers)

    def test_create_proxy_opener_without_proxy(self):
        opener = proxy_utils.create_proxy_opener(None)
        self.assertIsNotNone(opener)

    def test_required_proxy_refuses_direct_or_invalid_values(self):
        for value in (None, "", "Direct", "invalid_proxy_format"):
            with self.subTest(value=value), self.assertRaises(
                proxy_utils.ProxyConfigurationError
            ):
                proxy_utils.create_proxy_opener(value, require_proxy=True)

    def test_proxy_display_never_exposes_credentials(self):
        self.assertEqual(
            proxy_utils.proxy_display_value(
                "socks5://proxy-user:proxy-pass@1.2.3.4:1080"
            ),
            "socks5://1.2.3.4:1080",
        )
        self.assertEqual(
            proxy_utils.proxy_display_value("1.2.3.4:8080:user:pass"),
            "1.2.3.4:8080",
        )


class TestYouTubeCommentsProxyRouting(unittest.TestCase):
    @patch("auto_yt.services.youtube_comments.create_proxy_opener")
    def test_request_json_passes_proxy(self, mock_create_opener):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"items": []}'
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_create_opener.return_value = mock_opener

        proxy_str = "171.238.21.12:34313:DDPT:DDPT"
        res = youtube_comments._request_json(
            "https://www.googleapis.com/youtube/v3/channels",
            token="fake_token",
            proxy=proxy_str,
        )
        self.assertEqual(res, {"items": []})
        mock_create_opener.assert_called_once_with(proxy_str, require_proxy=True)

    @patch("auto_yt.services.youtube_comments.create_proxy_opener")
    def test_refresh_access_token_passes_proxy(self, mock_create_opener):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"access_token": "new_tok", "expires_in": 3600}'
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_create_opener.return_value = mock_opener

        proxy_str = "42.114.0.14:35369"
        config = {
            "client_id": "test_client",
            "client_secret": "test_secret",
            "redirect_uri": "http://localhost",
        }
        res = youtube_comments.refresh_access_token(
            "fake_refresh", config=config, proxy=proxy_str
        )
        self.assertEqual(res.get("access_token"), "new_tok")
        mock_create_opener.assert_called_once_with(proxy_str, require_proxy=True)

    @patch("auto_yt.services.youtube_comments.create_proxy_opener")
    def test_exchange_authorization_code_passes_proxy(self, mock_create_opener):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"access_token": "auth_tok", "refresh_token": "ref_tok", "expires_in": 3600}'
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_create_opener.return_value = mock_opener

        proxy_str = "171.238.21.12:34313:DDPT:DDPT"
        config = {
            "client_id": "test_client",
            "client_secret": "test_secret",
            "redirect_uri": "http://localhost:8080/callback",
        }
        res = youtube_comments.exchange_authorization_code(
            "auth_code_123",
            config=config,
            code_verifier="verifier_456",
            proxy=proxy_str,
        )
        self.assertEqual(res.get("access_token"), "auth_tok")
        mock_create_opener.assert_called_once_with(proxy_str, require_proxy=True)

    @patch("auto_yt.services.youtube_comments.create_proxy_opener")
    def test_get_authenticated_channels_passes_proxy(self, mock_create_opener):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"items": [{"id": "UC123", "snippet": {"title": "Test Channel"}}]}'
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_create_opener.return_value = mock_opener

        proxy_str = "171.238.21.12:34313:DDPT:DDPT"
        channels = youtube_comments.get_authenticated_channels(
            "fake_token",
            proxy=proxy_str,
        )
        self.assertEqual(len(channels), 1)
        self.assertEqual(channels[0]["channel_id"], "UC123")
        mock_create_opener.assert_called_once_with(proxy_str, require_proxy=True)


class TestYouTubePublisherProxyRouting(unittest.TestCase):
    @patch("auto_yt.services.youtube_publisher.create_proxy_opener")
    def test_api_json_passes_proxy(self, mock_create_opener):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"id": "vid_123"}'
        mock_opener.open.return_value.__enter__.return_value = mock_response
        mock_create_opener.return_value = mock_opener

        proxy_str = "1.55.229.171:12068:user:pass"
        res = youtube_publisher._api_json(
            "https://www.googleapis.com/youtube/v3/videos",
            "fake_token",
            proxy=proxy_str,
        )
        self.assertEqual(res, {"id": "vid_123"})
        mock_create_opener.assert_called_once_with(
            proxy_str, timeout=60, require_proxy=True
        )


if __name__ == "__main__":
    unittest.main()
