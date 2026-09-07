import unittest
from unittest.mock import MagicMock, patch

from auto_yt.services import youtube_service


class YouTubeServiceTests(unittest.TestCase):
    def test_configures_system_trust_store(self):
        with patch.object(youtube_service.truststore, "inject_into_ssl") as inject:
            youtube_service._configure_system_trust_store()

        inject.assert_called_once_with()

    def test_title_request_keeps_tls_verification_enabled(self):
        response = MagicMock()
        response.text = "<title>Tiêu đề kiểm thử - YouTube</title>"

        with patch.object(
            youtube_service.curl_requests,
            "get",
            return_value=response,
        ) as get:
            title = youtube_service.get_video_title(
                "https://www.youtube.com/watch?v=video-id"
            )

        self.assertEqual(title, "Tiêu đề kiểm thử")
        self.assertNotIn("verify", get.call_args.kwargs)
        self.assertEqual(
            get.call_args.kwargs["timeout"],
            youtube_service.YOUTUBE_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status.assert_called_once_with()

    def test_transcript_download_keeps_tls_verification_enabled(self):
        class FakeYoutubeDL:
            last_options = {}

            def __init__(self, options):
                type(self).last_options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download=False):
                self.assert_download_disabled = not download
                return {
                    "automatic_captions": {
                        "vi": [{
                            "ext": "json3",
                            "url": "https://www.youtube.com/api/timedtext",
                        }]
                    }
                }

        response = MagicMock()
        response.json.return_value = {
            "events": [{"segs": [{"utf8": "Nội dung "}, {"utf8": "mới"}]}]
        }

        with (
            patch.object(youtube_service, "YoutubeDL", FakeYoutubeDL),
            patch.object(
                youtube_service.curl_requests,
                "get",
                return_value=response,
            ) as get,
        ):
            transcript = youtube_service.get_video_transcript(
                "https://www.youtube.com/watch?v=video-id"
            )

        self.assertEqual(transcript, "Nội dung mới")
        self.assertNotIn("nocheckcertificate", FakeYoutubeDL.last_options)
        self.assertNotIn("verify", get.call_args.kwargs)
        response.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
