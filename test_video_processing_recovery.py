import unittest
from unittest.mock import patch

from auto_yt import main
from auto_yt.services.chatgpt_worker import (
    get_minimum_body_part_chars,
    is_core_script_complete,
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
        self.voice_patch.start()

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

    def test_short_core_script_is_not_eligible_for_audio(self):
        transcript = "x" * 10000
        state = {
            "intro": "i" * 1000,
            "body_parts": ["b" * 1000],
            "outro": "o" * 1000,
            "expected_body_parts": 1,
        }

        self.assertFalse(is_core_script_complete(transcript, state))

        state["body_parts"] = ["b" * 5500]
        self.assertTrue(is_core_script_complete(transcript, state))

    def test_body_target_scales_with_transcript_and_number_of_parts(self):
        minimum_chars = get_minimum_body_part_chars(
            transcript="x" * 72000,
            intro="i" * 2000,
            body_part_count=8,
        )

        self.assertGreaterEqual(minimum_chars, 6100)

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
