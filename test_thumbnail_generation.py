import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from fastapi import BackgroundTasks
from playwright.sync_api import Error as PlaywrightError

from auto_yt import main
from auto_yt.services.chatgpt_worker import (
    CHATGPT_RESPONSE_TIMEOUT_SECONDS,
    ChatGPTGenerationTimeoutError,
    THUMBNAIL_REGENERATE_PROMPT,
    THUMBNAIL_REPAIR_PROMPT,
    build_thumbnail_generation_prompt,
    ensure_prompt_editor_integrity,
    ensure_expected_conversation_page,
    get_new_assistant_response,
    get_video_thumbnail_chat_url,
    is_thumbnail_generation_error_response,
    prompt_text_matches,
    retry_thumbnail_generation,
    send_prompt,
    send_thumbnail_prompt,
    select_thumbnail_response_turn_number,
    validate_prompt_text,
    wait_for_assistant_response,
    wait_for_thumbnail_image,
    wait_for_thumbnail_images,
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
    def test_unicode_prompt_validation_accepts_vietnamese_text(self):
        prompt = "Hãy viết lại nội dung đầy đủ. Vì sao nhân vật rời đi?"

        validate_prompt_text(prompt)
        self.assertTrue(
            prompt_text_matches(
                prompt,
                "Hãy viết lại nội dung đầy đủ.\nVì sao nhân vật rời đi?",
            )
        )

    def test_unicode_prompt_validation_rejects_corrupted_words(self):
        for prompt in (
            "H?y vi?t l?i ph?n BODY.",
            "??y l? b?n BODY thay th?.",
            "Nội dung bị lỗi \ufffd trong prompt.",
        ):
            with self.subTest(prompt=prompt), self.assertRaisesRegex(
                ValueError,
                "corrupted Unicode",
            ):
                validate_prompt_text(prompt)

    def test_corrupted_prompt_is_blocked_before_browser_input(self):
        page = MagicMock()

        with self.assertRaisesRegex(ValueError, "No prompt was sent"):
            send_prompt(page, "H?y vi?t l?i ph?n BODY.")

        page.locator.assert_not_called()

    def test_corrupted_editor_text_is_reinserted_before_send(self):
        page = MagicMock()
        prompt_textarea = MagicMock()
        prompt_textarea.inner_text.side_effect = (
            "H?y vi?t l?i ph?n BODY.",
            "Hãy viết lại phần BODY.",
        )

        with (
            patch(
                "auto_yt.services.chatgpt_worker.replace_prompt_text_with_javascript"
            ) as replace_prompt,
            patch("auto_yt.services.chatgpt_worker.time.sleep"),
        ):
            ensure_prompt_editor_integrity(
                page,
                prompt_textarea,
                "Hãy viết lại phần BODY.",
            )

        replace_prompt.assert_called_once_with(
            page,
            "Hãy viết lại phần BODY.",
        )

    def test_prompt_is_not_sent_if_editor_remains_corrupted(self):
        page = MagicMock()
        prompt_textarea = MagicMock()
        prompt_textarea.inner_text.return_value = "H?y vi?t l?i ph?n BODY."

        with (
            patch(
                "auto_yt.services.chatgpt_worker.replace_prompt_text_with_javascript"
            ),
            patch("auto_yt.services.chatgpt_worker.time.sleep"),
            self.assertRaisesRegex(RuntimeError, "No prompt was sent"),
        ):
            ensure_prompt_editor_integrity(
                page,
                prompt_textarea,
                "Hãy viết lại phần BODY.",
            )

    def test_both_type_generates_and_updates_both_thumbnails(self):
        generated_with_text = {
            "thumb_text": "Prompt mới có chữ.",
            "thumb_notext": None,
            "image1_url": "/api/thumbnails/new_with_text.png",
            "image1_urls": [
                "/api/thumbnails/new_with_text.png",
                "/api/thumbnails/new_with_text_2.png",
            ],
            "image2_url": "",
        }
        generated_without_text = {
            "thumb_text": None,
            "thumb_notext": "Prompt mới không chữ.",
            "image1_url": "",
            "image2_url": "/api/thumbnails/new_without_text.png",
            "image2_urls": [
                "/api/thumbnails/new_without_text.png",
                "/api/thumbnails/new_without_text_2.png",
            ],
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
            "[IMAGE_URL:/api/thumbnails/new_with_text_2.png]",
            updated_script,
        )
        self.assertIn(
            "[IMAGE_URL:/api/thumbnails/new_without_text.png]",
            updated_script,
        )
        self.assertIn(
            "[IMAGE_URL:/api/thumbnails/new_without_text_2.png]",
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

    def test_thumbnail_retry_prompts_match_requested_commands(self):
        self.assertEqual(
            THUMBNAIL_REPAIR_PROMPT,
            "hãy chỉ ra điểm vi phạm prompt của tôi. sau đó sửa prompt  sao cho không vi phạm nữa.",
        )
        self.assertEqual(
            THUMBNAIL_REGENERATE_PROMPT,
            "Tạo ảnh theo prompt vừa được sửa ở ngay trên. Lưu ý: chỉ cần xuất ảnh của prompt mới sửa",
        )

    def test_thumbnail_retry_waits_for_repair_then_requests_image(self):
        page = MagicMock()
        download_image = MagicMock()
        call_order = []

        with (
            patch(
                "auto_yt.services.chatgpt_worker.send_prompt",
                side_effect=lambda *args: call_order.append(("repair", args[1])),
            ),
            patch(
                "auto_yt.services.chatgpt_worker.send_thumbnail_prompt",
                side_effect=lambda *args: (
                    call_order.append(("generate", args[1]))
                    or ("", ["/api/thumbnails/repaired.png"])
                ),
            ),
        ):
            response_text, image_urls = retry_thumbnail_generation(
                page,
                download_image,
            )

        self.assertEqual(response_text, "")
        self.assertEqual(image_urls, ["/api/thumbnails/repaired.png"])
        self.assertEqual(
            call_order,
            [
                ("repair", THUMBNAIL_REPAIR_PROMPT),
                ("generate", THUMBNAIL_REGENERATE_PROMPT),
            ],
        )

    def test_thumbnail_image_command_is_not_sent_if_repair_fails(self):
        page = MagicMock()
        with (
            patch(
                "auto_yt.services.chatgpt_worker.send_prompt",
                side_effect=RuntimeError("repair failed"),
            ),
            patch(
                "auto_yt.services.chatgpt_worker.send_thumbnail_prompt",
            ) as generate_image,
            self.assertRaisesRegex(RuntimeError, "repair failed"),
        ):
            retry_thumbnail_generation(page, MagicMock())

        generate_image.assert_not_called()

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

    def test_empty_thumbnail_response_waits_for_rendered_image(self):
        page = MagicMock()
        download_image = MagicMock()

        with (
            patch(
                "auto_yt.services.chatgpt_worker.get_latest_conversation_turn",
                return_value=67,
            ),
            patch(
                "auto_yt.services.chatgpt_worker.send_prompt",
                return_value="",
            ),
            patch(
                "auto_yt.services.chatgpt_worker.wait_for_new_user_turn",
                return_value=68,
            ),
            patch(
                "auto_yt.services.chatgpt_worker.wait_for_thumbnail_images",
                return_value=["/api/thumbnails/rendered.png"],
            ) as wait_for_images,
        ):
            response_text, image_urls = send_thumbnail_prompt(
                page,
                "thumbnail prompt",
                download_image,
            )

        self.assertEqual(response_text, "")
        self.assertEqual(image_urls, ["/api/thumbnails/rendered.png"])
        wait_for_images.assert_called_once_with(page, 68, download_image)

    def test_two_unique_thumbnail_images_are_downloaded_from_same_response(self):
        page = MagicMock()
        turn = MagicMock()
        images = MagicMock()
        stop_button = MagicMock()
        source_urls = [
            "https://chatgpt.com/backend-api/estuary/content?id=file_first&sig=large",
            "https://chatgpt.com/backend-api/estuary/content?id=file_first&sig=small",
            "https://chatgpt.com/backend-api/estuary/content?id=file_second&sig=small",
        ]
        turn.count.return_value = 1
        turn.locator.return_value = images
        images.evaluate_all.return_value = [
            {"src": source_url, "ready": True}
            for source_url in source_urls
        ]
        stop_button.count.return_value = 0
        page.locator.side_effect = lambda selector: (
            stop_button if selector == '[data-testid="stop-button"]' else turn
        )
        download_image = MagicMock(
            side_effect=(
                "/api/thumbnails/first.png",
                "/api/thumbnails/second.png",
            )
        )

        with (
            patch(
                "auto_yt.services.chatgpt_worker.get_visible_conversation_turns",
                return_value=[(67, "user"), (68, "assistant")],
            ),
            patch(
                "auto_yt.services.chatgpt_worker.time.time",
                side_effect=(0, 1),
            ),
        ):
            image_urls = wait_for_thumbnail_images(page, 67, download_image)

        self.assertEqual(
            image_urls,
            [
                "/api/thumbnails/first.png",
                "/api/thumbnails/second.png",
            ],
        )
        self.assertEqual(
            download_image.call_args_list,
            [call(source_urls[0]), call(source_urls[2])],
        )

    def test_thumbnail_image_waits_until_generation_stops(self):
        page = MagicMock()
        turn = MagicMock()
        images = MagicMock()
        stop_button = MagicMock()
        download_image = MagicMock(return_value="/api/thumbnails/new.png")

        turn.count.return_value = 1
        turn.locator.return_value = images
        images.evaluate_all.return_value = [{
            "src": "https://chatgpt.com/backend-api/estuary/image",
            "ready": True,
        }]
        stop_button.count.side_effect = (1, 0)
        page.locator.side_effect = lambda selector: (
            stop_button if selector == '[data-testid="stop-button"]' else turn
        )

        with (
            patch(
                "auto_yt.services.chatgpt_worker.get_visible_conversation_turns",
                return_value=[(67, "user"), (68, "assistant")],
            ),
            patch(
                "auto_yt.services.chatgpt_worker.time.time",
                side_effect=(0, 1, 2),
            ),
            patch("auto_yt.services.chatgpt_worker.time.sleep"),
        ):
            image_url = wait_for_thumbnail_image(page, 67, download_image)

        self.assertEqual(image_url, "/api/thumbnails/new.png")
        self.assertEqual(stop_button.count.call_count, 2)
        download_image.assert_called_once_with(
            "https://chatgpt.com/backend-api/estuary/image"
        )

    def test_thumbnail_image_snapshot_retries_after_dom_rerender(self):
        page = MagicMock()
        turn = MagicMock()
        images = MagicMock()
        stop_button = MagicMock()
        source_url = "https://chatgpt.com/backend-api/estuary/content?id=file_new"
        download_image = MagicMock(return_value="/api/thumbnails/new.png")

        turn.count.return_value = 1
        turn.locator.return_value = images
        images.evaluate_all.side_effect = (
            PlaywrightError("execution context changed"),
            [{"src": source_url, "ready": True}],
        )
        stop_button.count.return_value = 0
        page.locator.side_effect = lambda selector: (
            stop_button if selector == '[data-testid="stop-button"]' else turn
        )

        with (
            patch(
                "auto_yt.services.chatgpt_worker.get_visible_conversation_turns",
                return_value=[(67, "user"), (68, "assistant")],
            ),
            patch(
                "auto_yt.services.chatgpt_worker.time.time",
                side_effect=(0, 1, 2),
            ),
            patch("auto_yt.services.chatgpt_worker.time.sleep"),
        ):
            image_urls = wait_for_thumbnail_images(page, 67, download_image)

        self.assertEqual(image_urls, ["/api/thumbnails/new.png"])
        self.assertEqual(images.evaluate_all.call_count, 2)
        download_image.assert_called_once_with(source_url)

    def test_next_prompt_is_blocked_while_generation_is_still_running(self):
        page = MagicMock()
        page.url = "https://chatgpt.com/c/test-conversation"
        prompt_locator = MagicMock()
        prompt_textarea = MagicMock()
        send_locator = MagicMock()
        send_button = MagicMock()
        prompt_locator.first = prompt_textarea
        send_locator.first = send_button
        page.locator.side_effect = lambda selector: (
            prompt_locator if selector == "#prompt-textarea" else send_locator
        )
        page.wait_for_function.side_effect = (None, None, None)

        with (
            patch("auto_yt.services.chatgpt_worker.time.sleep"),
            patch(
                "auto_yt.services.chatgpt_worker.get_latest_conversation_turn",
                return_value=42,
            ),
            patch(
                "auto_yt.services.chatgpt_worker.wait_for_conversation_history"
            ) as wait_for_history,
            patch(
                "auto_yt.services.chatgpt_worker.wait_for_assistant_response",
                side_effect=ChatGPTGenerationTimeoutError(
                    "generation is still running. No next prompt was sent."
                ),
            ) as wait_for_response,
            self.assertRaisesRegex(Exception, "No next prompt was sent"),
        ):
            send_prompt(page, "next prompt")

        wait_for_history.assert_called_once_with(page)
        wait_for_response.assert_called_once()

    def test_empty_text_response_stops_the_workflow(self):
        page = MagicMock()
        page.url = "https://chatgpt.com/c/test-conversation"
        prompt_locator = MagicMock()
        prompt_textarea = MagicMock()
        send_locator = MagicMock()
        send_button = MagicMock()
        prompt_locator.first = prompt_textarea
        send_locator.first = send_button
        page.locator.side_effect = lambda selector: (
            prompt_locator if selector == "#prompt-textarea" else send_locator
        )

        with (
            patch(
                "auto_yt.services.chatgpt_worker.get_latest_conversation_turn",
                return_value=10,
            ),
            patch(
                "auto_yt.services.chatgpt_worker.wait_for_assistant_response",
                side_effect=RuntimeError("ChatGPT returned no readable text"),
            ),
            patch(
                "auto_yt.services.chatgpt_worker.wait_for_conversation_history"
            ),
            self.assertRaisesRegex(RuntimeError, "no readable text"),
        ):
            send_prompt(page, "text prompt")

    def test_image_prompt_allows_an_empty_text_response(self):
        page = MagicMock()
        page.url = "https://chatgpt.com/c/test-conversation"
        prompt_locator = MagicMock()
        prompt_textarea = MagicMock()
        send_locator = MagicMock()
        send_button = MagicMock()
        prompt_locator.first = prompt_textarea
        send_locator.first = send_button
        page.locator.side_effect = lambda selector: (
            prompt_locator if selector == "#prompt-textarea" else send_locator
        )

        with (
            patch(
                "auto_yt.services.chatgpt_worker.get_latest_conversation_turn",
                return_value=10,
            ),
            patch(
                "auto_yt.services.chatgpt_worker.wait_for_assistant_response",
                return_value="",
            ),
            patch(
                "auto_yt.services.chatgpt_worker.wait_for_conversation_history"
            ),
        ):
            response = send_prompt(
                page,
                "image prompt",
                allow_empty_response=True,
            )

        self.assertEqual(response, "")

    def test_response_wait_handles_a_late_reply_without_a_stop_button(self):
        page = MagicMock()
        with (
            patch(
                "auto_yt.services.chatgpt_worker.is_chatgpt_generation_active",
                return_value=False,
            ),
            patch(
                "auto_yt.services.chatgpt_worker.get_new_assistant_response",
                side_effect=("", "", "[PHAN]\nDàn ý", "[PHAN]\nDàn ý"),
            ),
            patch(
                "auto_yt.services.chatgpt_worker.time.monotonic",
                side_effect=(0, 0.5, 1, 2, 7.1),
            ),
            patch("auto_yt.services.chatgpt_worker.time.sleep"),
        ):
            response = wait_for_assistant_response(page, 2, timeout=20)

        self.assertEqual(response, "[PHAN]\nDàn ý")

    def test_response_wait_does_not_advance_while_chatgpt_is_busy(self):
        page = MagicMock()
        with (
            patch(
                "auto_yt.services.chatgpt_worker.is_chatgpt_generation_active",
                return_value=True,
            ),
            patch(
                "auto_yt.services.chatgpt_worker.get_new_assistant_response",
                return_value="Phản hồi chưa hoàn tất",
            ),
            patch(
                "auto_yt.services.chatgpt_worker.time.monotonic",
                side_effect=(0, CHATGPT_RESPONSE_TIMEOUT_SECONDS + 1),
            ),
            self.assertRaises(ChatGPTGenerationTimeoutError),
        ):
            wait_for_assistant_response(page, 2)

    def test_dom_fallback_does_not_reuse_an_existing_assistant_message(self):
        page = MagicMock()
        assistant_messages = MagicMock()
        assistant_messages.count.return_value = 1
        page.locator.return_value = assistant_messages

        with (
            patch(
                "auto_yt.services.chatgpt_worker.get_visible_conversation_turns",
                return_value=[],
            ),
            patch(
                "auto_yt.services.chatgpt_worker._read_assistant_message"
            ) as read_message,
        ):
            response = get_new_assistant_response(
                page,
                previous_assistant_turn=-1,
                previous_assistant_count=1,
            )

        self.assertEqual(response, "")
        read_message.assert_not_called()

    def test_dom_fallback_reads_only_a_new_assistant_message(self):
        page = MagicMock()
        assistant_messages = MagicMock()
        new_message = MagicMock()
        assistant_messages.count.return_value = 2
        assistant_messages.last = new_message
        page.locator.return_value = assistant_messages

        with (
            patch(
                "auto_yt.services.chatgpt_worker.get_visible_conversation_turns",
                return_value=[],
            ),
            patch(
                "auto_yt.services.chatgpt_worker._read_assistant_message",
                return_value="Phản hồi mới",
            ) as read_message,
        ):
            response = get_new_assistant_response(
                page,
                previous_assistant_turn=-1,
                previous_assistant_count=1,
            )

        self.assertEqual(response, "Phản hồi mới")
        read_message.assert_called_once_with(new_message)

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
                {
                    "busy": True,
                    "operation": "video",
                    "prompt_version": "",
                },
            )
            generate_thumbnails.assert_not_called()
        finally:
            main._finish_chatgpt_operation()

    def test_video_job_is_queued_while_thumbnail_generation_is_running(self):
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(
                main.db,
                "DB_PATH",
                Path(temporary_directory) / "database.db",
            ),
            patch.object(main, "_kick_video_queue"),
        ):
            main.db.init_db()
            self.assertTrue(main._try_start_chatgpt_operation("thumbnails"))
            try:
                response = main.process_video(
                    main.VideoRequest(
                        url="https://www.youtube.com/watch?v=test",
                        prompt_version="default",
                    )
                )
                job = main.get_job(response["job_id"])

                self.assertEqual(job["status"], "queued")
                self.assertIsNone(job["error"])
                self.assertEqual(job["queue_position"], 1)
                self.assertEqual(
                    main.get_chatgpt_status(),
                    {
                        "busy": True,
                        "operation": "thumbnails",
                        "prompt_version": "",
                    },
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
