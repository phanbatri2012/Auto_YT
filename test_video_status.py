import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt import main
from auto_yt.services import database


class VideoStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            database,
            "DB_PATH",
            Path(self.temp_directory.name) / "database.db",
        )
        self.database_patch.start()
        database.init_db()

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def create_video(self, suffix: str = "one") -> int:
        return database.save_video(
            f"https://www.youtube.com/watch?v={suffix}",
            f"Original {suffix}",
            "Transcript",
            "### [NỘI DUNG KỊCH BẢN]\nIncomplete content is still active",
            prompt_version="prompt-a",
        )

    def test_error_status_is_manual_and_has_separate_dashboard_counts(self):
        active_id = self.create_video("active")
        error_id = self.create_video("error")

        self.assertEqual(database.get_video(error_id)["video_status"], "active")
        database.set_video_status(error_id, database.VIDEO_STATUS_ERROR)

        result = database.get_all_videos(limit=10)
        self.assertEqual(result["count_unpublished"], 1)
        self.assertEqual(result["count_error"], 1)
        self.assertEqual({item["id"] for item in result["items"]}, {active_id, error_id})
        self.assertEqual(
            [item["id"] for item in database.get_all_videos(
                limit=10,
                video_status=database.VIDEO_STATUS_ERROR,
            )["items"]],
            [error_id],
        )

    def test_error_video_is_skipped_by_automatic_job_and_audio_lists(self):
        video_id = self.create_video("skipped")
        database.create_system_job(
            job_id="video-job",
            job_type="video_generation",
            title="Queued video",
            payload={"url": "https://www.youtube.com/watch?v=skipped"},
            prompt_version="prompt-a",
            voice_id="voice-a",
        )
        database.update_system_job("video-job", video_id=video_id)
        database.upsert_audio_review(
            video_id,
            "hash",
            "approved",
            {"can_approve": True},
        )
        database.upsert_audio_task(
            video_id,
            "request-hash",
            "task-id",
            "pending",
        )

        database.set_video_status(video_id, database.VIDEO_STATUS_ERROR)

        self.assertEqual(database.list_system_jobs(), [])
        self.assertEqual(database.list_audio_reviews(), [])
        self.assertEqual(database.list_audio_tasks(), [])
        self.assertEqual(database.get_active_audio_tasks(), [])
        self.assertIsNone(database.claim_next_system_job("video_generation"))

    def test_restoring_video_does_not_restart_canceled_work(self):
        video_id = self.create_video("restore")
        database.create_system_job(
            job_id="restore-job",
            job_type="video_generation",
            title="Queued video",
            payload={"url": "https://www.youtube.com/watch?v=restore"},
            prompt_version="prompt-a",
            voice_id="voice-a",
        )
        database.update_system_job("restore-job", video_id=video_id)

        database.set_video_status(video_id, database.VIDEO_STATUS_ERROR)
        database.set_video_status(video_id, database.VIDEO_STATUS_ACTIVE)

        self.assertEqual(database.get_system_job("restore-job")["status"], "canceled")
        self.assertIsNone(database.claim_next_system_job("video_generation"))

    def test_video_worker_does_not_assign_error_status_automatically(self):
        worker_source = inspect.getsource(main._execute_video_job)
        self.assertNotIn("set_video_status", worker_source)


if __name__ == "__main__":
    unittest.main()
