import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt.services import chatgpt_worker
from auto_yt.services import chatgpt_projects


PROJECT_URL = (
    "https://chatgpt.com/g/"
    "g-p-6a1f9204f2d88191b39b64eb7f2dbb97-dd-vn2-phan-tich/project"
)


class ChatGPTProjectTests(unittest.TestCase):
    def test_uses_configured_project_url(self):
        with (
            patch.object(
                chatgpt_projects,
                "PROMPTS_PATH",
                Path("missing-prompts.json"),
            ),
            patch.dict(
                os.environ,
                {chatgpt_worker.CHATGPT_PROJECT_URL_ENV: PROJECT_URL},
            ),
        ):
            self.assertEqual(
                chatgpt_worker.get_chatgpt_project_url(),
                PROJECT_URL,
            )

    def test_rejects_non_project_url(self):
        with (
            patch.object(
                chatgpt_projects,
                "PROMPTS_PATH",
                Path("missing-prompts.json"),
            ),
            patch.dict(
                os.environ,
                {chatgpt_worker.CHATGPT_PROJECT_URL_ENV: "https://chatgpt.com"},
            ),
            self.assertRaisesRegex(ValueError, "URL ChatGPT Project"),
        ):
            chatgpt_worker.get_chatgpt_project_url()

    def test_uses_project_configured_for_prompt_version(self):
        second_project_url = (
            "https://chatgpt.com/g/g-p-second-project-noi-dung/project"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            prompts_path = Path(temporary_directory) / "prompts.json"
            prompts_path.write_text(
                json.dumps({
                    "active_version": "default",
                    "versions": {
                        "default": {
                            "name": "Default",
                            "project_url": PROJECT_URL,
                            "prompts": {},
                        },
                        "second": {
                            "name": "Second",
                            "project_url": second_project_url,
                            "prompts": {},
                        },
                    },
                }),
                encoding="utf-8",
            )
            with (
                patch.object(chatgpt_projects, "PROMPTS_PATH", prompts_path),
                patch.dict(os.environ, {}, clear=True),
            ):
                self.assertEqual(
                    chatgpt_worker.get_chatgpt_project_url("second"),
                    second_project_url,
                )

    def test_rejects_invalid_project_in_prompt_configuration(self):
        with self.assertRaisesRegex(ValueError, "URL ChatGPT Project"):
            chatgpt_projects.validate_prompt_projects({
                "active_version": "default",
                "versions": {
                    "default": {
                        "name": "Default",
                        "project_url": "https://chatgpt.com/",
                        "prompts": {},
                    },
                },
            })

    def test_project_redirect_is_rejected_before_prompt(self):
        with self.assertRaisesRegex(RuntimeError, "No prompt was sent"):
            chatgpt_worker.ensure_expected_project_page(
                "https://chatgpt.com/",
                PROJECT_URL,
            )

    def test_accepts_standard_and_project_conversation_urls(self):
        self.assertTrue(
            chatgpt_worker.is_chatgpt_conversation_url(
                "https://chatgpt.com/c/conversation-id"
            )
        )
        self.assertTrue(
            chatgpt_worker.is_chatgpt_conversation_url(
                "https://chatgpt.com/g/g-p-project-id/c/conversation-id"
            )
        )
        self.assertFalse(
            chatgpt_worker.is_chatgpt_conversation_url(PROJECT_URL)
        )


if __name__ == "__main__":
    unittest.main()
