import asyncio
import re
import unittest
from unittest.mock import MagicMock, patch

from auto_yt import main
from auto_yt.services.chatgpt_worker import (
    get_new_assistant_response,
    get_response_turn_baseline,
    get_visible_conversation_turns,
    is_valid_chapter_response,
    sanitize_chapter_response,
    select_reusable_chapter_response,
    wait_for_valid_chapter_response,
)


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
    def test_chapter_response_removes_attachment_and_explanatory_noise(self):
        response = """Đã tạo chapter theo yêu cầu.

Nội dung chính trong video:

00:00:00 - Mở đầu câu chuyện
00:04:17 - Bí mật đầu tiên xuất hiện
00:08:42 - Cao trào của gia đình

noi-dung-video.txt"""

        self.assertEqual(
            sanitize_chapter_response(response),
            "Nội dung chính trong video:\n\n"
            "00:00:00 - Mở đầu câu chuyện\n"
            "00:04:17 - Bí mật đầu tiên xuất hiện\n"
            "00:08:42 - Cao trào của gia đình",
        )

    def test_ignores_hidden_stale_conversation_turns(self):
        page = MagicMock()
        turns = MagicMock()
        hidden_turn = MagicMock()
        visible_user_turn = MagicMock()
        visible_assistant_turn = MagicMock()
        page.locator.return_value = turns
        turns.count.return_value = 3
        turns.nth.side_effect = (
            hidden_turn,
            visible_user_turn,
            visible_assistant_turn,
        )

        hidden_turn.is_visible.return_value = False
        visible_user_turn.is_visible.return_value = True
        visible_assistant_turn.is_visible.return_value = True
        visible_user_turn.get_attribute.side_effect = lambda name: {
            "data-testid": "conversation-turn-1",
            "data-turn": "user",
        }.get(name)
        visible_assistant_turn.get_attribute.side_effect = lambda name: {
            "data-testid": "conversation-turn-2",
            "data-turn": "assistant",
        }.get(name)

        self.assertEqual(
            get_visible_conversation_turns(page),
            [(1, "user"), (2, "assistant")],
        )
        hidden_turn.get_attribute.assert_not_called()

    def test_resets_turn_baseline_when_chat_url_changes(self):
        self.assertEqual(
            get_response_turn_baseline(
                "https://chatgpt.com/g/project-id/project",
                "https://chatgpt.com/g/project-id/c/new-chat-id",
                16,
            ),
            -1,
        )
        self.assertEqual(
            get_response_turn_baseline(
                "https://chatgpt.com/c/existing-chat",
                "https://chatgpt.com/c/existing-chat",
                16,
            ),
            16,
        )

    def test_reads_new_assistant_response_by_conversation_turn(self):
        page = MagicMock()
        turn = MagicMock()
        assistant_nodes = MagicMock()
        message = MagicMock()
        markdown_locator = MagicMock()
        markdown = MagicMock()

        page.locator.return_value = turn
        turn.locator.return_value = assistant_nodes
        assistant_nodes.count.return_value = 1
        assistant_nodes.last = message
        message.locator.return_value = markdown_locator
        markdown_locator.first = markdown
        markdown.count.return_value = 1
        markdown.inner_text.return_value = "00:00 - Chapter mới"

        with patch(
            "auto_yt.services.chatgpt_worker.get_visible_conversation_turns",
            return_value=[
                (10, "assistant"),
                (11, "user"),
                (12, "assistant"),
            ],
        ):
            response = get_new_assistant_response(page, 10)

        self.assertEqual(response, "00:00 - Chapter mới")
        page.locator.assert_called_once_with(
            '[data-testid="conversation-turn-12"]'
        )

    def test_waits_for_late_valid_chapter_without_sending_prompt(self):
        valid_response = (
            "00:00 - Mở đầu\n"
            "05:30 - Phân tích\n"
            "12:00 - Kết luận"
        )

        with (
            patch(
                "auto_yt.services.chatgpt_worker.time.time",
                side_effect=(0, 1),
            ),
            patch("auto_yt.services.chatgpt_worker.time.sleep"),
            patch(
                "auto_yt.services.chatgpt_worker.get_new_assistant_response",
                return_value=valid_response,
            ) as get_response,
            patch(
                "auto_yt.services.chatgpt_worker.send_prompt"
            ) as send_prompt,
        ):
            response = wait_for_valid_chapter_response(
                MagicMock(),
                previous_assistant_turn=20,
                initial_response="",
                timeout=300,
            )

        self.assertEqual(response, sanitize_chapter_response(valid_response))
        get_response.assert_called_once()
        send_prompt.assert_not_called()

    def test_reuses_late_completed_chapters_from_same_conversation(self):
        chapter_response = (
            "Nội dung chính trong video:\n"
            "00:00:00 - Mở đầu câu chuyện\n"
            "00:05:30 - Bí mật được hé lộ\n"
            "00:12:10 - Bài học cuối cùng"
        )
        turns = [
            ("assistant", "Nội dung kịch bản"),
            ("user", "Hãy tạo chapter chuẩn SEO cho video"),
            ("assistant", chapter_response),
            ("user", "Hãy tạo chapter chuẩn SEO cho video"),
            ("assistant", "00:00 - Phản hồi trùng chưa hoàn tất"),
        ]

        self.assertTrue(is_valid_chapter_response(chapter_response))
        self.assertEqual(
            select_reusable_chapter_response(turns),
            sanitize_chapter_response(chapter_response),
        )

    def test_does_not_reuse_unrelated_or_incomplete_response(self):
        unrelated_turns = [
            ("user", "Hãy tạo lại tiêu đề"),
            ("assistant", "00:00 - Một dòng không liên quan"),
        ]
        incomplete_chapter_turns = [
            ("user", "Hãy tạo chapter"),
            ("assistant", "00:00 - Mở đầu\n05:00 - Phần tiếp theo"),
        ]

        self.assertEqual(select_reusable_chapter_response(unrelated_turns), "")
        self.assertEqual(
            select_reusable_chapter_response(incomplete_chapter_turns),
            "",
        )

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
        self.assertEqual(response["video_id"], 46)
        generate_chapters.assert_called_once_with(
            SCRIPT,
            "https://chatgpt.com/c/test",
            "default",
            False,
        )
        updated_script = update_script.call_args.args[1]
        self.assertIn("00:00 - Chapter mới", updated_script)
        self.assertNotIn("00:00 - Chapter cũ", updated_script)
        self.assertIn(
            "https://api.genmax.io/audio/existing.mp3",
            updated_script,
        )

    def test_regeneration_merges_chapters_into_latest_script(self):
        initial_script = SCRIPT.rsplit("\n\n### [AUDIO]", 1)[0]
        initial_video = {
            "generated_script": initial_script,
            "chat_url": "https://chatgpt.com/c/test",
            "prompt_version": "default",
        }
        latest_video = {**initial_video, "generated_script": SCRIPT}
        request = main.GenerateChaptersRequest(video_id=48)

        with (
            patch.object(
                main.db,
                "get_video",
                side_effect=[initial_video, latest_video],
            ),
            patch.object(
                main.db,
                "update_script",
                return_value=True,
            ) as update_script,
            patch(
                "auto_yt.services.chatgpt_worker.generate_chapters_only",
                return_value=(
                    "00:00 - Chapter mới\n"
                    "05:30 - Phân tích\n"
                    "12:00 - Kết luận"
                ),
            ),
        ):
            response = asyncio.run(main.generate_chapters_endpoint(request))

        self.assertTrue(response["success"])
        updated_script = update_script.call_args.args[1]
        self.assertIn("00:00 - Chapter mới", updated_script)
        self.assertIn(
            "https://api.genmax.io/audio/existing.mp3",
            updated_script,
        )

    def test_missing_chapters_reuses_completed_response_before_resending(self):
        script_without_chapters = re.sub(
            r"(### \[CHAPTERS\]\n).*?(?=\n### \[)",
            r"\1",
            SCRIPT,
            flags=re.DOTALL,
        )
        video = {
            "generated_script": script_without_chapters,
            "chat_url": "https://chatgpt.com/c/test",
            "prompt_version": "default",
        }
        request = main.GenerateChaptersRequest(video_id=47)

        with (
            patch.object(main.db, "get_video", return_value=video),
            patch.object(main.db, "update_script", return_value=True),
            patch(
                "auto_yt.services.chatgpt_worker.generate_chapters_only",
                return_value=(
                    "00:00 - Mở đầu\n"
                    "05:30 - Phân tích\n"
                    "12:00 - Kết luận"
                ),
            ) as generate_chapters,
        ):
            response = asyncio.run(main.generate_chapters_endpoint(request))

        self.assertTrue(response["success"])
        self.assertEqual(response["video_id"], 47)
        generate_chapters.assert_called_once_with(
            script_without_chapters,
            "https://chatgpt.com/c/test",
            "default",
            True,
        )


if __name__ == "__main__":
    unittest.main()
