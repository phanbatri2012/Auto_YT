import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt import main
from auto_yt.services import database, voice_config


VOICE_ID = "2559f15a-b9bc-4f39-b7cb-82b21d45e51e"
SECOND_VOICE_ID = "a39e4493-3a8a-4be8-bd13-b96f2f5c4906"
SYSTEM_VOICE_ID = "Vietnamese_crisp_announcer_v2"
SCRIPT = """
### [INTRO]
Nội dung kiểm tra giọng đọc.

### [BODY]
Đây là phần nội dung chính.

### [OUTRO]
Kết thúc video.

### [METADATA & QUIZ]
TIÊU ĐỀ: Video kiểm tra
""".strip()


class VoiceSelectionTests(unittest.TestCase):
    def test_system_voice_id_is_accepted(self):
        config = voice_config.validate_voice_config({
            "active_voice_id": SYSTEM_VOICE_ID,
            "voices": [
                {"id": VOICE_ID, "name": "Giọng tùy chỉnh"},
                {"id": SYSTEM_VOICE_ID, "name": "Giọng phát thanh"},
            ],
        })

        self.assertEqual(config["active_voice_id"], SYSTEM_VOICE_ID)
        self.assertEqual(config["voices"][1]["id"], SYSTEM_VOICE_ID)

    def test_unsafe_system_voice_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Voice ID"):
            voice_config.validate_voice_config({
                "active_voice_id": "../invalid-voice",
                "voices": [
                    {"id": "../invalid-voice", "name": "Giọng không hợp lệ"},
                ],
            })

    def test_voice_configuration_is_validated_and_persisted(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "voices.json"
            with patch.object(voice_config, "VOICE_CONFIG_PATH", config_path):
                saved = voice_config.save_voice_config({
                    "active_voice_id": SECOND_VOICE_ID,
                    "voices": [
                        {"id": VOICE_ID, "name": "Giọng kể chuyện"},
                        {"id": SECOND_VOICE_ID, "name": "Giọng tin tức"},
                    ],
                })

                self.assertEqual(
                    voice_config.load_voice_config(),
                    saved,
                )
                self.assertEqual(
                    voice_config.get_voice(SECOND_VOICE_ID)["name"],
                    "Giọng tin tức",
                )

    def test_video_and_audio_task_store_voice_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "database.db"
            with patch.object(database, "DB_PATH", database_path):
                database.init_db()
                video_id = database.save_video(
                    "https://youtube.test/voice",
                    "Video test",
                    "Transcript",
                    SCRIPT,
                    voice_id=VOICE_ID,
                    voice_name="Giọng kể chuyện",
                )
                task = database.upsert_audio_task(
                    video_id=video_id,
                    request_hash="request-hash",
                    task_id="task-id",
                    status="pending",
                    voice_id=VOICE_ID,
                    voice_name="Giọng kể chuyện",
                )

                video = database.get_video(video_id)
                dashboard_video = database.get_all_videos()["items"][0]
                self.assertEqual(video["voice_id"], VOICE_ID)
                self.assertEqual(video["voice_name"], "Giọng kể chuyện")
                self.assertEqual(dashboard_video["voice_name"], "Giọng kể chuyện")
                self.assertEqual(task["voice_id"], VOICE_ID)

    def test_audio_submission_uses_voice_saved_on_video(self):
        stored_versions = []

        def store_task(**kwargs):
            stored_versions.append(kwargs)
            return {
                **kwargs,
                "updated_at": "2026-08-11T00:00:00+00:00",
            }

        with (
            patch.object(main, "_require_audio_review_approval"),
            patch.object(
                main.db,
                "get_video",
                return_value={
                    "generated_script": SCRIPT,
                    "voice_id": VOICE_ID,
                    "voice_name": "Giọng kể chuyện",
                },
            ),
            patch.object(main.db, "get_audio_task", return_value=None),
            patch.object(main.db, "get_audio_task_by_request_hash", return_value=None),
            patch.object(main.tts, "find_matching_task", return_value=None),
            patch.object(
                main.tts,
                "submit_tts_task",
                return_value={"id": "new-task", "status": "pending"},
            ) as submit_task,
            patch.object(main.db, "upsert_audio_task", side_effect=store_task),
            patch.object(main, "_start_audio_watcher"),
        ):
            result = main._ensure_audio_task(91)

        submit_task.assert_called_once()
        self.assertEqual(submit_task.call_args.args[1], VOICE_ID)
        self.assertEqual(result["voice_id"], VOICE_ID)
        self.assertEqual(stored_versions[-1]["voice_name"], "Giọng kể chuyện")

    def test_regeneration_rejects_unchanged_script_and_voice(self):
        request_hash = main.tts.get_generation_request_hash(
            main.apply_tts_filters(main.get_clean_script_for_tts(SCRIPT)),
            VOICE_ID,
        )
        request = main.RegenerateAudioRequest(
            voice_id=VOICE_ID,
            confirm_credit_charge=True,
        )
        with (
            patch.object(
                main.voice_config,
                "get_voice",
                return_value={"id": VOICE_ID, "name": "Giọng kể chuyện"},
            ),
            patch.object(
                main.db,
                "get_video",
                return_value={
                    "generated_script": SCRIPT + "\n\n### [AUDIO]\nhttps://old.mp3"
                },
            ),
            patch.object(
                main.db,
                "get_audio_task",
                return_value={
                    "request_hash": request_hash,
                    "status": "completed",
                },
            ),
            patch.object(main, "_ensure_audio_task") as ensure_audio,
        ):
            with self.assertRaisesRegex(main.HTTPException, "tạo trùng"):
                main.regenerate_audio_for_video(91, request)

        ensure_audio.assert_not_called()

    def test_regeneration_keeps_old_audio_until_new_voice_completes(self):
        request = main.RegenerateAudioRequest(
            voice_id=SECOND_VOICE_ID,
            confirm_credit_charge=True,
        )
        pending_task = {
            "video_id": 91,
            "request_hash": "new-request-hash",
            "task_id": "new-task",
            "status": "pending",
            "audio_url": "",
            "error": "",
            "voice_id": SECOND_VOICE_ID,
            "voice_name": "Giọng tin tức",
            "segments_json": "",
            "updated_at": "2026-08-11T00:00:00+00:00",
        }
        with (
            patch.object(
                main.voice_config,
                "get_voice",
                return_value={
                    "id": SECOND_VOICE_ID,
                    "name": "Giọng tin tức",
                },
            ),
            patch.object(
                main.db,
                "get_video",
                return_value={
                    "generated_script": SCRIPT + "\n\n### [AUDIO]\nhttps://old.mp3"
                },
            ),
            patch.object(
                main.db,
                "get_audio_task",
                return_value={
                    "request_hash": "old-request-hash",
                    "status": "completed",
                },
            ),
            patch.object(
                main,
                "_ensure_audio_task",
                return_value=pending_task,
            ) as ensure_audio,
        ):
            result = main.regenerate_audio_for_video(91, request)

        self.assertTrue(result["success"])
        self.assertTrue(result["preserved_previous_audio"])
        ensure_audio.assert_called_once_with(
            91,
            requested_voice_id=SECOND_VOICE_ID,
            requested_voice_name="Giọng tin tức",
        )


if __name__ == "__main__":
    unittest.main()
