import json
import tempfile
import unittest
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from auto_yt import main
from auto_yt.services import database, tts_registry, voice_config


GENMAX_VOICE_ID = "e1d9617c-045c-4072-8d17-9be0ec113723"
OMNI_VOICE_ID = "d7d23aef-84e3-45d5-a4f0-75f59ded9718"
OMNI_PROFILE_ID = "45e715da-0d05-454d-901f-272c41923061"


class VoiceCatalogMigrationTests(unittest.TestCase):
    def test_v1_migration_preserves_ids_default_and_creates_one_backup(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "voices.json"
            legacy = {
                "active_voice_id": "Vietnamese_crisp_announcer_v2",
                "voices": [
                    {"id": GENMAX_VOICE_ID, "name": "Giọng Đ.Đoàn"},
                    {
                        "id": "Vietnamese_crisp_announcer_v2",
                        "name": "Giọng hệ thống",
                    },
                ],
            }
            config_path.write_text(
                json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
            )
            with patch.object(voice_config, "VOICE_CONFIG_PATH", config_path):
                migrated = voice_config.load_voice_config()
                migrated_again = voice_config.load_voice_config()

            self.assertEqual(migrated, migrated_again)
            self.assertEqual(migrated["schema_version"], 2)
            self.assertEqual(
                migrated["default_voice_id"], "Vietnamese_crisp_announcer_v2"
            )
            self.assertEqual(
                {voice["id"] for voice in migrated["voices"]},
                {GENMAX_VOICE_ID, "Vietnamese_crisp_announcer_v2"},
            )
            self.assertTrue(config_path.with_name("voices.json.v1.bak").exists())
            for voice in migrated["voices"]:
                self.assertEqual(voice["provider_id"], "genmax")
                self.assertEqual(voice["provider_voice_id"], voice["id"])

    def test_secret_fields_are_rejected_from_public_provider_config(self):
        config = voice_config._default_config()
        config["providers"][0]["config"] = {"api_key": "must-not-leak"}
        with self.assertRaisesRegex(ValueError, "bí mật"):
            voice_config.validate_voice_config(config)


class ProviderAwareAudioTests(unittest.TestCase):
    @staticmethod
    def snapshot(
        provider_id: str,
        provider_voice_id: str,
        revision: int = 1,
        config: dict | None = None,
    ) -> dict:
        return {
            "voice_id": OMNI_VOICE_ID,
            "voice_name": "Giọng kiểm thử",
            "provider_id": provider_id,
            "provider_voice_id": provider_voice_id,
            "voice_revision": revision,
            "config": config or {},
        }

    def test_request_hash_changes_with_provider_voice_revision_and_settings(self):
        text = "Nội dung giống nhau."
        base = tts_registry.get_request_hash(
            text,
            OMNI_VOICE_ID,
            self.snapshot("genmax", GENMAX_VOICE_ID),
        )
        variants = {
            tts_registry.get_request_hash(
                text,
                OMNI_VOICE_ID,
                self.snapshot("omnivoice", OMNI_PROFILE_ID),
            ),
            tts_registry.get_request_hash(
                text,
                OMNI_VOICE_ID,
                self.snapshot("genmax", "different-remote-id"),
            ),
            tts_registry.get_request_hash(
                text,
                OMNI_VOICE_ID,
                self.snapshot("genmax", GENMAX_VOICE_ID, revision=2),
            ),
            tts_registry.get_request_hash(
                text,
                OMNI_VOICE_ID,
                self.snapshot("genmax", GENMAX_VOICE_ID, config={"speed": 1.1}),
            ),
        }
        self.assertNotIn(base, variants)
        self.assertEqual(len(variants), 4)

    def test_disabled_provider_fails_closed_without_fallback(self):
        provider = {
            "id": "omnivoice",
            "type": "local",
            "display_name": "OmniVoice",
            "enabled": False,
            "health": "unknown",
            "capabilities": {},
            "config": {},
        }
        with patch.object(voice_config, "get_provider", return_value=provider):
            with self.assertRaisesRegex(ValueError, "đang bị tắt"):
                tts_registry.get_provider("omnivoice")

    def test_public_provider_response_never_contains_credentials(self):
        provider = voice_config._default_providers()[0]
        with (
            patch.object(main.db, "get_active_audio_tasks", return_value=[]),
            patch.object(main.tts.genmax_service, "has_api_key", return_value=True),
        ):
            public = main._public_provider(provider, main._provider_health("genmax"))
        serialized = json.dumps(public).casefold()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("token", serialized)
        self.assertNotIn("secret", serialized)
        self.assertTrue(public["health"]["configured"])

    def test_omnivoice_snapshot_has_versioned_production_defaults(self):
        snapshot = voice_config.build_voice_snapshot({
            "id": OMNI_VOICE_ID,
            "name": "Giọng OmniVoice",
            "provider_id": "omnivoice",
            "provider_voice_id": OMNI_PROFILE_ID,
            "revision": 1,
            "config": {},
        })

        self.assertEqual(snapshot["config"]["engine_revision"], 3)
        self.assertFalse(snapshot["config"]["denoise"])
        self.assertEqual(snapshot["config"]["target_words_per_minute"], 145)
        self.assertEqual(snapshot["config"]["max_chunk_chars"], 220)

    def test_genmax_snapshot_does_not_receive_omnivoice_defaults(self):
        snapshot = voice_config.build_voice_snapshot({
            "id": GENMAX_VOICE_ID,
            "name": "Giọng Genmax",
            "provider_id": "genmax",
            "provider_voice_id": GENMAX_VOICE_ID,
            "revision": 1,
            "config": {},
        })

        self.assertEqual(snapshot["config"], {})

    def test_changed_script_cannot_accept_completed_provider_audio(self):
        snapshot = self.snapshot(
            "omnivoice",
            OMNI_PROFILE_ID,
            config={"engine_revision": 2},
        )
        stored_task = {
            "video_id": 70,
            "request_hash": tts_registry.get_request_hash(
                "Kịch bản cũ.", OMNI_VOICE_ID, snapshot
            ),
            "task_id": "omnivoice:45e715da-0d05-454d-901f-272c41923061",
            "status": "processing",
            "audio_url": "",
            "error": "",
            "segments_json": "",
            "voice_id": OMNI_VOICE_ID,
            "voice_name": "Giọng kiểm thử",
            "tts_provider_id": "omnivoice",
            "voice_revision": 1,
            "voice_snapshot_json": json.dumps(snapshot),
        }

        with (
            patch.object(main.db, "get_video", return_value={
                "generated_script": "### [BODY]\nKịch bản mới hoàn toàn.",
                "video_status": "unpublished",
            }),
            patch.object(main.db, "get_audio_task", return_value=stored_task),
            patch.object(main.db, "upsert_audio_task", side_effect=lambda **kwargs: kwargs),
            patch.object(main.tts, "get_tts_task") as poll,
        ):
            result = main._sync_audio_task_unlocked(70)

        poll.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertIn("đã thay đổi", result["error"])


class OmniVoicePreviewTests(unittest.TestCase):
    @staticmethod
    def voice() -> dict:
        return {
            "id": OMNI_VOICE_ID,
            "name": "Giọng OmniVoice thử nghiệm",
            "provider_id": "omnivoice",
            "provider_voice_id": OMNI_PROFILE_ID,
            "revision": 3,
            "status": "active",
            "config": {"target_words_per_minute": 150},
        }

    def test_preview_accepts_arbitrary_short_text_and_uses_separate_hash(self):
        text = "Đây là một nội dung tiếng Việt dùng để nghe thử."
        voice = self.voice()
        snapshot = voice_config.build_voice_snapshot(voice)
        provider = MagicMock()
        provider.submit.return_value = {
            "id": "omnivoice:preview-provider-job",
            "status": "queued",
        }

        def save_preview(**values):
            return {
                "id": values["preview_id"],
                "provider_task_id": values["provider_task_id"],
                "request_hash": values["request_hash"],
                "status": values["status"],
                "text": values["text"],
                "character_count": len(values["text"]),
                "voice_id": values["voice_id"],
                "voice_name": values["voice_name"],
                "tts_provider_id": values["tts_provider_id"],
                "voice_revision": values["voice_revision"],
                "voice_snapshot": json.loads(values["voice_snapshot_json"]),
                "audio_filename": "",
                "duration_seconds": None,
                "error": "",
                "created_at": database.utc_now(),
                "updated_at": database.utc_now(),
                "expires_at": values["expires_at"],
            }

        with (
            patch.object(main.tts, "get_voice_context", return_value=(voice, provider)),
            patch.object(main.db, "create_tts_preview", side_effect=save_preview),
            patch.object(main, "_provider_health", return_value={"state": "ready"}),
        ):
            result = main.create_tts_preview(
                main.TTSPreviewCreateData(voice_id=OMNI_VOICE_ID, text=f"  {text}  ")
            )

        submitted_text, submitted_voice, preview_hash = provider.submit.call_args.args
        production_hash = tts_registry.get_request_hash(text, OMNI_VOICE_ID, snapshot)
        self.assertEqual(submitted_text, text)
        self.assertEqual(submitted_voice["config"]["engine_revision"], 3)
        self.assertNotEqual(preview_hash, production_hash)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["character_count"], len(text))

    def test_preview_rejects_non_omnivoice_voice(self):
        voice = {
            **self.voice(),
            "provider_id": "genmax",
            "provider_voice_id": GENMAX_VOICE_ID,
        }
        with patch.object(
            main.tts,
            "get_voice_context",
            return_value=(voice, MagicMock()),
        ):
            with self.assertRaisesRegex(Exception, "chỉ hỗ trợ giọng OmniVoice") as context:
                main.create_tts_preview(
                    main.TTSPreviewCreateData(
                        voice_id=voice["id"],
                        text="Nội dung thử.",
                    )
                )
        self.assertEqual(context.exception.status_code, 400)

    def test_preview_endpoint_rejects_more_than_200_trimmed_characters(self):
        with self.assertRaisesRegex(Exception, "tối đa 200 ký tự") as context:
            main.create_tts_preview(
                main.TTSPreviewCreateData(
                    voice_id=OMNI_VOICE_ID,
                    text="x" * 201,
                )
            )
        self.assertEqual(context.exception.status_code, 400)

    def test_completed_preview_is_materialized_and_duration_checked(self):
        preview_id = "ee32bf20-721f-45ee-a65d-9a2e4ae26745"
        preview = {
            "id": preview_id,
            "provider_task_id": "omnivoice:provider-job",
            "request_hash": "hash",
            "status": "processing",
            "text": "Nội dung để nghe thử giọng nói.",
            "character_count": 33,
            "voice_id": OMNI_VOICE_ID,
            "voice_name": "Giọng thử",
            "tts_provider_id": "omnivoice",
            "voice_revision": 2,
            "voice_snapshot": {"config": {"min_words_per_minute": 105}},
            "audio_filename": "",
            "duration_seconds": None,
            "error": "",
            "created_at": database.utc_now(),
            "updated_at": database.utc_now(),
            "expires_at": (
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(hours=1)
            ).isoformat(),
        }

        def update_preview(_preview_id, **changes):
            return {**preview, **changes}

        with (
            patch.object(main, "_get_unexpired_tts_preview", return_value=preview),
            patch.object(main.tts, "get_tts_task", return_value={
                "id": "omnivoice:provider-job",
                "status": "completed",
            }),
            patch.object(
                main.tts,
                "materialize_preview_audio",
                return_value=(f"{preview_id}.wav", 4.2),
            ),
            patch.object(main.audio_utils, "validate_spoken_duration") as validate,
            patch.object(main.db, "update_tts_preview", side_effect=update_preview),
        ):
            result = main._sync_tts_preview(preview_id)

        validate.assert_called_once()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["audio_filename"], f"{preview_id}.wav")

    def test_preview_record_survives_reopen_and_expires(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "database.db"
            preview_id = "c0188646-cddb-4501-a2c6-093d33dc50cc"
            expired_at = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(minutes=1)
            ).isoformat()
            with patch.object(database, "DB_PATH", database_path):
                database.init_db()
                database.create_tts_preview(
                    preview_id=preview_id,
                    provider_task_id="omnivoice:provider-job",
                    request_hash="preview-hash",
                    status="queued",
                    text="Nội dung thử.",
                    voice_id=OMNI_VOICE_ID,
                    voice_name="Giọng thử",
                    tts_provider_id="omnivoice",
                    voice_revision=2,
                    voice_snapshot_json="{}",
                    expires_at=expired_at,
                )
                reopened = database.get_tts_preview(preview_id)
                expired = database.delete_expired_tts_previews()

            self.assertEqual(reopened["text"], "Nội dung thử.")
            self.assertEqual(expired[0]["id"], preview_id)


if __name__ == "__main__":
    unittest.main()
