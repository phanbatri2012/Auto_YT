import asyncio
import unittest
from unittest.mock import patch

from auto_yt import main
from auto_yt.services.chatgpt_worker import (
    build_metadata_generation_prompt,
    request_complete_metadata,
    validate_metadata_response,
)


SCRIPT = """
### [INTRO]
Nội dung mở đầu.

### [METADATA & QUIZ]
TIÊU ĐỀ: Tiêu đề cũ

SLUG: slug-cu

MÔ TẢ VIDEO: Mô tả cũ.

CÂU HỎI: Câu hỏi cũ?

### [CHAPTERS]
00:00 - Mở đầu

### [AUDIO]
https://api.genmax.io/audio/existing.mp3
""".strip()

NEW_METADATA = """
TIÊU ĐỀ: TIÊU ĐỀ MỚI

URL SLUG: tieu-de-moi

MÔ TẢ VIDEO: Mô tả mới.

HASHTAG: #NgoaiTinh #DanhGhen #HonNhanGiaDinh

BÌNH LUẬN GHIM: Bình luận mới.

CÂU HỎI: Câu hỏi mới?
A. Một
B. Hai
C. Ba
D. Bốn
""".strip()


class MetadataGenerationTests(unittest.TestCase):
    def test_prompt_reuses_the_full_configured_metadata_prompt(self):
        metadata_prompt = (
            "Tạo tiêu đề, URL slug, mô tả, hashtag, bình luận ghim và quiz."
        )

        prompt = build_metadata_generation_prompt(metadata_prompt)

        self.assertIn(metadata_prompt, prompt)
        self.assertIn("đầy đủ toàn bộ phần metadata", prompt)
        self.assertIn("TIÊU ĐỀ, URL SLUG, MÔ TẢ", prompt)

    def test_response_validation_rejects_incomplete_metadata(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "missing MÔ TẢ, HASHTAG, BÌNH LUẬN GHIM, QUIZ",
        ):
            validate_metadata_response(
                "TIÊU ĐỀ: Tiêu đề mới\nURL SLUG: tieu-de-moi"
            )

    def test_response_validation_accepts_labeled_hashtags(self):
        self.assertEqual(
            validate_metadata_response(NEW_METADATA),
            NEW_METADATA,
        )

    def test_response_validation_accepts_quiz_started_with_theo_cac_ban(self):
        metadata = NEW_METADATA.replace(
            "CÂU HỎI: Câu hỏi mới?",
            "Theo các bạn, câu trả lời nào phù hợp?",
        )

        self.assertEqual(validate_metadata_response(metadata), metadata)

    def test_incomplete_response_is_retried_once_in_the_same_chat(self):
        incomplete_metadata = NEW_METADATA.replace(
            "CÂU HỎI: Câu hỏi mới?\nA. Một\nB. Hai\nC. Ba\nD. Bốn",
            "",
        )
        page = object()

        with patch(
            "auto_yt.services.chatgpt_worker.send_prompt",
            side_effect=[incomplete_metadata, NEW_METADATA],
        ) as send_prompt:
            result = request_complete_metadata(page, "metadata prompt")

        self.assertEqual(result, NEW_METADATA)
        self.assertEqual(send_prompt.call_count, 2)
        self.assertIs(send_prompt.call_args_list[0].args[0], page)
        self.assertIs(send_prompt.call_args_list[1].args[0], page)
        self.assertIn("còn thiếu: QUIZ", send_prompt.call_args_list[1].args[1])

    def test_endpoint_replaces_metadata_and_preserves_other_sections(self):
        video = {
            "generated_script": SCRIPT,
            "chat_url": "https://chatgpt.com/c/video-chat",
            "prompt_version": "default",
        }
        request = main.GenerateMetadataRequest(video_id=51)

        with (
            patch.object(main.db, "get_video", return_value=video),
            patch.object(
                main.db,
                "update_script",
                return_value=True,
            ) as update_script,
            patch(
                "auto_yt.services.chatgpt_worker.generate_metadata_only",
                return_value=NEW_METADATA,
            ) as generate_metadata,
        ):
            response = asyncio.run(main.generate_metadata_endpoint(request))

        self.assertTrue(response["success"])
        self.assertEqual(response["video_id"], 51)
        generate_metadata.assert_called_once_with(
            "https://chatgpt.com/c/video-chat",
            "default",
        )
        updated_script = update_script.call_args.args[1]
        self.assertIn(NEW_METADATA, updated_script)
        self.assertNotIn("Tiêu đề cũ", updated_script)
        self.assertNotIn("Mô tả cũ", updated_script)
        self.assertNotIn("Câu hỏi cũ", updated_script)
        self.assertIn("00:00 - Mở đầu", updated_script)
        self.assertIn(
            "https://api.genmax.io/audio/existing.mp3",
            updated_script,
        )

    def test_endpoint_is_rejected_while_chatgpt_is_busy(self):
        request = main.GenerateMetadataRequest(video_id=51)
        video = {
            "generated_script": SCRIPT,
            "chat_url": "https://chatgpt.com/c/video-chat",
            "prompt_version": "default",
        }

        self.assertTrue(main._try_start_chatgpt_operation("video"))
        try:
            with (
                patch.object(main.db, "get_video", return_value=video),
                patch(
                    "auto_yt.services.chatgpt_worker.generate_metadata_only"
                ) as generate_metadata,
            ):
                response = asyncio.run(
                    main.generate_metadata_endpoint(request)
                )

            self.assertFalse(response["success"])
            self.assertEqual(response["error"], main.CHATGPT_BUSY_ERROR)
            generate_metadata.assert_not_called()
        finally:
            main._finish_chatgpt_operation()


if __name__ == "__main__":
    unittest.main()
