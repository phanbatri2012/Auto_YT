import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from auto_yt import main
from auto_yt.services import account_store, api_security, tts_service


FRONTEND_ORIGIN = "http://127.0.0.1:5173"


def make_request(method: str, path: str, headers: dict | None = None) -> Request:
    raw_headers = [(b"host", b"testserver")]
    raw_headers.extend(
        (name.lower().encode("ascii"), value.encode("ascii"))
        for name, value in (headers or {}).items()
    )
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }
    )


async def successful_endpoint(_request):
    return JSONResponse({"value": 1})


class LocalApiSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_and_csrf_are_required(self):
        unauthorized = await api_security.protect_loopback_api(
            make_request("GET", "/api/value"),
            successful_endpoint,
        )
        self.assertEqual(unauthorized.status_code, 401)

        session_response = await api_security.protect_loopback_api(
            make_request(
                "GET",
                "/api/security/session",
                {"Origin": FRONTEND_ORIGIN},
            ),
            successful_endpoint,
        )
        self.assertEqual(session_response.status_code, 200)
        token = json.loads(session_response.body)["csrf_token"]
        self.assertIn("HttpOnly", session_response.headers["set-cookie"])
        cookie = session_response.headers["set-cookie"].split(";", 1)[0]

        get_response = await api_security.protect_loopback_api(
            make_request("GET", "/api/value", {"Cookie": cookie}),
            successful_endpoint,
        )
        self.assertEqual(get_response.status_code, 200)
        forbidden = await api_security.protect_loopback_api(
            make_request("POST", "/api/value", {"Cookie": cookie}),
            successful_endpoint,
        )
        self.assertEqual(forbidden.status_code, 403)
        response = await api_security.protect_loopback_api(
            make_request(
                "POST",
                "/api/value",
                {"Cookie": cookie, api_security.CSRF_HEADER_NAME: token},
            ),
            successful_endpoint,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    async def test_cross_site_session_bootstrap_is_blocked(self):
        response = await api_security.protect_loopback_api(
            make_request(
                "GET",
                "/api/security/session",
                {"Origin": "https://attacker.example"},
            ),
            successful_endpoint,
        )
        self.assertEqual(response.status_code, 403)


class SecretStorageTests(unittest.TestCase):
    def test_account_and_cookies_are_not_stored_as_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            account_path = root / "account.json"
            session_path = root / "session.json"
            with (
                patch.object(account_store, "ACCOUNT_PATH", account_path),
                patch.object(account_store, "SESSION_PATH", session_path),
            ):
                account_store.save_account(
                    {
                        "email": "local@example.com",
                        "password": "plain-password-for-test",
                        "totp_secret": "totp-secret-for-test",
                        "session_cookie": [{"name": "session", "value": "cookie-for-test"}],
                        "headless": True,
                    }
                )
                raw = account_path.read_text(encoding="utf-8")
                self.assertNotIn("plain-password-for-test", raw)
                self.assertNotIn("totp-secret-for-test", raw)
                self.assertNotIn("cookie-for-test", raw)
                stored = json.loads(raw)[account_store.DEFAULT_ACCOUNT_KEY]
                self.assertTrue(stored["password_encrypted"].startswith("dpapi:"))
                restored = account_store.load_account()
                self.assertEqual(restored["password"], "plain-password-for-test")
                self.assertEqual(restored["totp_secret"], "totp-secret-for-test")
                self.assertEqual(restored["session_cookie"][0]["value"], "cookie-for-test")

    def test_legacy_genmax_key_is_migrated_to_dpapi(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            key_path = Path(temporary_directory) / "genmax_api_key.txt"
            key_path.write_text("legacy-key-for-test", encoding="utf-8")
            with (
                patch.object(tts_service, "API_KEY_FILE", key_path),
                patch.dict("os.environ", {"GENMAX_API_KEY": ""}),
            ):
                self.assertEqual(tts_service._get_api_key(), "legacy-key-for-test")
                self.assertTrue(key_path.read_text(encoding="utf-8").startswith("dpapi:"))


class MediaPathSecurityTests(unittest.TestCase):
    def test_media_paths_are_confined_to_their_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            image = root / "thumbnail.png"
            image.write_bytes(b"png")
            self.assertEqual(
                main._resolve_media_file(root, "thumbnail.png", ".png"),
                image.resolve(),
            )
            with self.assertRaises(HTTPException):
                main._resolve_media_file(root, "..\\account.json", ".png")
            with self.assertRaises(HTTPException):
                main._resolve_media_file(root, "thumbnail.txt", ".png")


if __name__ == "__main__":
    unittest.main()
