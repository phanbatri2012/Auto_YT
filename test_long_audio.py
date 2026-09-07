import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from pathlib import Path

from auto_yt import main
from auto_yt.services import audio_utils, database, tts_service


class LongAudioTests(unittest.TestCase):
    def test_long_text_is_split_below_provider_limit(self):
        text = ("Một câu thử nghiệm đủ dài để chia tự nhiên. " * 600).strip()

        chunks = tts_service.split_text_for_tts(text, max_chars=900)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 900 for chunk in chunks))
        self.assertEqual("".join(text.split()), "".join("".join(chunks).split()))

    def test_batch_hash_is_versioned_but_short_hash_stays_compatible(self):
        short_text = "Nội dung ngắn."
        long_text = "Nội dung dài. " * 1000

        self.assertEqual(
            tts_service.get_generation_request_hash(short_text, "voice"),
            tts_service.get_request_hash(short_text, "voice"),
        )
        self.assertNotEqual(
            tts_service.get_generation_request_hash(long_text, "voice"),
            tts_service.get_request_hash(long_text, "voice"),
        )

    def test_batch_submission_is_persisted_and_reused(self):
        chunks = ["Đoạn thứ nhất.", "Đoạn thứ hai."]
        stored_versions = []

        def store_task(**kwargs):
            task = {
                **kwargs,
                "updated_at": "2026-08-04T00:00:00+00:00",
            }
            stored_versions.append(task)
            return task

        submitted = [
            {"id": "task-1", "status": "pending"},
            {"id": "task-2", "status": "pending"},
        ]
        with (
            patch.object(main.tts, "find_matching_tasks", return_value={}),
            patch.object(main.tts, "submit_tts_task", side_effect=submitted) as submit,
            patch.object(main.db, "upsert_audio_task", side_effect=store_task),
            patch.object(main, "_start_audio_watcher") as start_watcher,
        ):
            first = main._ensure_batch_audio_task(
                70,
                "request-hash",
                chunks,
                None,
            )
            second = main._ensure_batch_audio_task(
                70,
                "request-hash",
                chunks,
                first,
            )

        self.assertEqual(submit.call_count, 2)
        self.assertEqual(first["task_id"], "batch-request-hash")
        segments = json.loads(first["segments_json"])
        self.assertEqual([segment["task_id"] for segment in segments], ["task-1", "task-2"])
        self.assertEqual(second, first)
        self.assertGreaterEqual(start_watcher.call_count, 2)

    def test_incomplete_batch_is_marked_interrupted(self):
        segments = [
            {
                "index": 0,
                "task_id": "task-1",
                "status": "completed",
                "audio_url": "https://audio/1.mp3",
            },
            {
                "index": 1,
                "task_id": "",
                "status": "not_submitted",
                "audio_url": "",
            },
        ]
        stored_task = {
            "video_id": 70,
            "request_hash": "request-hash",
            "task_id": "batch-request-hash",
            "status": "pending",
            "audio_url": "",
            "error": "",
            "updated_at": "2026-08-08T00:00:00+00:00",
            "segments_json": json.dumps(segments),
        }

        def store_task(**kwargs):
            return {
                **kwargs,
                "updated_at": "2026-08-08T00:00:00+00:00",
            }

        with patch.object(
            main.db,
            "upsert_audio_task",
            side_effect=store_task,
        ):
            result = main._sync_batch_audio_task(stored_task, segments)

        self.assertEqual(result["status"], main.AUDIO_INTERRUPTED_STATUS)
        self.assertIn("Còn 1 phần chưa gửi", result["error"])
        self.assertEqual(main._audio_task_response(result)["missing_segments"], 1)

    def test_transient_segment_error_preserves_other_segment_progress(self):
        segments = [
            {
                "index": 0,
                "task_id": "task-1",
                "status": "processing",
                "audio_url": "",
            },
            {
                "index": 1,
                "task_id": "task-2",
                "status": "processing",
                "audio_url": "",
            },
        ]
        stored_task = {
            "video_id": 70,
            "request_hash": "request-hash",
            "task_id": "batch-request-hash",
            "status": "processing",
            "audio_url": "",
            "error": "",
            "updated_at": "2026-08-08T00:00:00+00:00",
            "segments_json": json.dumps(segments),
        }

        def store_task(**kwargs):
            return {
                **kwargs,
                "updated_at": "2026-08-08T00:00:00+00:00",
            }

        with (
            patch.object(
                main.tts,
                "get_tts_task",
                side_effect=[
                    {
                        "status": "completed",
                        "result": {"audio_url": "https://audio/1.mp3"},
                    },
                    RuntimeError("temporary API timeout"),
                ],
            ),
            patch.object(
                main.db,
                "upsert_audio_task",
                side_effect=store_task,
            ),
        ):
            result = main._sync_batch_audio_task(stored_task, segments)

        saved_segments = json.loads(result["segments_json"])
        self.assertEqual(result["status"], "processing")
        self.assertIn("đoạn: 2", result["error"])
        self.assertEqual(saved_segments[0]["status"], "completed")
        self.assertEqual(
            saved_segments[0]["audio_url"],
            "https://audio/1.mp3",
        )
        self.assertEqual(saved_segments[1]["status"], "processing")

    def test_audio_status_poll_synchronizes_pending_task_immediately(self):
        pending_task = {
            "video_id": 70,
            "request_hash": "request-hash",
            "task_id": "task-1",
            "status": "pending",
            "audio_url": "",
            "error": "",
            "updated_at": "2026-08-08T00:00:00+00:00",
            "segments_json": "",
        }
        completed_task = {
            **pending_task,
            "status": "completed",
            "audio_url": "http://127.0.0.1:8080/api/audio/video_70.mp3",
        }
        with (
            patch.object(main.db, "get_video", return_value={
                "generated_script": "### [BODY]\nNội dung",
            }),
            patch.object(main.db, "get_audio_task", return_value=pending_task),
            patch.object(
                main,
                "_sync_audio_task",
                return_value=completed_task,
            ) as sync_task,
            patch.object(main, "_start_audio_watcher") as start_watcher,
        ):
            result = main.get_audio_status(70)

        sync_task.assert_called_once_with(70)
        start_watcher.assert_not_called()
        self.assertEqual(result["audio_task"]["status"], "completed")

    def test_audio_status_repairs_missing_script_marker_from_completed_task(self):
        completed_task = {
            "video_id": 70,
            "request_hash": "request-hash",
            "task_id": "task-1",
            "status": "completed",
            "audio_url": "http://127.0.0.1:8080/api/audio/video_70.mp3",
            "error": "",
            "updated_at": "2026-08-08T00:00:00+00:00",
            "segments_json": "",
            "voice_id": "voice-id",
            "voice_name": "Giọng thử nghiệm",
        }
        with (
            patch.object(main.db, "get_video", return_value={
                "generated_script": "### [BODY]\nNội dung",
            }),
            patch.object(main.db, "get_audio_task", return_value=completed_task),
            patch.object(main, "_save_audio_url") as save_audio_url,
        ):
            result = main.get_audio_status(70)

        save_audio_url.assert_called_once_with(
            70,
            completed_task["audio_url"],
            "voice-id",
            "Giọng thử nghiệm",
        )
        self.assertEqual(result["audio_task"]["status"], "completed")

    def test_interrupted_batch_submits_only_missing_segments(self):
        chunks = ["Đoạn đã hoàn thành.", "Đoạn còn thiếu."]
        segments = [
            {
                "index": 0,
                "task_id": "task-1",
                "status": "completed",
                "audio_url": "https://audio/1.mp3",
            },
            {
                "index": 1,
                "task_id": "",
                "status": "not_submitted",
                "audio_url": "",
            },
        ]
        stored_task = {
            "video_id": 70,
            "request_hash": "request-hash",
            "task_id": "batch-request-hash",
            "status": main.AUDIO_INTERRUPTED_STATUS,
            "audio_url": "",
            "error": "",
            "updated_at": "2026-08-08T00:00:00+00:00",
            "segments_json": json.dumps(segments),
        }

        def store_task(**kwargs):
            return {
                **kwargs,
                "updated_at": "2026-08-08T00:00:00+00:00",
            }

        with (
            patch.object(main.tts, "find_matching_tasks", return_value={}) as history,
            patch.object(
                main.tts,
                "submit_tts_task",
                return_value={"id": "task-2", "status": "pending"},
            ) as submit,
            patch.object(
                main.db,
                "upsert_audio_task",
                side_effect=store_task,
            ),
            patch.object(main, "_start_audio_watcher") as start_watcher,
        ):
            result = main._ensure_batch_audio_task(
                70,
                "request-hash",
                chunks,
                stored_task,
            )

        history.assert_called_once_with(chunks, main.AUDIO_VOICE_ID)
        submit.assert_called_once_with(chunks[1], main.AUDIO_VOICE_ID)
        result_segments = json.loads(result["segments_json"])
        self.assertEqual(
            [segment["task_id"] for segment in result_segments],
            ["task-1", "task-2"],
        )
        start_watcher.assert_called_once_with(70)

    def test_submission_failure_preserves_interrupted_manifest(self):
        chunks = ["Đoạn đã hoàn thành.", "Đoạn còn thiếu."]
        stored_task = {
            "video_id": 70,
            "request_hash": "request-hash",
            "task_id": "batch-request-hash",
            "status": main.AUDIO_INTERRUPTED_STATUS,
            "audio_url": "",
            "error": "",
            "updated_at": "2026-08-08T00:00:00+00:00",
            "segments_json": json.dumps([
                {
                    "index": 0,
                    "task_id": "task-1",
                    "status": "completed",
                    "audio_url": "https://audio/1.mp3",
                },
                {
                    "index": 1,
                    "task_id": "",
                    "status": "not_submitted",
                    "audio_url": "",
                },
            ]),
        }
        stored_versions = []

        def store_task(**kwargs):
            stored_versions.append(kwargs)
            return {
                **kwargs,
                "updated_at": "2026-08-08T00:00:00+00:00",
            }

        with (
            patch.object(main.tts, "find_matching_tasks", return_value={}),
            patch.object(
                main.tts,
                "submit_tts_task",
                side_effect=RuntimeError("Genmax unavailable"),
            ),
            patch.object(
                main.db,
                "upsert_audio_task",
                side_effect=store_task,
            ),
            self.assertRaisesRegex(RuntimeError, "Genmax unavailable"),
        ):
            main._ensure_batch_audio_task(
                70,
                "request-hash",
                chunks,
                stored_task,
            )

        self.assertEqual(
            stored_versions[-1]["status"],
            main.AUDIO_INTERRUPTED_STATUS,
        )
        self.assertIn("Còn 1 phần chưa gửi", stored_versions[-1]["error"])

    def test_mp3_metadata_frame_is_removed_and_duration_is_counted(self):
        data, frame_length = self._build_test_mp3(10)

        frames, duration_seconds, encoding = audio_utils.extract_mp3_audio_frames(data)

        self.assertEqual(len(frames), frame_length * 10)
        self.assertAlmostEqual(duration_seconds, 10 * 1152 / 44100, places=5)
        self.assertEqual(encoding, (1.0, 3, 44100))

    def test_remote_mp3_segments_are_merged_into_one_file(self):
        first_data, frame_length = self._build_test_mp3(4)
        second_data, _ = self._build_test_mp3(6)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "merged.mp3"
            with patch.object(
                audio_utils,
                "download_audio",
                side_effect=[first_data, second_data],
            ):
                duration_seconds = audio_utils.merge_remote_mp3_files(
                    ["https://audio/1", "https://audio/2"],
                    output_path,
                )

            self.assertEqual(output_path.stat().st_size, frame_length * 10)
            self.assertAlmostEqual(duration_seconds, 10 * 1152 / 44100, places=5)

    def test_concurrent_merges_use_separate_temporary_files(self):
        data, frame_length = self._build_test_mp3(4)
        downloads_started = Barrier(2)

        def download_at_same_time(_audio_url):
            downloads_started.wait(timeout=2)
            return data

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "merged.mp3"
            with patch.object(
                audio_utils,
                "download_audio",
                side_effect=download_at_same_time,
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(
                        lambda _: audio_utils.merge_remote_mp3_files(
                            ["https://audio/segment"],
                            output_path,
                        ),
                        range(2),
                    ))

            self.assertEqual(output_path.stat().st_size, frame_length * 4)
            self.assertEqual(len(results), 2)
            self.assertFalse(list(output_path.parent.glob("*.tmp")))

    def test_audio_segment_manifest_round_trips_through_database(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_database = Path(temporary_directory) / "database.db"
            with patch.object(database, "DB_PATH", temporary_database):
                database.init_db()
                video_id = database.save_video(
                    "https://youtube.test/video",
                    "Title",
                    "Transcript",
                    "Script",
                )
                segments_json = json.dumps([{"index": 0, "task_id": "task-1"}])
                task = database.upsert_audio_task(
                    video_id,
                    "request-hash",
                    "batch-request-hash",
                    "pending",
                    segments_json=segments_json,
                )

                self.assertEqual(task["segments_json"], segments_json)

    def test_stale_sync_cannot_downgrade_completed_audio_task(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_database = Path(temporary_directory) / "database.db"
            with patch.object(database, "DB_PATH", temporary_database):
                database.init_db()
                video_id = database.save_video(
                    "https://youtube.test/video",
                    "Title",
                    "Transcript",
                    "Script",
                )
                database.upsert_audio_task(
                    video_id,
                    "request-hash",
                    "batch-request-hash",
                    "completed",
                    audio_url="http://audio/complete.mp3",
                )

                stale_result = database.upsert_audio_task(
                    video_id,
                    "request-hash",
                    "batch-request-hash",
                    "processing",
                    error="temporary timeout",
                )

                self.assertEqual(stale_result["status"], "completed")
                self.assertEqual(
                    stale_result["audio_url"],
                    "http://audio/complete.mp3",
                )

                new_request = database.upsert_audio_task(
                    video_id,
                    "new-request-hash",
                    "batch-new-request-hash",
                    "pending",
                )

                self.assertEqual(new_request["status"], "pending")
                self.assertEqual(new_request["request_hash"], "new-request-hash")

    def test_implausibly_short_audio_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "incomplete audio"):
            audio_utils.validate_spoken_duration("từ " * 600, 30)

    @staticmethod
    def _build_test_mp3(audio_frame_count: int) -> tuple[bytes, int]:
        frame_header = bytes.fromhex("FF FB B0 00")
        frame_length = 626
        metadata_frame = bytearray(frame_header + bytes(frame_length - 4))
        metadata_frame[36:40] = b"Info"
        audio_frame = frame_header + bytes(frame_length - 4)
        data = (
            b"ID3\x04\x00\x00\x00\x00\x00\x00"
            + bytes(metadata_frame)
            + audio_frame * audio_frame_count
        )
        return data, frame_length


if __name__ == "__main__":
    unittest.main()
