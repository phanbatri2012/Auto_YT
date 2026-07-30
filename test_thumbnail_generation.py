import asyncio
import unittest
from unittest.mock import call, patch

from fastapi import BackgroundTasks

from auto_yt import main
from auto_yt.services.chatgpt_worker import (
    build_thumbnail_generation_prompt,
    ensure_expected_conversation_page,
    extract_thumbnail_source_context,
    get_video_thumbnail_chat_url,
    is_thumbnail_generation_error_response,
    sanitize_thumbnail_source_context,
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

    def test_thumbnail_prompt_is_anchored_to_current_video(self):
        script = """
### [INTRO]
Phần mở đầu có nhắc đến nhiều câu chuyện.

### [BODY]
Câu chuyện đầu tiên: người vợ bắt gặp chồng ở nhà nghỉ.

Tiếp theo là câu chuyện về que thử thai và tiền.
""".strip()

        prompt = build_thumbnail_generation_prompt(
            "PROMPT GỐC",
            script,
            "without_text",
        )

        self.assertIn("PROMPT GỐC", prompt)
        self.assertIn("người vợ bắt gặp chồng ở nhà nghỉ", prompt)
        self.assertNotIn("que thử thai", prompt)
        self.assertIn("chỉ tạo thumbnail cho câu chuyện đầu tiên", prompt)
        self.assertIn("ZERO TEXT, NO WORDS, NO LETTERS", prompt)

    def test_thumbnail_source_uses_first_body_story(self):
        script = """
### [INTRO]
Tóm tắt cả câu chuyện đầu tiên và câu chuyện thứ hai.

### [BODY]
Nội dung câu chuyện đầu tiên.

Câu chuyện tiếp theo nói về tài sản.
""".strip()

        source_context = extract_thumbnail_source_context(script)

        self.assertEqual(source_context, "Nội dung câu chuyện đầu tiên.")

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

    def test_thumbnail_context_softens_sensitive_language(self):
        sensitive_cases = (
            ("ngủ với người cũ", "phản bội"),
            ("muốn ngủ cùng người ấy", "vượt giới hạn"),
            ("tiếp tục quan hệ thể xác", "vượt giới hạn hôn nhân"),
            ("qua đêm với đồng nghiệp", "bí mật gặp gỡ"),
            ("nhắc đến chuyện giường chiếu", "đời sống hôn nhân"),
            ("mô tả chuyện ấy", "việc vượt giới hạn"),
            ("hình ảnh gợi dục", "không phù hợp"),
        )

        for source_context, expected_text in sensitive_cases:
            with self.subTest(source_context=source_context):
                sanitized_context = sanitize_thumbnail_source_context(
                    source_context
                )
                self.assertNotEqual(sanitized_context, source_context)
                self.assertIn(expected_text, sanitized_context)

    def test_content_policy_response_is_not_used_as_draw_prompt(self):
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


if __name__ == "__main__":
    unittest.main()
