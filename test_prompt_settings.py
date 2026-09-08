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
        self.assertEqual(saved["versions"]["default"]["pipeline"], pipeline)
        self.assertEqual(
            saved["versions"]["second"]["pipeline"],
            before["versions"]["second"]["pipeline"],
        )

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
