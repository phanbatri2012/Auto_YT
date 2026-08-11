import unittest
from unittest.mock import Mock, patch

from auto_yt.services import chatgpt_service, chatgpt_worker


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

    def test_partial_worker_metadata_is_returned_to_the_backend(self):
        completed_process = Mock(
            returncode=0,
            stdout=(
                b"Partial script\n"
                b"###CHAT_URL###https://chatgpt.com/c/test\n"
                b"###WORKER_META###"
                b'{"warning":"chapters: timed out",'
                b'"failed_step":"chapters",'
                b'"complete_for_audio":true}'
            ),
            stderr=b"",
        )

        with patch.object(
            chatgpt_service.subprocess,
            "run",
            return_value=completed_process,
        ):
            result = chatgpt_service.process_prompt_via_chatgpt("transcript")

        self.assertEqual(result["script"], "Partial script")
        self.assertEqual(result["chat_url"], "https://chatgpt.com/c/test")
        self.assertEqual(result["failed_step"], "chapters")
        self.assertTrue(result["complete_for_audio"])
        self.assertIn("timed out", result["warning"])

    def test_chapter_timeout_preserves_complete_core_script(self):
        def fail_at_chapters(_transcript, state):
            state.update(
                {
                    "chat_url": "https://chatgpt.com/c/test",
                    "current_step": "chapters",
                    "expected_body_parts": 2,
                    "intro": "Intro complete",
                    "body_parts": ["Body one", "Body two"],
                    "outro": "Outro complete",
                    "metadata": "Metadata complete",
                }
            )
            raise TimeoutError("generation did not finish")

        with patch.object(
            chatgpt_worker,
            "_run_complete",
            side_effect=fail_at_chapters,
        ):
            result = chatgpt_worker.run("transcript")

        self.assertTrue(result["complete_for_audio"])
        self.assertEqual(result["failed_step"], "chapters")
        self.assertIn("Body one\n\nBody two", result["script"])
        self.assertIn("Metadata complete", result["script"])
        self.assertIn("### [CHAPTERS]\n\n", result["script"])

    def test_body_timeout_never_marks_partial_script_ready_for_audio(self):
        def fail_during_body(_transcript, state):
            state.update(
                {
                    "chat_url": "https://chatgpt.com/c/test",
                    "current_step": "body 2/3",
                    "expected_body_parts": 3,
                    "intro": "Intro complete",
                    "body_parts": ["Only body one"],
                }
            )
            raise TimeoutError("generation did not finish")

        with patch.object(
            chatgpt_worker,
            "_run_complete",
            side_effect=fail_during_body,
        ):
            result = chatgpt_worker.run("transcript")

        self.assertFalse(result["complete_for_audio"])
        self.assertEqual(result["failed_step"], "body 2/3")
        self.assertIn("Only body one", result["script"])


if __name__ == "__main__":
    unittest.main()
