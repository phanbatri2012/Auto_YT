import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt import main
from auto_yt.services import database
from auto_yt.services.audio_review import audit_script_for_audio


SCRIPT = """
### [INTRO]
Mở đầu ngắn gọn nhưng đầy đủ.

### [BODY]
Đây là nội dung chính của video và được viết thành câu hoàn chỉnh.

### [OUTRO]
Kết thúc câu chuyện và cảm ơn khán giả.

### [METADATA & QUIZ]
TIÊU ĐỀ: Video kiểm tra
""".strip()


class AudioReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            database,
            "DB_PATH",
            Path(self.temporary_directory.name) / "database.db",
        )
        self.database_patch.start()
        self.checkpoint_patch = patch.object(main, "load_checkpoint", return_value=None)
        self.checkpoint_patch.start()
        database.init_db()
        self.video_id = database.save_video(
            "https://youtube.test/review",
            "Video review",
            "Transcript",
            SCRIPT,
            voice_id="Vietnamese_crisp_announcer_v2",
            voice_name="Giọng kiểm thử",
        )

    def tearDown(self):
        self.checkpoint_patch.stop()
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    def test_complete_short_script_is_reviewable_without_length_threshold(self):
        report = audit_script_for_audio(SCRIPT)

        self.assertTrue(report["can_approve"])
        self.assertEqual(report["errors"], [])
        self.assertGreater(report["metrics"]["word_count"], 0)

    def test_missing_section_and_corrupted_unicode_are_blocking(self):
        report = audit_script_for_audio(
            "### [INTRO]\nN?i dung ?? l?i\n\n### [BODY]\nNội dung"
        )

        self.assertFalse(report["can_approve"])
        self.assertIn("missing_section", {item["code"] for item in report["errors"]})
        self.assertIn("corrupted_unicode", {item["code"] for item in report["errors"]})

    def test_approval_survives_metadata_change_but_not_narrative_change(self):
        review = main._prepare_audio_review(self.video_id)
        database.upsert_audio_review(
            self.video_id,
            review["script_hash"],
            "approved",
            review["report"],
            database.utc_now(),
        )

        database.update_script(
            self.video_id,
            SCRIPT.replace("TIÊU ĐỀ: Video kiểm tra", "TIÊU ĐỀ: Tiêu đề mới"),
        )
        self.assertEqual(main._prepare_audio_review(self.video_id)["status"], "approved")

        database.update_script(
            self.video_id,
            SCRIPT.replace("Đây là nội dung chính", "Nội dung chính đã thay đổi"),
        )
        self.assertEqual(main._prepare_audio_review(self.video_id)["status"], "pending")

    def test_generate_audio_automatically_approves_before_submission(self):
        task = {
            "video_id": self.video_id,
            "task_id": "task-id",
            "status": "pending",
            "audio_url": "",
            "error": "",
            "voice_id": "Vietnamese_crisp_announcer_v2",
            "voice_name": "Giọng kiểm thử",
            "segments_json": "",
            "updated_at": "2026-09-08T00:00:00+00:00",
        }
        with patch.object(main, "_ensure_audio_task", return_value=task) as ensure_audio:
            response = main.generate_audio_for_video(self.video_id)

        self.assertTrue(response["success"])
        self.assertEqual(response["audio_review"]["status"], "approved")
        self.assertEqual(database.get_audio_review(self.video_id)["status"], "approved")
        ensure_audio.assert_called_once_with(
            self.video_id,
            requested_voice_id="",
            requested_voice_name="",
        )

    def test_failed_automatic_review_never_submits_to_genmax(self):
        database.update_script(
            self.video_id,
            "### [INTRO]\nN?i dung ?? l?i\n\n### [BODY]\nNội dung",
        )
        with patch.object(main, "_ensure_audio_task") as ensure_audio:
            response = main.generate_audio_for_video(self.video_id)

        self.assertFalse(response["success"])
        self.assertTrue(response["quality_blocked"])
        self.assertEqual(response["audio_review"]["status"], "blocked")
        ensure_audio.assert_not_called()

    def test_approval_is_saved_before_audio_submission(self):
        task = {
            "video_id": self.video_id,
            "task_id": "task-id",
            "status": "pending",
            "audio_url": "",
            "error": "",
            "voice_id": "Vietnamese_crisp_announcer_v2",
            "voice_name": "Giọng kiểm thử",
            "segments_json": "",
            "updated_at": "2026-09-08T00:00:00+00:00",
        }
        with (
            patch.object(
                main.voice_config,
                "get_voice",
                return_value={
                    "id": "Vietnamese_crisp_announcer_v2",
                    "name": "Giọng kiểm thử",
                },
            ),
            patch.object(main, "_ensure_audio_task", return_value=task) as ensure_audio,
        ):
            response = main.approve_audio_review(
                self.video_id,
                main.ApproveAudioReviewRequest(
                    confirm_credit_charge=True,
                    voice_id="Vietnamese_crisp_announcer_v2",
                ),
            )

        self.assertTrue(response["success"])
        self.assertEqual(database.get_audio_review(self.video_id)["status"], "approved")
        ensure_audio.assert_called_once()


if __name__ == "__main__":
    unittest.main()
