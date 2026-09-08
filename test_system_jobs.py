import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt import main
from auto_yt.services import database


class SystemJobTests(unittest.TestCase):
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

    def create_job(self, job_id: str) -> dict:
        return database.create_system_job(
            job_id=job_id,
            job_type="video_generation",
            title=f"Video {job_id}",
            payload={"url": f"https://youtube.com/watch?v={job_id}"},
            prompt_version="prompt-a",
            voice_id="voice-a",
        )

    def test_claims_video_jobs_in_fifo_order(self):
        self.create_job("first")
        self.create_job("second")

        first = database.claim_next_system_job("video_generation")
        database.update_system_job("first", status="done")
        second = database.claim_next_system_job("video_generation")

        self.assertEqual(first["id"], "first")
        self.assertEqual(second["id"], "second")
        self.assertEqual(first["attempt"], 1)
        self.assertEqual(database.get_system_job_queue_position("second"), None)

    def test_queued_job_can_be_canceled_and_retried(self):
        self.create_job("queued")

        canceled = database.request_cancel_system_job("queued")
        retried = database.retry_system_job("queued")

        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(database.get_system_job_queue_position("queued"), 1)

    def test_queued_job_can_be_paused_and_resumed(self):
        self.create_job("pausable")

        paused = database.pause_system_job("pausable")
        self.assertEqual(paused["status"], "paused")
        self.assertIsNone(database.claim_next_system_job("video_generation"))

        resumed = database.resume_system_job("pausable")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(
            database.claim_next_system_job("video_generation")["id"],
            "pausable",
        )

    def test_running_job_is_requeued_after_restart(self):
        self.create_job("interrupted")
        database.claim_next_system_job("video_generation")

        recovered_count = database.recover_interrupted_system_jobs(
            "video_generation"
        )
        recovered = database.get_system_job("interrupted")

        self.assertEqual(recovered_count, 1)
        self.assertEqual(recovered["status"], "queued")
        self.assertEqual(recovered["recovery_count"], 1)
        self.assertIn("khôi phục", recovered["progress"].lower())

    def test_delayed_recovery_is_claimed_only_when_due(self):
        self.create_job("recoverable")
        database.claim_next_system_job("video_generation")

        waiting = database.schedule_system_job_recovery(
            "recoverable",
            resume_from_step="body 2/4",
            delay_seconds=3600,
            error="temporary failure",
            result_json={"video_id": 42},
        )

        self.assertEqual(waiting["status"], "retry_wait")
        self.assertEqual(waiting["resume_from_step"], "body 2/4")
        self.assertEqual(waiting["recovery_count"], 1)
        self.assertIsNone(database.claim_next_system_job("video_generation"))

        database.update_system_job("recoverable", next_retry_at=database.utc_now())
        recovered = database.claim_next_system_job("video_generation")

        self.assertEqual(recovered["id"], "recoverable")
        self.assertEqual(recovered["status"], "running")
        self.assertEqual(recovered["attempt"], 2)

    def test_retry_wait_job_can_be_paused_and_resumed(self):
        self.create_job("waiting")
        database.claim_next_system_job("video_generation")
        database.schedule_system_job_recovery(
            "waiting",
            resume_from_step="metadata",
            delay_seconds=3600,
            error="temporary failure",
        )

        paused = database.pause_system_job("waiting")
        resumed = database.resume_system_job("waiting")

        self.assertEqual(paused["status"], "paused")
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["resume_from_step"], "metadata")

    def test_delayed_recovery_keeps_later_video_jobs_in_fifo_order(self):
        self.create_job("recovering-first")
        database.claim_next_system_job("video_generation")
        database.schedule_system_job_recovery(
            "recovering-first",
            resume_from_step="body 2/4",
            delay_seconds=3600,
            error="temporary failure",
        )
        self.create_job("queued-second")

        self.assertFalse(database.has_claimable_system_jobs("video_generation"))
        self.assertIsNone(database.claim_next_system_job("video_generation"))

        database.update_system_job(
            "recovering-first",
            next_retry_at=database.utc_now(),
        )
        recovered = database.claim_next_system_job("video_generation")

        self.assertEqual(recovered["id"], "recovering-first")

    def test_payload_and_result_are_round_tripped_as_objects(self):
        created = self.create_job("json")
        updated = database.update_system_job(
            "json",
            result_json={"video_id": 42, "success": True},
        )

        self.assertEqual(created["payload"]["url"], "https://youtube.com/watch?v=json")
        self.assertEqual(updated["result"]["video_id"], 42)

    def test_list_jobs_includes_linked_video_search_fields(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=searchable-id",
            "Tieu de goc",
            "transcript",
            "TIÊU ĐỀ VIDEO: Tiêu đề mới",
        )
        self.create_job("search-fields")
        database.update_system_job("search-fields", video_id=video_id)

        job = database.list_system_jobs(job_type="video_generation")[0]

        self.assertEqual(job["video_url"], "https://youtube.com/watch?v=searchable-id")
        self.assertEqual(job["original_title"], "Tieu de goc")
        self.assertEqual(job["generated_title"], "Tiêu đề mới")

        response = main.list_jobs(
            limit=100,
            job_type="video_generation",
            search="tieu de moi",
        )
        self.assertEqual([item["id"] for item in response["items"]], ["search-fields"])

    def test_job_search_matches_each_video_inside_download_batch(self):
        item = main._download_job_center_item({
            "id": "batch-a",
            "status": "completed",
            "total": 2,
            "completed": 2,
            "failed": 0,
            "items": [
                {
                    "id": "alpha-id",
                    "title": "Video thông thường",
                    "url": "https://youtube.com/watch?v=alpha-id",
                },
                {
                    "id": "history-id",
                    "title": "Lịch sử Việt Nam",
                    "url": "https://youtube.com/watch?v=history-id",
                },
            ],
        })

        self.assertTrue(main._job_matches_video_search(item, "lich su viet nam"))
        self.assertTrue(main._job_matches_video_search(item, "history-id"))
        self.assertFalse(main._job_matches_video_search(item, "khong ton tai"))


if __name__ == "__main__":
    unittest.main()
