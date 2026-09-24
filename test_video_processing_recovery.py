import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from auto_yt import main
from auto_yt.services.chatgpt_worker import (
    is_core_script_complete,
    select_reusable_outline_response,
    split_outline_parts,
)
from auto_yt.services.chatgpt_runtime import (
    CHATGPT_LOGIN_REQUIRED_MESSAGE,
    ChatGPTAttentionRequiredError,
)


class ImmediateThread:
    def __init__(self, target, daemon):
        self.target = target

    def start(self):
        self.target()


def audio_review(status="pending"):
    return {
        "video_id": 72,
        "script_hash": "script-hash",
        "status": status,
        "reviewed_at": "",
        "updated_at": "2026-08-04T00:00:00+00:00",
        "report": {
            "can_approve": status != "blocked",
            "errors": [] if status != "blocked" else [{"message": "Incomplete"}],
            "warnings": [],
            "metrics": {"word_count": 3},
        },
    }


class VideoProcessingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            main.db,
            "DB_PATH",
            Path(self.temp_directory.name) / "database.db",
        )
        self.database_patch.start()
        main.db.init_db()
        main._video_queue_worker_active = False
        main._jobs.clear()
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
        self.database_patch.stop()
        self.temp_directory.cleanup()
        main._video_queue_worker_active = False
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
            patch.object(main, "_kick_video_queue"),
        ):
            main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        self.get_voice.assert_called_once_with(prompt_voice_id)

    def test_duplicate_active_request_reuses_the_existing_queue_job(self):
        request = main.VideoRequest(
            url="https://www.youtube.com/watch?v=same-video",
            prompt_version="version-key",
            voice_id=main.AUDIO_VOICE_ID,
        )
        with patch.object(main, "_kick_video_queue"):
            first = main.process_video(request)
            second = main.process_video(request)

        self.assertEqual(first["job_id"], second["job_id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(
            main.db.get_system_job(first["job_id"])["status"],
            "queued",
        )

    def test_new_video_job_keeps_a_pipeline_snapshot(self):
        pipeline = {
            "title": True,
            "slug": True,
            "description": True,
            "tags": True,
            "pinned_comment": True,
            "quiz": True,
            "chapters": False,
            "thumbnail_with_text": False,
            "thumbnail_without_text": True,
            "audio": False,
            "video_render": False,
            "youtube_upload": False,
            "youtube_schedule": False,
        }
        with (
            patch.object(
                main,
                "_get_prompt_production_snapshot",
                return_value={"prompt_version": "version-key", "pipeline": pipeline},
            ),
            patch.object(main, "_kick_video_queue"),
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=pipeline-snapshot",
                    prompt_version="version-key",
                )
            )

        job = main.db.get_system_job(response["job_id"])
        self.assertEqual(job["payload"]["pipeline"], pipeline)

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
                "get_video",
                return_value={"video_status": main.db.VIDEO_STATUS_ACTIVE},
            ),
            patch.object(
                main.db,
                "update_video_generation",
                return_value=True,
            ) as update_generation,
            patch.object(
                main,
                "_prepare_audio_review",
                return_value=audio_review("blocked"),
            ),
            patch.object(
                main,
                "load_checkpoint",
                return_value={
                    "chat_url": "https://chatgpt.com/c/test",
                    "current_step": "body 2/3",
                    "intro": "Intro",
                    "body_parts": ["Only body one"],
                },
            ),
            patch.object(main, "_schedule_video_queue_wakeup"),
            patch.object(main, "_ensure_audio_task") as ensure_audio,
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        job = main._jobs[response["job_id"]]
        self.assertEqual(job["status"], "retry_wait")
        self.assertFalse(job["result"]["complete_for_audio"])
        self.assertIn("body 2/3", job["result"]["generation_warning"])
        save_video.assert_called_once()
        saved = save_video.call_args.kwargs
        self.assertEqual(saved["url"], "https://www.youtube.com/watch?v=generic")
        self.assertEqual(saved["generated_script"], main.INITIAL_GENERATED_SCRIPT)
        self.assertEqual(saved["prompt_version"], "version-key")
        self.assertEqual(saved["voice_id"], main.AUDIO_VOICE_ID)
        self.assertEqual(saved["voice_name"], "Giọng kiểm thử")
        self.assertEqual(saved["tts_provider_id"], "genmax")
        self.assertEqual(saved["voice_revision"], 1)
        self.assertIn('"provider_id": "genmax"', saved["voice_snapshot_json"])
        update_generation.assert_called_once_with(
            71,
            partial_script,
            "https://chatgpt.com/c/test",
        )
        ensure_audio.assert_not_called()

    def test_new_video_sanitizes_script_again_before_database_update(self):
        long_paragraph = (
            "Lực lượng được tổ chức linh hoạt để thích nghi với chiến trường "
            "và giữ được sức mạnh trong những thời điểm quyết định. " * 3
        )
        worker_script = (
            f"### [INTRO]\n{long_paragraph}\n\n"
            f"### [BODY]\n{long_paragraph}\n\n"
            "Làm rõ vai trò từng lực lượng\n"
            "Tăng nhịp kể và sức hút\n\n"
            f"{long_paragraph}\n\n"
            f"### [OUTRO]\n{long_paragraph}\n\n"
            "### [METADATA & QUIZ]\nMetadata\n\n"
            "### [CHAPTERS]\n"
        )
        worker_result = {
            "script": worker_script,
            "chat_url": "https://chatgpt.com/c/test",
            "warning": "chapters: generation did not finish",
            "failed_step": "chapters",
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
            patch.object(main.db, "save_video", return_value=74),
            patch.object(
                main.db,
                "update_video_generation",
                return_value=True,
            ) as update_generation,
            patch.object(
                main,
                "_prepare_audio_review",
                return_value=audio_review("blocked"),
            ),
            patch.object(main, "_ensure_audio_task") as ensure_audio,
        ):
            main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        saved_script = update_generation.call_args.args[1]
        self.assertNotIn("Làm rõ vai trò từng lực lượng", saved_script)
        self.assertNotIn("Tăng nhịp kể và sức hút", saved_script)
        self.assertIn("Metadata", saved_script)
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

    def test_expired_chatgpt_login_pauses_job_and_starts_automatic_login(self):
        with (
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(main.threading, "Thread", ImmediateThread),
            patch.object(main, "get_video_transcript", return_value="Transcript"),
            patch.object(main, "get_video_title", return_value="Title"),
            patch.object(
                main,
                "process_prompt_via_chatgpt",
                side_effect=ChatGPTAttentionRequiredError(
                    CHATGPT_LOGIN_REQUIRED_MESSAGE
                ),
            ),
            patch.object(main.db, "save_video", return_value=731),
            patch.object(
                main,
                "_schedule_automatic_chatgpt_login",
                return_value=True,
            ) as schedule_login,
            patch.object(main, "_ensure_audio_task") as ensure_audio,
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=needs-login",
                    prompt_version="version-key",
                )
            )

        job = main.db.get_system_job(response["job_id"])
        self.assertEqual(job["status"], "paused")
        self.assertEqual(
            job["result"]["attention_required"],
            "chatgpt_verification",
        )
        self.assertEqual(job["result"]["automatic_login"], "pending")
        self.assertIn("Auto Login", job["progress"])
        self.assertEqual(job["recovery_count"], 0)
        schedule_login.assert_called_once()
        self.assertEqual(schedule_login.call_args.args[0], response["job_id"])
        ensure_audio.assert_not_called()

    def test_transient_chatgpt_start_failure_retries_saved_draft(self):
        transient_error = (
            "ChatGPT page did not become ready after bounded same-page recovery. "
            "No prompt was sent."
        )
        with (
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(main.threading, "Thread", ImmediateThread),
            patch.object(main, "get_video_transcript", return_value="Transcript"),
            patch.object(main, "get_video_title", return_value="Title"),
            patch.object(
                main,
                "process_prompt_via_chatgpt",
                side_effect=RuntimeError(transient_error),
            ) as process_chatgpt,
            patch.object(main.db, "save_video", return_value=75) as save_video,
            patch.object(
                main.db,
                "get_video",
                return_value={"video_status": main.db.VIDEO_STATUS_ACTIVE},
            ),
            patch.object(
                main.db,
                "update_video_generation",
            ) as update_generation,
            patch.object(main, "_schedule_video_queue_wakeup") as schedule_wakeup,
            patch.object(main, "_ensure_audio_task") as ensure_audio,
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        job = main.db.get_system_job(response["job_id"])
        self.assertEqual(job["status"], "retry_wait")
        self.assertEqual(job["resume_from_step"], "chatgpt_start")
        self.assertEqual(job["recovery_count"], 1)
        self.assertIn("ChatGPT", job["progress"])
        self.assertIn("No prompt was sent", job["error"])
        save_video.assert_called_once()
        process_chatgpt.assert_called_once()
        update_generation.assert_not_called()
        ensure_audio.assert_not_called()
        schedule_wakeup.assert_any_call(main.VIDEO_RECOVERY_DELAYS_SECONDS[0])

    def test_youtube_rate_limit_before_draft_is_retried_automatically(self):
        with (
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(main.threading, "Thread", ImmediateThread),
            patch.object(
                main,
                "get_video_transcript",
                side_effect=RuntimeError("HTTP Error 429: Too Many Requests"),
            ),
            patch.object(main, "get_video_title") as get_title,
            patch.object(main, "process_prompt_via_chatgpt") as process_chatgpt,
            patch.object(main.db, "save_video") as save_video,
            patch.object(main, "_schedule_video_queue_wakeup") as schedule_wakeup,
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=rate-limited",
                    prompt_version="version-key",
                )
            )

        job = main.db.get_system_job(response["job_id"])
        self.assertEqual(job["status"], "retry_wait")
        self.assertEqual(job["resume_from_step"], "youtube_transcript")
        self.assertEqual(job["recovery_count"], 1)
        self.assertIn("YouTube", job["progress"])
        self.assertIn("429", job["error"])
        get_title.assert_not_called()
        process_chatgpt.assert_not_called()
        save_video.assert_not_called()
        schedule_wakeup.assert_any_call(main.YOUTUBE_RECOVERY_DELAYS_SECONDS[0])

    def test_chapter_timeout_still_starts_audio_when_core_script_is_complete(self):
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
                "get_video",
                return_value={"video_status": main.db.VIDEO_STATUS_ACTIVE},
            ),
            patch.object(
                main.db,
                "update_video_generation",
                return_value=True,
            ),
            patch.object(
                main,
                "_auto_review_and_create_audio",
                return_value=(audio_review("approved"), audio_task, ""),
            ) as auto_audio,
            patch.object(
                main,
                "load_checkpoint",
                return_value={
                    "chat_url": "https://chatgpt.com/c/test",
                    "current_step": "chapters",
                    "intro": "Intro",
                    "body_parts": ["Complete body"],
                    "outro": "Outro",
                    "metadata": "Metadata",
                },
            ),
            patch.object(main, "_schedule_video_queue_wakeup"),
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        job = main._jobs[response["job_id"]]
        self.assertEqual(job["status"], "retry_wait")
        self.assertTrue(job["result"]["complete_for_audio"])
        self.assertEqual(job["result"]["audio_task"]["task_id"], "single-task")
        self.assertEqual(job["result"]["audio_review"]["status"], "approved")
        auto_audio.assert_called_once_with(
            72,
            requested_voice_id=main.AUDIO_VOICE_ID,
            requested_voice_name="Giọng kiểm thử",
        )

    def test_complete_video_is_auto_reviewed_and_submitted_for_audio(self):
        completed_script = (
            "### [INTRO]\nIntro hoàn chỉnh.\n\n"
            "### [BODY]\nNội dung hoàn chỉnh.\n\n"
            "### [OUTRO]\nOutro hoàn chỉnh.\n\n"
            "### [METADATA & QUIZ]\nMetadata\n\n"
            "### [CHAPTERS]\n00:00 - Mở đầu"
        )
        worker_result = {
            "script": completed_script,
            "chat_url": "https://chatgpt.com/c/test",
            "warning": "",
            "complete_for_audio": True,
        }
        audio_task = {
            "video_id": 75,
            "task_id": "auto-audio-task",
            "status": "pending",
            "audio_url": "",
            "error": "",
            "voice_id": main.AUDIO_VOICE_ID,
            "voice_name": "Giọng kiểm thử",
            "segments_json": "",
            "updated_at": "2026-09-08T00:00:00+00:00",
        }

        with (
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(main.threading, "Thread", ImmediateThread),
            patch.object(main, "get_video_transcript", return_value="Transcript"),
            patch.object(main, "get_video_title", return_value="Title"),
            patch.object(main, "process_prompt_via_chatgpt", return_value=worker_result),
            patch.object(main.db, "save_video", return_value=75),
            patch.object(main.db, "update_video_generation", return_value=True),
            patch.object(
                main,
                "_auto_review_and_create_audio",
                return_value=(audio_review("approved"), audio_task, ""),
            ) as auto_audio,
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=generic",
                    prompt_version="version-key",
                )
            )

        job = main._jobs[response["job_id"]]
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["result"]["audio_review"]["status"], "approved")
        self.assertEqual(job["result"]["audio_task"]["task_id"], "auto-audio-task")
        auto_audio.assert_called_once_with(
            75,
            requested_voice_id=main.AUDIO_VOICE_ID,
            requested_voice_name="Giọng kiểm thử",
        )

    def test_pipeline_can_finish_a_video_without_automatic_audio(self):
        pipeline = {
            "title": False,
            "slug": False,
            "description": False,
            "tags": False,
            "pinned_comment": False,
            "quiz": False,
            "chapters": False,
            "thumbnail_with_text": False,
            "thumbnail_without_text": False,
            "audio": False,
            "video_render": False,
            "youtube_upload": False,
            "youtube_schedule": False,
        }
        worker_result = {
            "script": (
                "### [INTRO]\nIntro hoàn chỉnh.\n\n"
                "### [BODY]\nNội dung hoàn chỉnh.\n\n"
                "### [OUTRO]\nOutro hoàn chỉnh.\n"
            ),
            "chat_url": "https://chatgpt.com/c/test",
            "warning": "",
            "complete_for_audio": True,
            "pipeline": pipeline,
        }

        with (
            patch.object(
                main,
                "_get_prompt_production_snapshot",
                return_value={"prompt_version": "version-key", "pipeline": pipeline},
            ),
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(main.threading, "Thread", ImmediateThread),
            patch.object(main, "get_video_transcript", return_value="Transcript"),
            patch.object(main, "get_video_title", return_value="Title"),
            patch.object(main, "process_prompt_via_chatgpt", return_value=worker_result),
            patch.object(main.db, "save_video", return_value=76),
            patch.object(main.db, "update_video_generation", return_value=True),
            patch.object(main, "_auto_review_and_create_audio") as auto_audio,
            patch.object(main, "_prepare_audio_review") as prepare_review,
        ):
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=no-auto-audio",
                    prompt_version="version-key",
                )
            )

        job = main._jobs[response["job_id"]]
        self.assertEqual(job["status"], "done")
        self.assertIsNone(job["result"]["audio_review"])
        self.assertEqual(job["result"]["pipeline"], pipeline)
        self.assertIn("không tự tạo audio", job["progress"])
        auto_audio.assert_not_called()
        prepare_review.assert_not_called()


if __name__ == "__main__":
    unittest.main()
