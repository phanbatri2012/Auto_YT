import unittest
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, patch

from auto_yt.services import chatgpt_service, chatgpt_worker


class ChatGptServiceTests(unittest.TestCase):
    def test_narrative_sanitizer_removes_isolated_editorial_lines(self):
        first_paragraph = (
            "Lực lượng được tổ chức linh hoạt để thích nghi với chiến trường "
            "và giữ được sức mạnh trong những thời điểm quyết định. " * 3
        )
        second_paragraph = (
            "Cách bố trí này tạo ra thế chủ động, buộc đối phương phải phân "
            "tán nguồn lực và không thể duy trì ưu thế lâu dài. " * 3
        )
        editorial_note = (
            "Làm rõ vai trò từng lực lượng\n"
            "Tăng nhịp kể và sức hút"
        )

        result = chatgpt_worker.sanitize_narrative_response(
            f"{first_paragraph}\n\n{editorial_note}\n\n{second_paragraph}"
        )

        self.assertNotIn(editorial_note, result)
        self.assertIn(first_paragraph.strip(), result)
        self.assertIn(second_paragraph.strip(), result)

    def test_narrative_sanitizer_preserves_short_complete_sentences(self):
        first_paragraph = (
            "Lực lượng được tổ chức linh hoạt để thích nghi với chiến trường "
            "và giữ được sức mạnh trong những thời điểm quyết định. " * 3
        )
        second_paragraph = (
            "Cách bố trí này tạo ra thế chủ động, buộc đối phương phải phân "
            "tán nguồn lực và không thể duy trì ưu thế lâu dài. " * 3
        )

        result = chatgpt_worker.sanitize_narrative_response(
            f"{first_paragraph}\n\nNhưng chưa hết.\n\n{second_paragraph}"
        )

        self.assertIn("Nhưng chưa hết.", result)

    def test_narrative_sanitizer_never_erases_the_only_response_block(self):
        self.assertEqual(
            chatgpt_worker.sanitize_narrative_response("Body complete"),
            "Body complete",
        )

    def test_video_script_builder_sanitizes_all_narrative_sections(self):
        long_paragraph = (
            "Diễn biến được kể liên tục, đầy đủ sự kiện và giữ đúng trình tự "
            "để người nghe có thể theo dõi câu chuyện một cách tự nhiên. " * 3
        )
        state = {
            "intro": f"Ghi chú biên tập\n\n{long_paragraph}",
            "body_parts": [
                f"{long_paragraph}\n\nTăng nhịp kể\n\n{long_paragraph}"
            ],
            "outro": f"Làm rõ thông điệp cuối\n\n{long_paragraph}",
            "metadata": "Metadata",
            "chapters": "00:00 - Mở đầu",
        }

        script = chatgpt_worker.build_video_script(state)

        self.assertNotIn("Ghi chú biên tập", script)
        self.assertNotIn("Tăng nhịp kể", script)
        self.assertNotIn("Làm rõ thông điệp cuối", script)
        self.assertIn("Metadata", script)
        self.assertIn("00:00 - Mở đầu", script)

    def test_complete_script_sanitizer_preserves_non_narrative_sections(self):
        long_paragraph = (
            "Lực lượng được tổ chức linh hoạt để thích nghi với chiến trường "
            "và giữ được sức mạnh trong những thời điểm quyết định. " * 3
        )
        script = (
            f"### [INTRO]\n{long_paragraph}\n\n"
            f"### [BODY]\n{long_paragraph}\n\n"
            "Làm rõ vai trò từng lực lượng\n"
            "Tăng nhịp kể và sức hút\n\n"
            f"{long_paragraph}\n\n"
            f"### [OUTRO]\n{long_paragraph}\n\n"
            "### [METADATA & QUIZ]\n"
            "TIÊU ĐỀ: Giữ nguyên metadata\n\n"
            "### [CHAPTERS]\n00:00 - Mở đầu\n"
        )

        result = chatgpt_worker.sanitize_generated_script(script)

        self.assertNotIn("Làm rõ vai trò từng lực lượng", result)
        self.assertNotIn("Tăng nhịp kể và sức hút", result)
        self.assertIn("TIÊU ĐỀ: Giữ nguyên metadata", result)
        self.assertIn("00:00 - Mở đầu", result)

    def test_video_id_is_forwarded_for_durable_worker_checkpoints(self):
        completed_process = Mock(
            returncode=0,
            stdout=b"Generated script",
            stderr=b"",
        )
        with patch.object(
            chatgpt_service.subprocess,
            "run",
            return_value=completed_process,
        ) as run_worker:
            chatgpt_service.process_prompt_via_chatgpt(
                "transcript",
                "version-key",
                video_id=122,
            )

        worker_env = run_worker.call_args.kwargs["env"]
        self.assertEqual(worker_env["PROMPT_VERSION"], "version-key")
        self.assertEqual(worker_env["VIDEO_ID"], "122")

    def test_pipeline_snapshot_is_forwarded_to_the_worker(self):
        pipeline = {
            "metadata": False,
            "chapters": True,
            "thumbnail_with_text": False,
            "thumbnail_without_text": True,
            "audio": False,
        }
        completed_process = Mock(
            returncode=0,
            stdout=b"Generated script",
            stderr=b"",
        )
        with patch.object(
            chatgpt_service.subprocess,
            "run",
            return_value=completed_process,
        ) as run_worker:
            chatgpt_service.process_prompt_via_chatgpt(
                "transcript",
                "version-key",
                video_id=122,
                pipeline=pipeline,
            )

        worker_env = run_worker.call_args.kwargs["env"]
        self.assertEqual(
            json.loads(worker_env["PROMPT_PIPELINE_JSON"]),
            pipeline,
        )

    def test_disabled_optional_pipeline_steps_send_no_chatgpt_prompt(self):
        page = Mock()
        page.url = "https://chatgpt.com/g/g-p-test/project"
        context = Mock()
        context.pages = [page]
        state = {
            "chat_url": "",
            "current_step": "outro",
            "expected_body_parts": 1,
            "outline_parts": ["Part one"],
            "intro": "Intro complete",
            "body_parts": ["Body complete"],
            "outro": "Outro complete",
            "metadata": "",
            "chapters": "",
            "thumb_text": "",
            "thumb_notext": "",
            "pipeline": {
                "metadata": False,
                "chapters": False,
                "thumbnail_with_text": False,
                "thumbnail_without_text": False,
                "audio": False,
            },
        }
        playwright_manager = Mock()
        playwright_manager.__enter__ = Mock(
            return_value=Mock(chromium=Mock())
        )
        playwright_manager.__exit__ = Mock(return_value=False)

        with (
            patch.object(chatgpt_worker, "gpt_profile_dir", return_value=Path.cwd()),
            patch.object(
                chatgpt_worker,
                "sync_playwright",
                return_value=playwright_manager,
            ),
            patch.object(
                chatgpt_worker,
                "launch_chatgpt_context",
                return_value=context,
            ),
            patch.object(chatgpt_worker, "ensure_expected_project_page"),
            patch.object(chatgpt_worker, "send_prompt") as send_prompt,
            patch.object(chatgpt_worker, "send_thumbnail_prompt") as send_thumbnail,
            patch.object(chatgpt_worker, "persist_generation_state"),
        ):
            result = chatgpt_worker._run_complete("Transcript", state)

        send_prompt.assert_not_called()
        send_thumbnail.assert_not_called()
        self.assertEqual(result["pipeline"], state["pipeline"])
        self.assertTrue(result["complete_for_audio"])

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

    def test_outline_failure_with_a_created_chat_is_recoverable(self):
        def fail_after_chat_created(_transcript, state):
            state.update({
                "chat_url": "https://chatgpt.com/c/created-chat",
                "current_step": "outline",
            })
            raise RuntimeError("response arrived late")

        with patch.object(
            chatgpt_worker,
            "_run_complete",
            side_effect=fail_after_chat_created,
        ):
            result = chatgpt_worker.run("transcript")

        self.assertFalse(result["complete_for_audio"])
        self.assertEqual(result["chat_url"], "https://chatgpt.com/c/created-chat")
        self.assertEqual(result["failed_step"], "outline")

    def test_run_restores_matching_checkpoint_before_opening_chat(self):
        transcript = "saved transcript"
        checkpoint = {
            "chat_url": "https://chatgpt.com/c/saved",
            "current_step": "body 1/3",
            "expected_body_parts": 3,
            "outline_parts": ["one", "two", "three"],
            "intro": "Saved intro",
            "body_parts": ["Saved body"],
            "outro": "",
            "metadata": "",
            "chapters": "",
            "thumb_text": "",
            "thumb_notext": "",
            "transcript_fingerprint": hashlib.sha256(
                transcript.encode("utf-8")
            ).hexdigest(),
        }

        def inspect_state(_transcript, state):
            self.assertEqual(state["chat_url"], checkpoint["chat_url"])
            self.assertEqual(state["body_parts"], ["Saved body"])
            return {
                "script": "resumed",
                "chat_url": state["chat_url"],
                "warning": "",
                "failed_step": "",
                "complete_for_audio": True,
            }

        with (
            patch.dict("os.environ", {"VIDEO_ID": "122"}),
            patch.object(chatgpt_worker, "load_checkpoint", return_value=checkpoint),
            patch.object(chatgpt_worker, "_run_complete", side_effect=inspect_state),
        ):
            result = chatgpt_worker.run(transcript)

        self.assertEqual(result["script"], "resumed")

    def test_queue_pipeline_snapshot_wins_when_recovering_a_checkpoint(self):
        transcript = "saved transcript"
        pipeline = {
            "metadata": False,
            "chapters": True,
            "thumbnail_with_text": False,
            "thumbnail_without_text": False,
            "audio": True,
        }
        checkpoint = {
            "chat_url": "https://chatgpt.com/c/saved",
            "current_step": "body 1/2",
            "expected_body_parts": 2,
            "outline_parts": ["one", "two"],
            "intro": "Saved intro",
            "body_parts": ["Saved body"],
            "outro": "",
            "pipeline": {
                key: True for key in pipeline
            },
            "transcript_fingerprint": hashlib.sha256(
                transcript.encode("utf-8")
            ).hexdigest(),
        }

        def inspect_state(_transcript, state):
            self.assertEqual(state["pipeline"], pipeline)
            return {
                "script": "resumed",
                "chat_url": state["chat_url"],
                "warning": "",
                "failed_step": "",
                "complete_for_audio": True,
                "pipeline": state["pipeline"],
            }

        with (
            patch.dict(
                "os.environ",
                {
                    "VIDEO_ID": "122",
                    "PROMPT_PIPELINE_JSON": json.dumps(pipeline),
                },
            ),
            patch.object(chatgpt_worker, "load_checkpoint", return_value=checkpoint),
            patch.object(chatgpt_worker, "_run_complete", side_effect=inspect_state),
        ):
            result = chatgpt_worker.run(transcript)

        self.assertEqual(result["pipeline"], pipeline)


if __name__ == "__main__":
    unittest.main()
