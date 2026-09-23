import datetime as dt
import io
import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from auto_yt import main
from auto_yt.services import database
from auto_yt.services import publication_scheduler
from auto_yt.services import video_production
from auto_yt.services import youtube_publisher
from auto_yt.services import youtube_publish_workflow


class VideoProductionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            database,
            "DB_PATH",
            Path(self.temporary_directory.name) / "database.db",
        )
        self.database_patch.start()
        database.init_db()

    def tearDown(self):
        self.database_patch.stop()
        try:
            self.temporary_directory.cleanup()
        except Exception:
            pass

    def test_build_scene_windows_splits_by_duration(self):
        captions = [{"start": i, "end": i + 1, "text": f"word {i}"} for i in range(120)]
        windows = video_production.build_scene_windows(
            captions,
            duration_seconds=120.0,
            minimum_seconds=25.0,
            target_seconds=30.0,
            maximum_seconds=35.0
        )
        self.assertTrue(len(windows) > 0)
        self.assertTrue(all(w["duration"] >= 20.0 for w in windows))

    def test_media_validation_uses_pyav_when_ffprobe_is_missing(self):
        probe_result = {
            "duration_seconds": 4.0,
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "width": 0,
                    "height": 0,
                },
            ],
        }
        with (
            patch.object(video_production, "_find_ffprobe", return_value=None),
            patch.object(
                video_production,
                "_probe_media_with_av",
                return_value=probe_result,
            ),
        ):
            duration = video_production.probe_media_duration(Path("audio.wav"))
            details = video_production.validate_render(
                Path("video.mp4"), 4.0, "ffmpeg.exe"
            )

        self.assertEqual(duration, 4.0)
        self.assertEqual(details["video_codec"], "h264")
        self.assertEqual(details["audio_codec"], "aac")

    def test_whisper_falls_back_to_cpu_when_cuda_fails_during_transcription(self):
        audio_path = Path(self.temporary_directory.name) / "audio.wav"
        captions_dir = Path(self.temporary_directory.name) / "captions"
        audio_path.write_bytes(b"audio")
        devices = []
        progress_messages = []

        class FakeWhisperModel:
            def __init__(self, _model_name, *, device, compute_type):
                self.device = device
                devices.append((device, compute_type))

            def transcribe(self, *_args, **_kwargs):
                if self.device == "cuda":
                    def failing_segments():
                        raise RuntimeError("missing CUDA runtime")
                        yield

                    return failing_segments(), {}
                return iter(
                    [SimpleNamespace(start=0.0, end=1.0, text=" Xin chào ")]
                ), {}

        with (
            patch.dict(
                sys.modules,
                {"faster_whisper": SimpleNamespace(WhisperModel=FakeWhisperModel)},
            ),
            patch.object(video_production, "CAPTIONS_DIR", captions_dir),
            patch.object(video_production, "_WHISPER_MODEL", None),
            patch.object(video_production, "_WHISPER_DEVICE", ""),
            patch.object(database, "get_latest_video_artifact", return_value=None),
            patch.object(database, "upsert_video_artifact"),
        ):
            caption_path, _ = video_production.create_srt(
                audio_path,
                1,
                lambda message, stage: progress_messages.append((stage, message)),
            )

        self.assertEqual(devices, [("cuda", "float16"), ("cpu", "int8")])
        self.assertIn("Xin chào", caption_path.read_text(encoding="utf-8"))
        self.assertTrue(any("CPU int8" in message for _, message in progress_messages))

    

    

    def test_schedule_respects_timezone_lead_daily_limit_and_existing_slots(self):
        now = dt.datetime(2026, 9, 14, 1, 0, tzinfo=dt.timezone.utc)
        result = publication_scheduler.find_next_publication_slot(
            timezone_name="Asia/Ho_Chi_Minh",
            slots=[{"day": 0, "time": "09:00"}, {"day": 1, "time": "09:00"}],
            daily_limit=1,
            lead_minutes=120,
            occupied_utc=["2026-09-14T02:00:00+00:00"],
            now_utc=now,
        )

        self.assertEqual(result, "2026-09-15T02:00:00+00:00")

    def test_schedule_skips_nonexistent_dst_time(self):
        result = publication_scheduler.find_next_publication_slot(
            timezone_name="America/New_York",
            slots=[{"day": 6, "time": "02:30"}],
            daily_limit=1,
            lead_minutes=120,
            occupied_utc=[],
            now_utc=dt.datetime(2026, 3, 7, 12, 0, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(result, "2026-03-15T06:30:00+00:00")

    def test_publish_workflow_reservation_is_idempotent_for_video_and_channel(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=source123",
            "Source",
            "Transcript",
            "### [METADATA & QUIZ]\nTiêu đề: Output",
        )
        channel = database.save_youtube_channel(
            channel_id="UC-production-test",
            title="Production test",
        )
        artifact_path = Path(self.temporary_directory.name) / "video.mp4"
        artifact_path.write_bytes(b"video")
        artifact = database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="final_mp4",
            path=str(artifact_path),
            content_hash="same-artifact",
            status="ready",
            mime_type="video/mp4",
        )

        first, first_created = database.reserve_youtube_publish_workflow(
            workflow_id="workflow-first",
            video_id=video_id,
            youtube_channel_id=channel["id"],
            artifact_id=artifact["id"],
            snapshot={"pipeline": {"youtube_upload": True}},
        )
        second, second_created = database.reserve_youtube_publish_workflow(
            workflow_id="workflow-second",
            video_id=video_id,
            youtube_channel_id=channel["id"],
            artifact_id=artifact["id"],
            snapshot={"pipeline": {"youtube_upload": True}},
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(second["id"], first["id"])

    def test_production_schema_defaults_do_not_enqueue_old_videos(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=legacy123",
            "Legacy",
            "Transcript",
            "Script",
        )

        video = database.get_video(video_id)
        self.assertEqual(video["production_snapshot_json"], "{}")
        self.assertEqual(
            database.list_completed_audio_video_ids_with_production_snapshot(),
            [],
        )
        self.assertEqual(database.list_system_jobs(limit=None), [])

    def test_forbidden_privacy_setting_is_classified_as_audit_restriction(self):
        payload = {
            "error": {
                "message": "The request attempts to set an invalid privacy setting.",
                "errors": [{"reason": "forbiddenPrivacySetting"}],
            }
        }
        error = urllib.error.HTTPError(
            "https://www.googleapis.com/youtube/v3/videos",
            403,
            "Forbidden",
            {},
            io.BytesIO(json.dumps(payload).encode("utf-8")),
        )

        classified = youtube_publisher._publish_error_from_http(error)

        self.assertIsInstance(
            classified,
            youtube_publisher.YouTubePublicUploadRestricted,
        )

    def test_youtube_publish_transport_runs_end_to_end_without_network(self):
        class FakeResponse:
            def __init__(self, payload=None, headers=None):
                self.payload = payload or {}
                self.headers = headers or {}

            def read(self, _limit=-1):
                return json.dumps(self.payload).encode("utf-8") if self.payload else b""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        video_path = Path(self.temporary_directory.name) / "video.mp4"
        thumbnail_path = Path(self.temporary_directory.name) / "thumbnail.png"
        caption_path = Path(self.temporary_directory.name) / "caption.srt"
        video_path.write_bytes(b"0123456789")
        thumbnail_path.write_bytes(b"png")
        caption_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nAcceptance\n",
            encoding="utf-8",
        )
        session_url = "https://www.googleapis.com/upload/session-safe-test"
        calls = []

        def fake_urlopen(request: urllib.request.Request, timeout=0):
            url = request.full_url
            method = request.get_method()
            calls.append((method, url, timeout))
            if url.startswith(f"{youtube_publisher.YOUTUBE_UPLOAD_BASE}/videos?"):
                return FakeResponse(headers={"Location": session_url})
            if url == session_url:
                content_range = request.get_header("Content-range") or ""
                end = int(
                    content_range.split(" ", 1)[1]
                    .split("-", 1)[1]
                    .split("/", 1)[0]
                )
                if end < video_path.stat().st_size - 1:
                    raise urllib.error.HTTPError(
                        url,
                        308,
                        "Resume Incomplete",
                        {"Range": f"bytes=0-{end}"},
                        io.BytesIO(),
                    )
                return FakeResponse({"id": "youtube-safe-test"})
            if "/thumbnails/set?" in url:
                return FakeResponse({"items": [{"default": {}}]})
            if url.startswith(f"{youtube_publisher.YOUTUBE_API_BASE}/captions?"):
                return FakeResponse({"items": []})
            if url.startswith(f"{youtube_publisher.YOUTUBE_UPLOAD_BASE}/captions?"):
                return FakeResponse({"id": "caption-safe-test"})
            if url.startswith(f"{youtube_publisher.YOUTUBE_API_BASE}/videos?"):
                if method == "PUT":
                    request_payload = json.loads(request.data.decode("utf-8"))
                    return FakeResponse(
                        {
                            "id": "youtube-safe-test",
                            "status": request_payload["status"],
                        }
                    )
                return FakeResponse(
                    {
                        "items": [
                            {
                                "status": {
                                    "privacyStatus": "private",
                                    "uploadStatus": "processed",
                                },
                                "processingDetails": {
                                    "processingStatus": "succeeded"
                                },
                                "snippet": {},
                            }
                        ]
                    }
                )
            raise AssertionError(f"Unexpected external request: {method} {url}")

        class FakeOpener:
            def open(self, req, timeout=0):
                return fake_urlopen(req, timeout=timeout)

        progress = []
        with (
            patch.object(
                youtube_publisher.urllib.request,
                "urlopen",
                side_effect=fake_urlopen,
            ),
            patch.object(
                youtube_publisher,
                "create_proxy_opener",
                return_value=FakeOpener(),
            ),
            patch.object(youtube_publisher, "UPLOAD_CHUNK_SIZE", 4),
        ):
            created_session = youtube_publisher.start_resumable_upload(
                token="fake-token",
                video_path=video_path,
                metadata={
                    "snippet": {"title": "Acceptance"},
                    "status": {"privacyStatus": "private"},
                },
                notify_subscribers=False,
            )
            uploaded = youtube_publisher.upload_video_resumable(
                session_url=created_session,
                video_path=video_path,
                token_provider=lambda: "fake-token",
                start_offset=0,
                persist_progress=progress.append,
                cancel_check=lambda: None,
            )
            youtube_publisher.upload_thumbnail(
                "fake-token", uploaded["id"], thumbnail_path
            )
            youtube_publisher.upload_caption(
                "fake-token", uploaded["id"], caption_path
            )
            processing = youtube_publisher.require_processing_succeeded(
                "fake-token", uploaded["id"]
            )
            youtube_publisher.schedule_video(
                "fake-token",
                uploaded["id"],
                "2099-09-20T02:00:00+00:00",
            )

        self.assertEqual(created_session, session_url)
        self.assertEqual(uploaded["id"], "youtube-safe-test")
        self.assertEqual(progress, [4, 8, 10])
        self.assertEqual(processing["status"]["privacyStatus"], "private")
        self.assertEqual(len(calls), 9)
        self.assertTrue(
            all(url.startswith("https://www.googleapis.com/") for _, url, _ in calls)
        )

    

    def _make_publish_job(self, *, schedule: bool) -> tuple[dict, dict, int]:
        thumbnail_url = "http://127.0.0.1:8080/api/thumbnails/upload.png"
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=source-upload",
            "Source upload",
            "Transcript",
            "### [METADATA & QUIZ]\nMÔ TẢ VIDEO: Description\n"
            f"### [THUMBNAIL KHÔNG CHỮ]\n{thumbnail_url}",
        )
        channel = database.save_youtube_channel(
            channel_id=f"UC-upload-{int(schedule)}",
            title="Upload channel",
            access_token_encrypted="encrypted-access",
            gpm_profile_id="gpm-profile",
            gpm_proxy_info="127.0.0.1:8899:user:password",
        )
        channel = database.update_youtube_channel(
            channel["id"], public_upload_verified=int(schedule)
        )
        artifact_path = Path(self.temporary_directory.name) / "final.mp4"
        artifact_path.write_bytes(b"video")
        artifact = database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="final_mp4",
            path=str(artifact_path),
            content_hash="final-hash",
            status="ready",
            mime_type="video/mp4",
            size_bytes=5,
        )
        captions_path = Path(self.temporary_directory.name) / "captions.srt"
        captions_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest", encoding="utf-8")
        database.upsert_video_artifact(
            video_id=video_id,
            artifact_type="captions",
            path=str(captions_path),
            content_hash="captions-hash",
            status="ready",
            mime_type="application/x-subrip",
        )
        snapshot = {
            "pipeline": {
                "youtube_upload": True,
                "youtube_schedule": schedule,
            },
            "image_generation_settings": {
                "thumbnail_variant": "without_text",
            },
            "publishing_settings": {
                "category_id": "22",
                "language": "vi",
                "made_for_kids": False,
                "notify_subscribers": False,
            },
        }
        workflow, _ = database.reserve_youtube_publish_workflow(
            workflow_id=f"publish-test-{int(schedule)}",
            video_id=video_id,
            youtube_channel_id=channel["id"],
            artifact_id=artifact["id"],
            snapshot=snapshot,
        )
        job = database.create_system_job(
            job_id=f"publish-job-{int(schedule)}",
            job_type="youtube_publish",
            title="Upload",
            payload={"workflow_id": workflow["id"]},
        )
        database.update_system_job(job["id"], video_id=video_id)
        database.update_youtube_publish_workflow(
            workflow["id"], system_job_id=job["id"]
        )
        return database.get_system_job(job["id"]), channel, video_id

    def test_upload_without_schedule_stops_at_private(self):
        job, channel, video_id = self._make_publish_job(schedule=False)
        thumbnail_directory = Path(self.temporary_directory.name) / "thumbnails"
        thumbnail_directory.mkdir()
        (thumbnail_directory / "upload.png").write_bytes(b"thumbnail")

        from auto_yt.services import youtube_publisher
        with (
            patch.object(main, "THUMBNAILS_DIR", thumbnail_directory),
            patch.object(
                youtube_publish_workflow.youtube_comments,
                "access_token_for_channel",
                return_value="token",
            ),
            patch.object(
                youtube_publish_workflow.secret_store,
                "encrypt_secret",
                side_effect=lambda value: f"encrypted:{value}",
            ),
            patch.object(
                youtube_publisher,
                "start_resumable_upload",
                return_value="https://www.googleapis.com/upload/session",
            ),
            patch.object(
                youtube_publisher,
                "upload_video_resumable",
                return_value={"id": "youtube-private", "snippet": {}},
            ),
            patch.object(youtube_publisher, "upload_thumbnail"),
            patch.object(youtube_publisher, "find_caption_track", return_value=""),
            patch.object(
                youtube_publisher,
                "upload_caption",
                return_value={"id": "caption-private"},
            ),
            patch.object(youtube_publisher, "require_processing_succeeded"),
            patch.object(youtube_publisher, "schedule_video") as schedule_video,
        ):
            main._execute_youtube_publish_job(job)

        workflow = database.get_youtube_publish_workflow("publish-test-0")
        publication = database.get_video_publication_by_youtube_id("youtube-private")
        self.assertEqual(workflow["status"], "uploaded_private")
        self.assertEqual(publication["privacy_status"], "private")
        self.assertEqual(database.get_video(video_id)["publish_status"], "uploaded_private")
        schedule_video.assert_not_called()

    def test_build_default_visual_scene_plan_meets_quality_spec(self):
        windows = [
            {"index": 0, "start": 0.0, "end": 30.0, "duration": 30.0, "transcript": "Mở đầu bài học về hôn nhân gia đình."},
            {"index": 1, "start": 30.0, "end": 60.0, "duration": 30.0, "transcript": "Phần tiếp theo làm rõ các dấu hiệu nhận biết."},
        ]
        plan = video_production.build_default_visual_scene_plan(windows, "Học Cách Thấu Hiểu", "Realistic cinematic")
        self.assertIn("scenes", plan)
        self.assertEqual(len(plan["scenes"]), 2)
        validated = video_production.validate_visual_scene_plan(plan, windows)
        self.assertEqual(len(validated["scenes"]), 2)
        for s in validated["scenes"]:
            self.assertGreaterEqual(len(s["prompt"]), 80)

    def test_trigger_render_video_api_endpoints(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=render123",
            "Test Render Video",
            "Transcript",
            "Script",
        )
        database.upsert_audio_task(
            video_id=video_id,
            request_hash="hash-123456",
            task_id="task-123",
            status="completed",
            audio_url="http://127.0.0.1:8080/api/audio/sample.mp3",
            error="",
            segments_json="[]",
            voice_id="sample-voice",
            voice_name="Sample Voice",
        )
        with patch.object(main, "_kick_production_queue"):
            res = main.trigger_render_video(video_id)
            self.assertTrue(res["success"])
            self.assertEqual(res["status"], "queued")

            status = main.get_render_status(video_id)
            self.assertEqual(status["video_id"], video_id)
            self.assertIsNotNone(status["job"])
            self.assertEqual(status["job"]["job_type"], "video_render")

    def test_produce_video_flow_without_comfyui(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=flow-test",
            "Flow Test Video",
            "Transcript",
            "Script",
        )
        fake_audio = Path(self.temporary_directory.name) / "audio.mp3"
        fake_audio.write_bytes(b"dummy audio content")
        fake_srt = Path(self.temporary_directory.name) / "captions.srt"
        fake_srt.write_text("1\n00:00:00,000 --> 00:00:30,000\nXin chào các bạn.\n", encoding="utf-8")
        fake_img = Path(self.temporary_directory.name) / "scene_0.png"
        fake_img.write_bytes(b"dummy image")

        prepared = {
            "video": database.get_video(video_id),
            "audio_path": fake_audio,
            "srt_path": fake_srt,
            "caption_hash": "dummy_caption_hash",
            "plan_hash": "dummy_plan_hash",
            "windows": [
                {"index": 0, "start": 0.0, "end": 30.0, "duration": 30.0, "transcript": "Xin chào các bạn."}
            ],
        }

        with (
            patch.object(video_production, "prepare_visual_plan_inputs", return_value=prepared),
            patch.object(video_production, "generate_scene_images", return_value=[fake_img]),
            patch.object(
                video_production,
                "render_video",
                return_value={"id": 1, "video_id": video_id, "artifact_type": "final_mp4", "status": "ready"}
            ) as mock_render,
        ):
            res = video_production.produce_video(
                video_id=video_id,
                snapshot={"image_generation_settings": {"style_prompt": "Cinematic"}},
                progress=lambda msg, stage: None,
                cancel_check=lambda: None,
            )

            self.assertIn("artifact", res)
            self.assertEqual(res["artifact"]["status"], "ready")
            self.assertEqual(len(res["scenes"]), 1)
            mock_render.assert_called_once()

    def test_trigger_render_video_recreate_vs_resume_mode(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=sample-mode-test",
            "Sample Mode Video",
            "Transcript",
            "Script",
        )
        database.upsert_audio_task(
            video_id=video_id,
            request_hash="hash-mode-test",
            task_id="task-mode-123",
            status="completed",
            audio_url="http://127.0.0.1:8080/api/audio/sample_mode.mp3",
            error="",
            segments_json="[]",
            voice_id="sample-voice",
            voice_name="Sample Voice",
        )
        with patch.object(main, "_kick_production_queue"):
            # 1. Resume mode (default)
            res_resume = main.trigger_render_video(video_id, mode="resume")
            self.assertTrue(res_resume["success"])
            job_resume = database.get_system_job(res_resume["job_id"])
            self.assertFalse(job_resume["payload"]["force_new_project"])
            self.assertEqual(job_resume["payload"]["mode"], "resume")

            # 2. Recreate mode
            res_recreate = main.trigger_render_video(video_id, mode="recreate")
            self.assertTrue(res_recreate["success"])
            job_recreate = database.get_system_job(res_recreate["job_id"])
            self.assertTrue(job_recreate["payload"]["force_new_project"])
            self.assertEqual(job_recreate["payload"]["mode"], "recreate")
            self.assertIn("Tạo mới", job_recreate["title"])

    def test_produce_video_propagates_force_new_project(self):
        video_id = database.save_video(
            "https://www.youtube.com/watch?v=flow-force-new",
            "Force New Video",
            "Transcript",
            "Script",
        )
        fake_audio = Path(self.temporary_directory.name) / "audio2.mp3"
        fake_audio.write_bytes(b"dummy audio")
        fake_srt = Path(self.temporary_directory.name) / "captions2.srt"
        fake_srt.write_text("1\n00:00:00,000 --> 00:00:30,000\nHello.\n", encoding="utf-8")
        fake_img = Path(self.temporary_directory.name) / "scene_new.png"
        fake_img.write_bytes(b"dummy image")

        prepared = {
            "video": database.get_video(video_id),
            "audio_path": fake_audio,
            "srt_path": fake_srt,
            "caption_hash": "cap_hash",
            "plan_hash": "plan_hash",
            "windows": [{"index": 0, "start": 0.0, "end": 30.0, "duration": 30.0, "transcript": "Hello."}],
        }

        with (
            patch.object(video_production, "prepare_visual_plan_inputs", return_value=prepared),
            patch.object(video_production, "generate_scene_images", return_value=[fake_img]) as mock_gen,
            patch.object(
                video_production,
                "render_video",
                return_value={"id": 2, "video_id": video_id, "artifact_type": "final_mp4", "status": "ready"},
            ),
        ):
            video_production.produce_video(
                video_id=video_id,
                snapshot={"image_generation_settings": {"style_prompt": "Cinematic"}},
                progress=lambda msg, stage: None,
                cancel_check=lambda: None,
                force_new_project=True,
            )

            mock_gen.assert_called_once()
            _, kwargs = mock_gen.call_args
            self.assertTrue(kwargs.get("force_new_project"))


if __name__ == "__main__":
    unittest.main()


