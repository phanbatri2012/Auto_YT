import asyncio
import unittest
from unittest.mock import call, patch

from fastapi import BackgroundTasks

from auto_yt import main
from auto_yt.services.chatgpt_worker import (
    build_thumbnail_generation_prompt,
    extract_thumbnail_source_context,
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


if __name__ == "__main__":
    unittest.main()
