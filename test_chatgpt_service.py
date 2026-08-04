import unittest
from unittest.mock import Mock, patch

from auto_yt.services import chatgpt_service


class ChatGptServiceTests(unittest.TestCase):
    def test_long_worker_is_not_limited_by_a_total_process_timeout(self):
        completed_process = Mock(
            returncode=0,
            stdout=b"Generated script\n###CHAT_URL###\nhttps://chatgpt.com/c/test",
            stderr=b"",
        )

        with patch.object(
            chatgpt_service.subprocess,
            "run",
            return_value=completed_process,
        ) as run_worker:
            result = chatgpt_service.process_prompt_via_chatgpt("transcript")

        self.assertNotIn("timeout", run_worker.call_args.kwargs)
        self.assertEqual(result["script"], "Generated script")
        self.assertEqual(result["chat_url"], "https://chatgpt.com/c/test")


if __name__ == "__main__":
    unittest.main()
