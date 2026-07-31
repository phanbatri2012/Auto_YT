import asyncio
import unittest
from unittest.mock import call, patch

from fastapi import BackgroundTasks

from auto_yt import main
from auto_yt.services.chatgpt_worker import (
    THUMBNAIL_RETRY_PROMPT,
    build_thumbnail_generation_prompt,
    ensure_expected_conversation_page,
    get_video_thumbnail_chat_url,
    is_thumbnail_generation_error_response,
    select_thumbnail_response_turn_number,
)


SCRIPT = """
### [THUMBNAIL CÓ CHỮ]
Prompt cũ có chữ.

[IMAGE_URL:/api/thumbnails/old_with_text.png]

### [THUMBNAIL KHÔNG CHỮ]
Prompt cũ không chữ.

[IMAGE_URL:/api/thumbnails/old_without_text.png]
""".strip()


class ThumbnailGenerationTests(unittest.TestCase):
    def test_both_type_generates_and_updates_both_thumbnails(self):
        generated_with_text = {
            "thumb_text": "Prompt mới có chữ.",
            "thumb_notext": None,
            "image1_url": "/api/thumbnails/new_with_text.png",
            "image2_url": "",
        }
        generated_without_text = {
            "thumb_text": None,
            "thumb_notext": "Prompt mới không chữ.",
            "image1_url": "",
            "image2_url": "/api/thumbnails/new_without_text.png",
        }
        request = main.GenerateThumbnailsRequest(
            script=SCRIPT,
            video_id=38,
            thumbnail_type="both",
        )

        with (
            patch.object(
                main.db,
                "get_video",
                return_value={
                    "generated_script": SCRIPT,
                    "chat_url": "https://chatgpt.com/c/test",
                    "prompt_version": "default",
                },
            ),
            patch.object(main.db, "update_script") as update_script,
            patch(
                "auto_yt.services.chatgpt_worker.generate_thumbnails_only",
                side_effect=(generated_with_text, generated_without_text),
            ) as generate_thumbnails,
        ):
            response = asyncio.run(
                main.generate_thumbnails_endpoint(request, BackgroundTasks())
            )

        self.assertTrue(response["success"])
        self.assertEqual(
            generate_thumbnails.call_args_list,
            [
                call(
                    SCRIPT,
                    "https://chatgpt.com/c/test",
                    "default",
                    "with_text",
                ),
                call(
                    SCRIPT,
                    "https://chatgpt.com/c/test",
                    "default",
                    "without_text",
                ),
            ],
        )
        update_script.assert_called_once()
        updated_script = update_script.call_args.args[1]
        self.assertIn(
            "[IMAGE_URL:/api/thumbnails/new_with_text.png]",
            updated_script,
        )
        self.assertIn(
            "[IMAGE_URL:/api/thumbnails/new_without_text.png]",
            updated_script,
        )
        self.assertNotIn("old_with_text.png", updated_script)
        self.assertNotIn("old_without_text.png", updated_script)

    def test_thumbnail_prompt_is_sent_without_changes(self):
        user_prompt = "PROMPT CỦA TÔI\nnegative_prompt tùy chỉnh"

        for thumbnail_type in ("with_text", "without_text"):
            with self.subTest(thumbnail_type=thumbnail_type):
                self.assertEqual(
                    build_thumbnail_generation_prompt(
                        user_prompt,
                        thumbnail_type,
                    ),
                    user_prompt,
                )

    def test_thumbnail_retry_prompt_matches_requested_command(self):
        self.assertEqual(
            THUMBNAIL_RETRY_PROMPT,
            "Sửa lại prompt sao cho không vi phạm. sau đó tạo lại thumbanil. "
            "chỉ cần xuất hình ảnh thumbnail.",
        )

    def test_both_type_keeps_old_images_when_first_image_is_missing(self):
        request = main.GenerateThumbnailsRequest(
            script=SCRIPT,
            video_id=38,
            thumbnail_type="both",
        )
        missing_image_result = {
            "thumb_text": "Something went wrong. Please try again.",
            "thumb_notext": None,
            "image1_url": "",
            "image2_url": "",
        }

        with (
            patch.object(
                main.db,
                "get_video",
                return_value={
                    "generated_script": SCRIPT,
                    "chat_url": "https://chatgpt.com/c/test",
                    "prompt_version": "default",
                },
            ),
            patch.object(main.db, "update_script") as update_script,
            patch(
                "auto_yt.services.chatgpt_worker.generate_thumbnails_only",
                return_value=missing_image_result,
            ) as generate_thumbnails,
        ):
            response = asyncio.run(
                main.generate_thumbnails_endpoint(request, BackgroundTasks())
            )

        self.assertFalse(response["success"])
        self.assertIn("Ảnh cũ được giữ nguyên", response["error"])
        generate_thumbnails.assert_called_once()
        update_script.assert_not_called()

    def test_content_policy_response_is_detected(self):
        response = (
            "We’re so sorry, but the prompt may violate our content policies. "
            "Please retry or edit your prompt."
        )

        self.assertTrue(is_thumbnail_generation_error_response(response))

    def test_thumbnail_reuses_video_conversation_url(self):
        chat_urls = (
            "https://chatgpt.com/c/conversation-id",
            "https://chatgpt.com/g/g-p-project-id/c/conversation-id",
        )

        for chat_url in chat_urls:
            with self.subTest(chat_url=chat_url):
                self.assertEqual(
                    get_video_thumbnail_chat_url(chat_url),
                    chat_url,
                )

    def test_thumbnail_does_not_create_chat_when_video_chat_is_missing(self):
        invalid_urls = (
            "",
            "https://chatgpt.com/",
            "https://chatgpt.com/g/g-p-project-id/project",
        )

        for chat_url in invalid_urls:
            with (
                self.subTest(chat_url=chat_url),
                self.assertRaisesRegex(
                    RuntimeError,
                    "valid ChatGPT conversation URL",
                ),
            ):
                get_video_thumbnail_chat_url(chat_url)

    def test_thumbnail_rejects_redirect_away_from_video_chat(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "video's conversation page",
        ):
            ensure_expected_conversation_page(
                "https://chatgpt.com/",
                "https://chatgpt.com/c/conversation-id",
            )

    def test_thumbnail_is_rejected_while_another_chatgpt_job_is_running(self):
        request = main.GenerateThumbnailsRequest(
            script=SCRIPT,
            video_id=38,
            thumbnail_type="both",
        )
        self.assertTrue(main._try_start_chatgpt_operation("video"))
        try:
            with patch(
                "auto_yt.services.chatgpt_worker.generate_thumbnails_only"
            ) as generate_thumbnails:
                response = asyncio.run(
                    main.generate_thumbnails_endpoint(
                        request,
                        BackgroundTasks(),
                    )
                )

            self.assertFalse(response["success"])
            self.assertEqual(response["error"], main.CHATGPT_BUSY_ERROR)
            self.assertEqual(
                main.get_chatgpt_status(),
                {"busy": True, "operation": "video"},
            )
            generate_thumbnails.assert_not_called()
        finally:
            main._finish_chatgpt_operation()

    def test_video_job_is_rejected_while_thumbnail_generation_is_running(self):
        self.assertTrue(main._try_start_chatgpt_operation("thumbnails"))
        try:
            response = main.process_video(
                main.VideoRequest(
                    url="https://www.youtube.com/watch?v=test",
                    prompt_version="default",
                )
            )
            job = main.get_job(response["job_id"])

            self.assertEqual(job["status"], "error")
            self.assertEqual(job["error"], main.CHATGPT_BUSY_ERROR)
            self.assertEqual(
                main.get_chatgpt_status(),
                {"busy": True, "operation": "thumbnails"},
            )
        finally:
            main._finish_chatgpt_operation()

    def test_thumbnail_selects_only_the_response_after_its_request(self):
        visible_turns = (
            (64, "assistant"),
            (65, "user"),
            (66, "assistant"),
            (67, "user"),
            (68, "assistant"),
            (69, "user"),
            (70, "assistant"),
        )

        self.assertEqual(
            select_thumbnail_response_turn_number(visible_turns, 67),
            68,
        )
        self.assertIsNone(
            select_thumbnail_response_turn_number(visible_turns[:4], 67)
        )


if __name__ == "__main__":
    unittest.main()
