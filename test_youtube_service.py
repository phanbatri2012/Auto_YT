import unittest
from unittest.mock import patch

from auto_yt.services import youtube_service


class YouTubeServiceTests(unittest.TestCase):
    def test_configures_system_trust_store(self):
        with patch.object(youtube_service.truststore, "inject_into_ssl") as inject:
            youtube_service._configure_system_trust_store()

        inject.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
