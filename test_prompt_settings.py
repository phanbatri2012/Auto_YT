import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from auto_yt import main
from auto_yt.services import chatgpt_projects


PROJECT_URL = chatgpt_projects.DEFAULT_CHATGPT_PROJECT_URL
SECOND_PROJECT_URL = "https://chatgpt.com/g/g-p-second-project/project"


def make_prompts_data() -> dict:
    return {
        "active_version": "default",
        "versions": {
            "default": {
                "name": "Bộ cũ",
                "project_url": PROJECT_URL,
                "default_voice_id": "",
                "prompts": {
                    key: f"old-{key}" for key in main.PROMPT_FIELD_KEYS
                },
            },
            "second": {
                "name": "Bộ thứ hai",
                "project_url": PROJECT_URL,
                "default_voice_id": "",
                "prompts": {
                    key: f"second-{key}" for key in main.PROMPT_FIELD_KEYS
                },
            },
        },
    }


class PromptSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.prompts_path = Path(self.temporary_directory.name) / "prompts.json"
        self.prompts_path.write_text(
            json.dumps(make_prompts_data(), ensure_ascii=False),
            encoding="utf-8",
        )
        self.path_patch = patch.object(main, "PROMPTS_PATH", self.prompts_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temporary_directory.cleanup()

    def read_saved_data(self) -> dict:
        return json.loads(self.prompts_path.read_text(encoding="utf-8"))

    def test_rename_only_changes_selected_version_name(self):
        before = main._read_prompts_config()

        main.save_prompt_version_name(
            "default",
            main.PromptVersionNameData(name="  Bộ prompt mới  "),
        )

        saved = self.read_saved_data()
        self.assertEqual(saved["versions"]["default"]["name"], "Bộ prompt mới")
        self.assertEqual(
            saved["versions"]["default"]["prompts"],
            before["versions"]["default"]["prompts"],
        )
        self.assertEqual(saved["versions"]["second"], before["versions"]["second"])

    def test_project_save_only_changes_project_url(self):
        before = self.read_saved_data()

        main.save_prompt_project(
            "default",
            main.PromptProjectData(project_url=SECOND_PROJECT_URL),
        )

        saved = self.read_saved_data()
        self.assertEqual(
            saved["versions"]["default"]["project_url"],
            SECOND_PROJECT_URL,
        )
        self.assertEqual(
            saved["versions"]["default"]["prompts"],
            before["versions"]["default"]["prompts"],
        )

    def test_prompt_save_only_changes_requested_prompt(self):
        before = main._read_prompts_config()

        main.save_prompt_field(
            "default",
            "intro",
            main.PromptFieldData(value="intro mới"),
        )

        saved = self.read_saved_data()
        self.assertEqual(saved["versions"]["default"]["prompts"]["intro"], "intro mới")
        self.assertEqual(
            saved["versions"]["default"]["prompts"]["body"],
            before["versions"]["default"]["prompts"]["body"],
        )
        self.assertEqual(saved["versions"]["second"], before["versions"]["second"])

    def test_default_voice_save_only_changes_selected_prompt_version(self):
        before = main._read_prompts_config()
        voice_id = "a39e4493-3a8a-4be8-bd13-b96f2f5c4906"

        with patch.object(
            main.voice_config,
            "get_voice",
            return_value={"id": voice_id, "name": "Giọng tin tức"},
        ):
            main.save_prompt_default_voice(
                "default",
                main.PromptDefaultVoiceData(voice_id=voice_id),
            )

        saved = self.read_saved_data()
        self.assertEqual(
            saved["versions"]["default"]["default_voice_id"],
            voice_id,
        )
        self.assertEqual(
            saved["versions"]["default"]["prompts"],
            before["versions"]["default"]["prompts"],
        )
        self.assertEqual(saved["versions"]["second"], before["versions"]["second"])

    def test_reads_default_voice_for_requested_prompt_version(self):
        data = self.read_saved_data()
        data["versions"]["second"]["default_voice_id"] = "voice-second"
        self.prompts_path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

        self.assertEqual(
            main._get_prompt_default_voice_id("second"),
            "voice-second",
        )

    def test_old_prompt_versions_receive_empty_default_youtube_channel(self):
        data = main._read_prompts_config()

        self.assertEqual(
            data["versions"]["default"]["default_youtube_channel_id"],
            "",
        )
        self.assertEqual(
            data["versions"]["second"]["default_youtube_channel_id"],
            "",
        )

    def test_default_youtube_channel_save_only_changes_selected_prompt_version(self):
        before = main._read_prompts_config()
        with patch.object(
            main.db,
            "get_youtube_channel_by_channel_id",
            return_value={"id": 7, "channel_id": "UC-default", "title": "Kênh A"},
        ):
            main.save_prompt_default_youtube_channel(
                "default",
                main.PromptDefaultYoutubeChannelData(channel_id=" UC-default "),
            )

        saved = self.read_saved_data()
        self.assertEqual(
            saved["versions"]["default"]["default_youtube_channel_id"],
            "UC-default",
        )
        self.assertEqual(
            saved["versions"]["second"],
            before["versions"]["second"],
        )

    def test_default_youtube_channel_rejects_unconnected_channel(self):
        with patch.object(
            main.db,
            "get_youtube_channel_by_channel_id",
            return_value=None,
        ):
            with self.assertRaises(HTTPException) as error:
                main.save_prompt_default_youtube_channel(
                    "default",
                    main.PromptDefaultYoutubeChannelData(channel_id="UC-missing"),
                )

        self.assertEqual(error.exception.status_code, 400)

    def test_disconnecting_channel_clears_every_prompt_reference(self):
        data = self.read_saved_data()
        for version in data["versions"].values():
            version["default_youtube_channel_id"] = "UC-disconnected"
        self.prompts_path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

        cleared = main._clear_prompt_default_youtube_channel_id(
            "UC-disconnected"
        )
        saved = self.read_saved_data()

        self.assertEqual(set(cleared), {"default", "second"})
        self.assertTrue(
            all(
                not version["default_youtube_channel_id"]
                for version in saved["versions"].values()
            )
        )

    def test_video_list_resolves_default_channel_from_its_prompt(self):
        data = self.read_saved_data()
        data["versions"]["default"]["default_youtube_channel_id"] = "UC-default"
        self.prompts_path.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
        result = {
            "total": 1,
            "count_published": 0,
            "count_unpublished": 1,
            "items": [{"id": 12, "prompt_version": "default"}],
        }
        with (
            patch.object(main.db, "get_all_videos", return_value=result),
            patch.object(
                main.db,
                "list_youtube_channels",
                return_value=[
                    {"id": 9, "channel_id": "UC-default", "title": "Kênh A"}
                ],
            ),
            patch.object(main, "load_checkpoint", return_value=None),
        ):
            response = main.get_videos()

        video = response["items"][0]
        self.assertEqual(video["default_youtube_channel_id"], "UC-default")
        self.assertEqual(video["default_youtube_channel_db_id"], 9)
        self.assertEqual(video["default_youtube_channel_title"], "Kênh A")

    def test_publication_uses_prompt_channel_when_request_omits_channel(self):
        publication = {"id": 3, "youtube_channel_id": 9}
        with (
            patch.object(
                main.db,
                "get_video",
                return_value={"id": 12, "prompt_version": "default"},
            ),
            patch.object(
                main,
                "_get_prompt_default_youtube_channel_id",
                return_value="UC-default",
            ),
            patch.object(
                main.db,
                "get_youtube_channel_by_channel_id",
                return_value={"id": 9, "channel_id": "UC-default", "title": "Kênh A"},
            ),
            patch.object(
                main,
                "_get_youtube_access_token",
                return_value=({"id": 9, "channel_id": "UC-default"}, "token"),
            ),
            patch.object(
                main.youtube_comments,
                "get_video_details",
                return_value={
                    "channel_id": "UC-default",
                    "title": "Video đã đăng",
                    "published_at": "2026-09-11T00:00:00Z",
                },
            ),
            patch.object(
                main.db,
                "save_video_publication",
                return_value=publication,
            ) as save_publication,
        ):
            response = main.add_video_publication(
                12,
                main.VideoPublicationRequest(
                    published_url="https://www.youtube.com/watch?v=abc_DEF-12"
                ),
            )

        self.assertEqual(response, publication)
        self.assertEqual(
            save_publication.call_args.kwargs["youtube_channel_id"],
            9,
        )

    def test_publication_without_prompt_channel_is_saved_without_verification(self):
        publication = {"id": 4, "youtube_channel_id": None}
        with (
            patch.object(
                main.db,
                "get_video",
                return_value={"id": 12, "prompt_version": "default"},
            ),
            patch.object(
                main,
                "_get_prompt_default_youtube_channel_id",
                return_value="",
            ),
            patch.object(main, "_get_youtube_access_token") as get_access_token,
            patch.object(
                main.youtube_comments,
                "get_video_details",
            ) as get_video_details,
            patch.object(
                main.db,
                "save_video_publication",
                return_value=publication,
            ) as save_publication,
        ):
            response = main.add_video_publication(
                12,
                main.VideoPublicationRequest(
                    published_url="https://youtu.be/abc_DEF-12"
                ),
            )

        self.assertEqual(response["youtube_channel_id"], None)
        self.assertEqual(
            save_publication.call_args.kwargs["youtube_channel_id"],
            None,
        )
        self.assertEqual(
            save_publication.call_args.kwargs["published_url"],
            "https://www.youtube.com/watch?v=abc_DEF-12",
        )
        get_access_token.assert_not_called()
        get_video_details.assert_not_called()

    def test_old_prompt_versions_receive_the_full_default_pipeline(self):
        data = main._read_prompts_config()

        self.assertEqual(
            data["versions"]["default"]["pipeline"],
            chatgpt_projects.DEFAULT_PROMPT_PIPELINE,
        )
        self.assertEqual(
            data["versions"]["second"]["pipeline"],
            chatgpt_projects.DEFAULT_PROMPT_PIPELINE,
        )

    def test_pipeline_save_only_changes_selected_prompt_version(self):
        before = main._read_prompts_config()
        pipeline = {
            "metadata": True,
            "chapters": False,
            "thumbnail_with_text": True,
            "thumbnail_without_text": False,
            "audio": False,
        }

        main.save_prompt_pipeline(
            "default",
            main.PromptPipelineData(**pipeline),
        )

        saved = self.read_saved_data()
        self.assertEqual(
            saved["versions"]["default"]["pipeline"],
            {
                **pipeline,
                "video_render": False,
                "youtube_upload": False,
                "youtube_schedule": False,
            },
        )
        self.assertEqual(
            saved["versions"]["second"]["pipeline"],
            before["versions"]["second"]["pipeline"],
        )

    def test_pipeline_save_enables_video_render_and_persists(self):
        result = main.save_prompt_pipeline(
            "default",
            main.PromptPipelineData(
                metadata=True,
                chapters=True,
                thumbnail_with_text=True,
                thumbnail_without_text=True,
                audio=True,
                video_render=True,
            ),
        )
        saved = self.read_saved_data()
        self.assertTrue(saved["versions"]["default"]["pipeline"]["video_render"])
        self.assertTrue(result["pipeline"]["video_render"])

    def test_image_generation_save_endpoint(self):
        result = main.save_prompt_image_generation(
            "default",
            main.PromptImageGenerationData(
                style_prompt="cinematic dramatic lighting",
                avoid_prompt="cartoon, 3D",
                density=35,
                thumbnail_variant="with_text",
            ),
        )
        saved = self.read_saved_data()
        saved_img = saved["versions"]["default"]["image_generation_settings"]
        self.assertEqual(saved_img["style_prompt"], "cinematic dramatic lighting")
        self.assertEqual(saved_img["avoid_prompt"], "cartoon, 3D")
        self.assertEqual(saved_img["negative_prompt"], "cartoon, 3D")
        self.assertEqual(saved_img["density"], 35)
        self.assertEqual(saved_img["thumbnail_variant"], "with_text")
        self.assertEqual(result["version"]["image_generation_settings"]["density"], 35)
        self.assertEqual(result["version"]["image_generation_settings"]["negative_prompt"], "cartoon, 3D")

    def test_image_generation_save_negative_prompt_from_frontend(self):
        result = main.save_prompt_image_generation(
            "default",
            main.PromptImageGenerationData(
                style_prompt="cinematic dramatic lighting",
                negative_prompt="blurry, distorted, 3D render",
            ),
        )
        saved = self.read_saved_data()
        saved_img = saved["versions"]["default"]["image_generation_settings"]
        self.assertEqual(saved_img["negative_prompt"], "blurry, distorted, 3D render")
        self.assertEqual(saved_img["avoid_prompt"], "blurry, distorted, 3D render")
        self.assertEqual(
            result["version"]["image_generation_settings"]["negative_prompt"],
            "blurry, distorted, 3D render",
        )

    def test_image_generation_clear_negative_prompt(self):
        main.save_prompt_image_generation(
            "default",
            main.PromptImageGenerationData(
                negative_prompt="blurry, distorted",
                avoid_prompt="blurry, distorted",
            ),
        )
        result = main.save_prompt_image_generation(
            "default",
            main.PromptImageGenerationData(
                negative_prompt="",
                avoid_prompt="",
            ),
        )
        saved = self.read_saved_data()
        saved_img = saved["versions"]["default"]["image_generation_settings"]
        self.assertEqual(saved_img["negative_prompt"], "")
        self.assertEqual(saved_img["avoid_prompt"], "")
        self.assertEqual(result["version"]["image_generation_settings"]["negative_prompt"], "")

    def test_publishing_save_endpoint(self):
        result = main.save_prompt_publishing(
            "default",
            main.PromptPublishingData(
                category_id="22",
                language="vi",
                made_for_kids=False,
                notify_subscribers=True,
                contains_synthetic_media=True,
            ),
        )
        saved = self.read_saved_data()
        saved_pub = saved["versions"]["default"]["publishing_settings"]
        self.assertEqual(saved_pub["category_id"], "22")
        self.assertEqual(saved_pub["language"], "vi")
        self.assertFalse(saved_pub["made_for_kids"])
        self.assertEqual(result["version"]["publishing_settings"]["category_id"], "22")

    def test_pipeline_dependencies_enable_every_required_step(self):
        resolved, auto_enabled = chatgpt_projects.resolve_prompt_pipeline_dependencies(
            {
                **chatgpt_projects.DEFAULT_PROMPT_PIPELINE,
                "metadata": False,
                "chapters": False,
                "thumbnail_without_text": False,
                "audio": False,
                "youtube_schedule": True,
            },
            "without_text",
        )

        self.assertTrue(resolved["youtube_upload"])
        self.assertTrue(resolved["video_render"])
        self.assertTrue(resolved["metadata"])
        self.assertTrue(resolved["chapters"])
        self.assertTrue(resolved["audio"])
        self.assertTrue(resolved["thumbnail_without_text"])
        self.assertEqual(len(auto_enabled), len(set(auto_enabled)))

    def test_snapshot_normalization_preserves_selected_text_thumbnail(self):
        pipeline = chatgpt_projects.normalize_prompt_pipeline(
            {
                **chatgpt_projects.DEFAULT_PROMPT_PIPELINE,
                "thumbnail_with_text": True,
                "thumbnail_without_text": False,
                "youtube_upload": True,
            }
        )

        self.assertTrue(pipeline["thumbnail_with_text"])
        self.assertFalse(pipeline["thumbnail_without_text"])

    def test_production_snapshot_embeds_google_flow_settings(self):
        """Snapshot must include Google Flow image generation settings, not ComfyUI profile."""
        version = main._read_prompts_config()["versions"]["default"]
        version["image_generation_settings"] = {
            "style_prompt": "cinematic documentary",
            "avoid_prompt": "blurry text",
            "density": 30,
            "thumbnail_variant": "without_text",
        }
        snapshot = main._build_prompt_production_snapshot("default", version)
        img = snapshot.get("image_generation_settings") or {}
        self.assertEqual(img.get("provider"), "google_flow")
        self.assertEqual(img.get("model"), "nano_banana_pro")
        self.assertEqual(img.get("style_prompt"), "cinematic documentary")
        self.assertEqual(img.get("avoid_prompt"), "blurry text")
        self.assertEqual(img.get("density"), 30)
        # Ensure no ComfyUI keys leak into new snapshots
        self.assertNotIn("comfyui_workflow_profile", snapshot)
        self.assertNotIn("workflow_profile_id", img)


    def test_rejects_blank_name_and_unknown_prompt(self):
        with self.assertRaises(HTTPException) as blank_name_error:
            main.save_prompt_version_name(
                "default",
                main.PromptVersionNameData(name="   "),
            )
        self.assertEqual(blank_name_error.exception.status_code, 400)

        with self.assertRaises(HTTPException) as prompt_error:
            main.save_prompt_field(
                "default",
                "unknown",
                main.PromptFieldData(value="value"),
            )
        self.assertEqual(prompt_error.exception.status_code, 404)

    def test_locked_version_is_rejected_but_other_version_can_be_saved(self):
        with patch.object(
            main,
            "_get_locked_prompt_version",
            return_value="default",
        ):
            with self.assertRaises(HTTPException) as locked_error:
                main.save_prompt_field(
                    "default",
                    "intro",
                    main.PromptFieldData(value="không được lưu"),
                )
            main.save_prompt_field(
                "second",
                "intro",
                main.PromptFieldData(value="được phép lưu"),
            )

        self.assertEqual(locked_error.exception.status_code, 409)
        saved = self.read_saved_data()
        self.assertEqual(
            saved["versions"]["default"]["prompts"]["intro"],
            "old-intro",
        )
        self.assertEqual(
            saved["versions"]["second"]["prompts"]["intro"],
            "được phép lưu",
        )

    def test_save_all_preserves_locked_version_and_manages_other_versions(self):
        incoming = make_prompts_data()
        incoming["versions"]["default"]["prompts"]["intro"] = "không lưu"
        del incoming["versions"]["second"]
        incoming["versions"]["third"] = {
            "name": "Bộ mới",
            "project_url": PROJECT_URL,
            "default_voice_id": "",
            "prompts": {
                key: f"third-{key}" for key in main.PROMPT_FIELD_KEYS
            },
        }
        incoming["active_version"] = "third"

        with patch.object(
            main,
            "_get_locked_prompt_version",
            return_value="default",
        ):
            main.save_prompts(main.PromptsData(**incoming))

        saved = self.read_saved_data()
        self.assertEqual(
            saved["versions"]["default"]["prompts"]["intro"],
            "old-intro",
        )
        self.assertNotIn("second", saved["versions"])
        self.assertEqual(saved["versions"]["third"]["name"], "Bộ mới")

    def test_chatgpt_status_identifies_prompt_version_used_by_job(self):
        self.assertTrue(main._try_start_chatgpt_operation("video", "second"))
        try:
            status = main.get_chatgpt_status()
            self.assertTrue(status["busy"])
            self.assertEqual(status["operation"], "video")
            self.assertEqual(status["prompt_version"], "second")
        finally:
            main._finish_chatgpt_operation()


if __name__ == "__main__":
    unittest.main()
