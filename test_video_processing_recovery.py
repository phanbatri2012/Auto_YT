import unittest
from unittest.mock import patch

from auto_yt import main
from auto_yt.services.chatgpt_worker import (
    is_core_script_complete,
    select_reusable_outline_response,
    split_outline_parts,
)


class ImmediateThread:
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        self.target()


class VideoProcessingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.voice_patch = patch.object(
            main.voice_config,
            "get_voice",
            return_value={
                "id": main.AUDIO_VOICE_ID,
                "name": "Giọng kiểm thử",
            },
        )
        self.get_voice = self.voice_patch.start()

    def tearDown(self):
        self.voice_patch.stop()
        main._jobs.clear()

    def test_oversized_single_outline_part_is_split_for_multiple_body_turns(self):
        outline_lines = [
            f"- Chi tiết câu chuyện số {index}: " + ("nội dung " * 35)
            for index in range(30)
        ]
        outline = "[PHAN]\n" + "\n".join(outline_lines)

        parts = split_outline_parts(outline, max_chars=3000)

        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 3000 for part in parts))
        self.assertEqual(
            " ".join("\n".join(parts).split()),
            " ".join("\n".join(outline_lines).split()),
        )

    def test_completed_late_outline_is_reused_from_the_same_chat(self):
        turns = [
            ("user", "Tạo dàn ý"),
            ("assistant", ""),
            ("assistant", "[PHAN]\nPhần một\n\n[PHAN]\nPhần hai"),
        ]

        response = select_reusable_outline_response(turns)

        self.assertIn("Phần một", response)
        self.assertEqual(len(split_outline_parts(response)), 2)

    def test_core_completion_depends_on_sections_not_a_length_threshold(self):
        transcript = "x" * 10000
        state = {
            "intro": "Intro",
            "body_parts": ["Body"],
            "outro": "Outro",
            "expected_body_parts": 1,
        }

        self.assertTrue(is_core_script_complete(transcript, state))

        state["body_parts"] = []
        self.assertFalse(is_core_script_complete(transcript, state))

    def test_video_without_voice_uses_prompt_default_voice(self):
        prompt_voice_id = "a39e4493-3a8a-4be8-bd13-b96f2f5c4906"
        with (
            patch.object(
                main,
                "_get_prompt_default_voice_id",
                return_value=prompt_voice_id,
            ),
            patch.object(
                main,
                "_try_start_chatgpt_operation",
                return_value=False,
            ),
        ):
            main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        self.get_voice.assert_called_once_with(prompt_voice_id)

    def test_continue_accepts_chat_checkpoint_before_outline_was_saved(self):
        video = {
            "id": 80,
            "title": "Title",
            "transcript": "Transcript",
            "prompt_version": "version-key",
        }
        with (
            patch.object(main.db, "get_video", return_value=video),
            patch.object(
                main,
                "load_checkpoint",
                return_value={
                    "chat_url": "https://chatgpt.com/c/created-chat",
                    "outline_parts": [],
                },
            ),
            patch.object(main, "_try_start_chatgpt_operation", return_value=False),
        ):
            response = main.continue_video_generation(80)

        self.assertIn("job_id", response)
        self.assertEqual(main._jobs[response["job_id"]]["status"], "error")

    def test_partial_body_is_saved_as_draft_without_audio_submission(self):
        partial_script = (
            "### [INTRO]\nIntro\n\n"
            "### [BODY]\nOnly body one\n\n"
            "### [OUTRO]\n\n"
            "### [METADATA & QUIZ]\n\n"
            "### [CHAPTERS]\n"
        )
        worker_result = {
            "script": partial_script,
            "chat_url": "https://chatgpt.com/c/test",
            "warning": "body 2/3: generation did not finish",
            "failed_step": "body 2/3",
            "complete_for_audio": False,
        }

        with (
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(main.threading, "Thread", ImmediateThread),
            patch.object(main, "get_video_transcript", return_value="Transcript"),
            patch.object(main, "get_video_title", return_value="Title"),
            patch.object(
                main,
                "process_prompt_via_chatgpt",
                return_value=worker_result,
            ),
            patch.object(main.db, "save_video", return_value=71) as save_video,
            patch.object(
                main.db,
                "update_video_generation",
                return_value=True,
            ) as update_generation,
            patch.object(main, "_ensure_audio_task") as ensure_audio,
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        job = main._jobs[response["job_id"]]
        self.assertEqual(job["status"], "done")
        self.assertFalse(job["result"]["complete_for_audio"])
        self.assertIn("body 2/3", job["result"]["generation_warning"])
        save_video.assert_called_once_with(
            "https://www.youtube.com/watch?v=generic",
            "Title",
            "Transcript",
            main.INITIAL_GENERATED_SCRIPT,
            "",
            "version-key",
            main.AUDIO_VOICE_ID,
            "Giọng kiểm thử",
        )
        update_generation.assert_called_once_with(
            71,
            partial_script,
            "https://chatgpt.com/c/test",
        )
        ensure_audio.assert_not_called()

    def test_hard_worker_failure_keeps_the_early_draft(self):
        with (
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(main.threading, "Thread", ImmediateThread),
            patch.object(main, "get_video_transcript", return_value="Transcript"),
            patch.object(main, "get_video_title", return_value="Title"),
            patch.object(
                main,
                "process_prompt_via_chatgpt",
                side_effect=RuntimeError("browser failed before outline"),
            ),
            patch.object(main.db, "save_video", return_value=73) as save_video,
            patch.object(
                main.db,
                "update_video_generation",
            ) as update_generation,
            patch.object(main, "_ensure_audio_task") as ensure_audio,
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        job = main._jobs[response["job_id"]]
        self.assertEqual(job["status"], "error")
        self.assertIn("Bản nháp video #73", job["error"])
        save_video.assert_called_once()
        update_generation.assert_not_called()
        ensure_audio.assert_not_called()

    def test_chapter_timeout_saves_video_and_submits_audio_once(self):
        recovered_script = (
            "### [INTRO]\nIntro\n\n"
            "### [BODY]\nComplete body\n\n"
            "### [OUTRO]\nOutro\n\n"
            "### [METADATA & QUIZ]\nMetadata\n\n"
            "### [CHAPTERS]\n"
        )
        worker_result = {
            "script": recovered_script,
            "chat_url": "https://chatgpt.com/c/test",
            "warning": "chapters: generation did not finish",
            "failed_step": "chapters",
            "complete_for_audio": True,
        }
        audio_task = {
            "video_id": 72,
            "task_id": "single-task",
            "status": "pending",
            "audio_url": "",
            "error": "",
            "updated_at": "2026-08-04T00:00:00+00:00",
        }

        with (
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(main.threading, "Thread", ImmediateThread),
            patch.object(main, "get_video_transcript", return_value="Transcript"),
            patch.object(main, "get_video_title", return_value="Title"),
            patch.object(
                main,
                "process_prompt_via_chatgpt",
                return_value=worker_result,
            ),
            patch.object(main.db, "save_video", return_value=72),
            patch.object(
                main.db,
                "update_video_generation",
                return_value=True,
            ),
            patch.object(
                main.db,
                "get_video",
                return_value={"generated_script": recovered_script},
            ),
            patch.object(
                main,
                "_ensure_audio_task",
                return_value=audio_task,
            ) as ensure_audio,
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        job = main._jobs[response["job_id"]]
        self.assertEqual(job["status"], "done")
        self.assertTrue(job["result"]["complete_for_audio"])
        self.assertEqual(job["result"]["audio_task"]["task_id"], "single-task")
        ensure_audio.assert_called_once_with(72)


if __name__ == "__main__":
    unittest.main()
