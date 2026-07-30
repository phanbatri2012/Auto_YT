import asyncio
import unittest
from unittest.mock import patch

from auto_yt import main


SCRIPT = """
### [INTRO]
Nội dung mở đầu.

### [CHAPTERS]
Nội dung chính trong video:
00:00 - Chapter cũ

### [AUDIO]
https://api.genmax.io/audio/existing.mp3
""".strip()


class ChapterGenerationTests(unittest.TestCase):
    def test_regeneration_replaces_chapters_and_preserves_audio(self):
        video = {
            "generated_script": SCRIPT,
            "chat_url": "https://chatgpt.com/c/test",
            "prompt_version": "default",
        }
        request = main.GenerateChaptersRequest(video_id=46)

        with (
            patch.object(main.db, "get_video", return_value=video),
            patch.object(main.db, "update_script", return_value=True) as update_script,
            patch(
                "auto_yt.services.chatgpt_worker.generate_chapters_only",
                return_value=(
                    "Nội dung chính trong video:\n"
                    "00:00 - Chapter mới\n"
                    "05:30 - Phân tích câu chuyện"
                ),
            ) as generate_chapters,
        ):
            response = asyncio.run(main.generate_chapters_endpoint(request))

        self.assertTrue(response["success"])
        generate_chapters.assert_called_once_with(
            SCRIPT,
            "https://chatgpt.com/c/test",
            "default",
        )
        updated_script = update_script.call_args.args[1]
        self.assertIn("00:00 - Chapter mới", updated_script)
        self.assertNotIn("00:00 - Chapter cũ", updated_script)
        self.assertIn(
            "https://api.genmax.io/audio/existing.mp3",
            updated_script,
        )


if __name__ == "__main__":
    unittest.main()
