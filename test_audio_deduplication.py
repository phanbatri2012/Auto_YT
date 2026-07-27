import unittest
from unittest.mock import patch

from auto_yt import main


SCRIPT = """
### [INTRO]
Nội dung dùng để kiểm tra audio.

### [METADATA]
Test
""".strip()


class AudioDeduplicationTests(unittest.TestCase):
    def test_active_task_is_reused_without_new_submission(self):
        stored_task = {
            "video_id": 31,
            "request_hash": main.tts.get_request_hash(
                main.apply_tts_filters(main.get_clean_script_for_tts(SCRIPT)),
                main.AUDIO_VOICE_ID,
            ),
            "task_id": "existing-task",
            "status": "processing",
            "audio_url": "",
            "error": "",
            "updated_at": "2026-07-27T00:00:00+00:00",
        }

        with (
            patch.object(
                main.db,
                "get_video",
                return_value={"generated_script": SCRIPT},
            ),
            patch.object(main.db, "get_audio_task", return_value=stored_task),
            patch.object(main, "_start_audio_watcher"),
            patch.object(main.tts, "find_matching_task") as find_task,
            patch.object(main.tts, "submit_tts_task") as submit_task,
        ):
            result = main._ensure_audio_task(31)

        self.assertEqual(result["task_id"], "existing-task")
        find_task.assert_not_called()
        submit_task.assert_not_called()

    def test_history_failure_never_falls_through_to_paid_submission(self):
        with (
            patch.object(
                main.db,
                "get_video",
                return_value={"generated_script": SCRIPT},
            ),
            patch.object(main.db, "get_audio_task", return_value=None),
            patch.object(
                main.db,
                "get_audio_task_by_request_hash",
                return_value=None,
            ),
            patch.object(
                main.tts,
                "find_matching_task",
                side_effect=RuntimeError("Genmax maintenance"),
            ),
            patch.object(main.tts, "submit_tts_task") as submit_task,
        ):
            with self.assertRaisesRegex(RuntimeError, "Genmax maintenance"):
                main._ensure_audio_task(31)

        submit_task.assert_not_called()

    def test_retry_requires_explicit_credit_confirmation(self):
        request = main.RetryAudioRequest(confirm_credit_charge=False)
        with patch.object(main.tts, "retry_tts_task") as retry_task:
            with self.assertRaisesRegex(
                main.HTTPException,
                "Phải xác nhận Genmax sẽ trừ credit lần nữa.",
            ):
                main.retry_audio_for_video(31, request)

        retry_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
