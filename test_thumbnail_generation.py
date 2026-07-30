import asyncio
import unittest
from unittest.mock import patch

from fastapi import BackgroundTasks

from auto_yt import main


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
        generated_result = {
            "thumb_text": "Prompt mới có chữ.",
            "thumb_notext": "Prompt mới không chữ.",
            "image1_url": "/api/thumbnails/new_with_text.png",
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
                return_value=generated_result,
            ) as generate_thumbnails,
        ):
            response = asyncio.run(
                main.generate_thumbnails_endpoint(request, BackgroundTasks())
            )

        self.assertTrue(response["success"])
        generate_thumbnails.assert_called_once_with(
            SCRIPT,
            "https://chatgpt.com/c/test",
            "default",
            None,
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


if __name__ == "__main__":
    unittest.main()
