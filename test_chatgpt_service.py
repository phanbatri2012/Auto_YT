import unittest
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

from auto_yt.services import chatgpt_service, chatgpt_worker
from auto_yt.services.chatgpt_runtime import ChatGPTAttentionRequiredError


class ChatGptServiceTests(unittest.TestCase):
    def test_assistant_reader_uses_every_non_empty_markdown_region(self):
        message = Mock()
        markdown_nodes = Mock()
        empty_markdown = Mock()
        content_markdown = Mock()
        markdown_nodes.count.return_value = 2
        markdown_nodes.first = empty_markdown
        markdown_nodes.nth.side_effect = [empty_markdown, content_markdown]
        empty_markdown.count.return_value = 1
        empty_markdown.inner_text.return_value = ""
        content_markdown.inner_text.return_value = "Metadata đầy đủ"
        message.locator.return_value = markdown_nodes
        message.inner_text.return_value = "Metadata đầy đủ"

        response = chatgpt_worker._read_assistant_message(message)

        self.assertEqual(response, "Metadata đầy đủ")

    def test_assistant_reader_falls_back_when_markdown_region_is_empty(self):
        message = Mock()
        markdown_nodes = Mock()
        empty_markdown = Mock()
        markdown_nodes.count.return_value = 1
        markdown_nodes.first = empty_markdown
        markdown_nodes.nth.return_value = empty_markdown
        empty_markdown.count.return_value = 1
        empty_markdown.inner_text.return_value = ""
        message.locator.return_value = markdown_nodes
        message.inner_text.return_value = "Nội dung trong assistant message"

        response = chatgpt_worker._read_assistant_message(message)

        self.assertEqual(response, "Nội dung trong assistant message")

    def test_assistant_reader_ignores_pure_thinking_indicator(self):
        for thinking_text in ["Stopped thinking", "Thought for 12 seconds", "Thinking...", "Đã dừng suy nghĩ"]:
            message = Mock()
            markdown_nodes = Mock()
            markdown_nodes.count.return_value = 0
            message.locator.return_value = markdown_nodes
            message.inner_text.return_value = thinking_text

            response = chatgpt_worker._read_assistant_message(message)
            self.assertEqual(response, "", f"Expected empty string for thinking text: {thinking_text}")

    def test_reads_assistant_after_latest_user_when_dom_counts_are_stable(self):
        page = Mock()
        messages = Mock()
        old_user = Mock()
        old_assistant = Mock()
        latest_user = Mock()
        latest_assistant = Mock()
        messages.count.return_value = 4
        message_nodes = [
            old_user,
            old_assistant,
            latest_user,
            latest_assistant,
        ]
        messages.nth.side_effect = lambda index: message_nodes[index]
        old_user.get_attribute.return_value = "user"
        old_assistant.get_attribute.return_value = "assistant"
        latest_user.get_attribute.return_value = "user"
        latest_user.inner_text.return_value = "Prompt metadata"
        latest_assistant.get_attribute.return_value = "assistant"
        page.locator.return_value = messages

        with patch.object(
            chatgpt_worker,
            "_read_assistant_message",
            return_value="Metadata mới",
        ) as read_message:
            response = chatgpt_worker.get_assistant_response_after_latest_user(
                page,
                expected_user_text="Prompt metadata",
            )

        self.assertEqual(response, "Metadata mới")
        read_message.assert_called_once_with(latest_assistant)

    def test_response_timeout_recovers_by_reloading_without_resending(self):
        page = Mock()
        with (
            patch.object(
                chatgpt_worker,
                "accept_external_app_permission_dialog",
                return_value=False,
            ),
            patch.object(
                chatgpt_worker,
                "is_chatgpt_generation_active",
                return_value=False,
            ),
            patch.object(
                chatgpt_worker,
                "get_new_assistant_response",
                return_value="",
            ),
            patch.object(
                chatgpt_worker,
                "recover_assistant_response_after_reload",
                return_value="Metadata recovered",
            ) as recover_response,
            patch.object(
                chatgpt_worker.time,
                "monotonic",
                side_effect=[0, 2],
            ),
        ):
            response = chatgpt_worker.wait_for_assistant_response(
                page,
                previous_assistant_turn=4,
                timeout=1,
                submitted_prompt_text="Prompt metadata",
            )

        self.assertEqual(response, "Metadata recovered")
        recover_response.assert_called_once_with(page, "Prompt metadata")

    def test_response_wait_uses_latest_user_fallback_after_busy_state(self):
        page = Mock()
        with (
            patch.object(
                chatgpt_worker,
                "accept_external_app_permission_dialog",
                return_value=False,
            ),
            patch.object(
                chatgpt_worker,
                "is_chatgpt_generation_active",
                side_effect=[True, False, False],
            ),
            patch.object(
                chatgpt_worker,
                "get_new_assistant_response",
                return_value="",
            ),
            patch.object(
                chatgpt_worker,
                "get_assistant_response_after_latest_user",
                side_effect=["", "Metadata generated", "Metadata generated"],
            ) as fallback_response,
            patch.object(
                chatgpt_worker.time,
                "monotonic",
                side_effect=[0, 0.5, 1, 2.1],
            ),
            patch.object(chatgpt_worker.time, "sleep"),
        ):
            response = chatgpt_worker.wait_for_assistant_response(
                page,
                previous_assistant_turn=4,
                timeout=20,
                submitted_prompt_text="Prompt metadata",
            )

        self.assertEqual(response, "Metadata generated")
        self.assertEqual(fallback_response.call_count, 3)

    def test_response_recovery_reloads_the_same_conversation_once(self):
        page = Mock(url="https://chatgpt.com/c/saved-chat")
        prompt_locator = Mock()
        prompt_editor = Mock()
        prompt_locator.first = prompt_editor
        page.locator.return_value = prompt_locator

        with (
            patch.object(chatgpt_worker, "check_chatgpt_page_attention"),
            patch.object(
                chatgpt_worker,
                "ensure_expected_conversation_page",
            ) as ensure_conversation,
            patch.object(
                chatgpt_worker,
                "wait_for_conversation_history",
            ) as wait_for_history,
            patch.object(
                chatgpt_worker,
                "wait_for_existing_assistant_response",
                return_value="Metadata recovered",
            ),
        ):
            response = chatgpt_worker.recover_assistant_response_after_reload(
                page,
                "Prompt metadata",
            )

        self.assertEqual(response, "Metadata recovered")
        page.reload.assert_called_once_with(
            wait_until="domcontentloaded",
            timeout=chatgpt_worker.CHATGPT_NAVIGATION_TIMEOUT_MS,
        )
        ensure_conversation.assert_called_once_with(page.url, page.url)
        wait_for_history.assert_called_once_with(page, composer_ready=True)

    def test_pending_generation_prompt_is_recovered_without_resending(self):
        prompt_text = "Prompt metadata"
        state = {
            "current_step": "metadata",
            "pending_prompt": {
                "step": "metadata",
                "fingerprint": chatgpt_worker.get_prompt_fingerprint(prompt_text),
            },
        }
        page = Mock()

        with (
            patch.object(
                chatgpt_worker,
                "recover_pending_prompt_response",
                return_value="Metadata recovered",
            ) as recover_response,
            patch.object(chatgpt_worker, "send_prompt") as send_prompt,
        ):
            response = chatgpt_worker.send_or_recover_generation_prompt(
                page,
                state,
                "metadata",
                prompt_text,
            )

        self.assertEqual(response, "Metadata recovered")
        from unittest.mock import ANY
        recover_response.assert_called_once_with(page, prompt_text, on_prompt_submitted=ANY)
        send_prompt.assert_not_called()

    def test_unresolved_pending_prompt_never_triggers_an_automatic_resend(self):
        prompt_text = "Prompt metadata"
        state = {
            "current_step": "metadata",
            "pending_prompt": {
                "step": "metadata",
                "fingerprint": chatgpt_worker.get_prompt_fingerprint(prompt_text),
            },
        }

        with (
            patch.object(
                chatgpt_worker,
                "recover_pending_prompt_response",
                side_effect=RuntimeError("Response is still unavailable"),
            ),
            patch.object(chatgpt_worker, "send_prompt") as send_prompt,
            self.assertRaisesRegex(RuntimeError, "still unavailable"),
        ):
            chatgpt_worker.send_or_recover_generation_prompt(
                Mock(),
                state,
                "metadata",
                prompt_text,
            )

        send_prompt.assert_not_called()

    def test_generation_prompt_is_checkpointed_after_submission(self):
        page = Mock(url="https://chatgpt.com/c/new-chat")
        state = {"current_step": "metadata", "chat_url": ""}

        def submit_prompt(_page, _prompt, **kwargs):
            kwargs["on_prompt_submitted"]()
            return "Metadata generated"

        with (
            patch.object(chatgpt_worker, "send_prompt", side_effect=submit_prompt),
            patch.object(
                chatgpt_worker,
                "persist_generation_state",
            ) as persist_state,
        ):
            response = chatgpt_worker.send_or_recover_generation_prompt(
                page,
                state,
                "metadata",
                "Prompt metadata",
            )

        self.assertEqual(response, "Metadata generated")
        self.assertEqual(state["pending_prompt"]["step"], "metadata")
        self.assertEqual(
            state["pending_prompt"]["fingerprint"],
            chatgpt_worker.get_prompt_fingerprint("Prompt metadata"),
        )
        self.assertEqual(state["chat_url"], page.url)
        persist_state.assert_called_once_with(state)

    def test_external_app_permission_dialog_is_accepted_without_app_specific_logic(self):
        page = Mock()
        dialog = Mock()
        deny_button = Mock()
        allow_button = Mock()
        dialogs = Mock()
        buttons = Mock()

        buttons.count.return_value = 2
        buttons.nth.side_effect = lambda idx: [deny_button, allow_button][idx]
        deny_button.is_visible.return_value = True
        allow_button.is_visible.return_value = True
        deny_button.inner_text.return_value = "Deny"
        allow_button.inner_text.return_value = "Allow"
        page.locator.return_value = buttons

        dismissed = chatgpt_worker.accept_external_app_permission_dialog(page)

        self.assertTrue(dismissed)
        page.locator.assert_called_once_with("button")
        allow_button.click.assert_called_once_with(
            timeout=chatgpt_worker.EXTERNAL_APP_PERMISSION_CLICK_TIMEOUT_MS
        )
        deny_button.click.assert_not_called()

    def test_unrelated_dialog_is_not_dismissed(self):
        page = Mock()
        dialog = Mock()
        deny_button = Mock()
        dialogs = Mock()

        buttons = Mock()
        buttons.count.return_value = 1
        buttons.nth.return_value = deny_button
        deny_button.is_visible.return_value = True
        deny_button.inner_text.return_value = "Deny"
        page.locator.return_value = buttons

        dismissed = chatgpt_worker.accept_external_app_permission_dialog(page)

        self.assertFalse(dismissed)
        deny_button.click.assert_not_called()

    def test_response_wait_clears_external_app_permission_dialog(self):
        page = Mock()

        with (
            patch.object(
                chatgpt_worker,
                "accept_external_app_permission_dialog",
                return_value=True,
            ) as dismiss_dialog,
            patch.object(
                chatgpt_worker,
                "is_chatgpt_generation_active",
                return_value=False,
            ),
            patch.object(
                chatgpt_worker,
                "get_new_assistant_response",
                return_value="Generated response",
            ),
            patch.object(chatgpt_worker, "ASSISTANT_RESPONSE_STABLE_SECONDS", 0),
        ):
            result = chatgpt_worker.wait_for_assistant_response(page, -1)

        self.assertEqual(result, "Generated response")
        dismiss_dialog.assert_called_once_with(page)

    def test_prompt_validation_allows_query_strings_in_source_urls(self):
        prompt = (
            "Mô tả video hợp lệ.\n"
            "Kênh: youtube.com/channel/UCnFwJ4JLuBbrKObiALIFtlA?sub_confirmation=1\n"
            "Nguồn: https://www.youtube.com/watch?v=Tf-2NkpjN7o"
        )

        chatgpt_worker.validate_prompt_text(prompt)

    def test_prompt_validation_allows_question_mark_without_following_space(self):
        chatgpt_worker.validate_prompt_text(
            "Ý kiến người xem: Chỉ khác nhau về thời điểm mà thôi?VD như sau."
        )

    def test_prompt_validation_allows_multiple_natural_questions(self):
        chatgpt_worker.validate_prompt_text(
            "Vì sao nhân vật rời đi? Ai là người kế tục? Điều gì xảy ra? "
            "Kết quả có thay đổi hay không? Chúng ta học được gì?"
        )

    def test_prompt_validation_does_not_treat_json_viewer_text_as_corruption(self):
        prompt = (
            'UNTRUSTED_COMMENTS_START\n[{"comment":"thôi?VD này?cũng hợp lệ?đúng"}]'
            "\nUNTRUSTED_COMMENTS_END"
        )

        chatgpt_worker.validate_prompt_text(prompt)

    def test_prompt_validation_still_rejects_corrupted_unicode_text(self):
        with self.assertRaisesRegex(ValueError, "corrupted Unicode"):
            chatgpt_worker.validate_prompt_text(
                "??y l? b?n BODY thay th? ho?n to?n cho b?n c?"
            )

    def test_prompt_submission_is_attempted_only_once_when_confirmation_times_out(self):
        page = Mock()
        page.url = "https://chatgpt.com/g/g-p-test/project"
        prompt_textarea = Mock()
        send_button = Mock()
        page.locator.side_effect = lambda selector: Mock(
            first=(
                send_button
                if "send-button" in selector or "Send" in selector
                else prompt_textarea
            )
        )
        page.wait_for_function.side_effect = [
            None,
            None,
            TimeoutError("submission confirmation timeout"),
        ]

        with (
            patch.object(
                chatgpt_worker,
                "wait_for_chatgpt_composer",
                return_value=prompt_textarea,
            ),
            patch.object(chatgpt_worker, "is_chatgpt_conversation_url", return_value=False),
            patch.object(chatgpt_worker, "get_latest_conversation_turn", return_value=-1),
            patch.object(chatgpt_worker, "get_assistant_message_count", return_value=0),
            patch.object(chatgpt_worker, "get_user_message_count", return_value=0),
            patch.object(chatgpt_worker, "ensure_prompt_editor_integrity"),
            self.assertRaisesRegex(RuntimeError, "No automatic resend"),
        ):
            chatgpt_worker.send_prompt(page, "Prompt test")

        send_button.click.assert_called_once_with()
        prompt_textarea.press.assert_not_called()

    def test_composer_wait_recovers_full_page_try_again_without_new_navigation(self):
        page = Mock()
        page.url = "https://chatgpt.com/g/g-p-test/project"
        prompt_textarea = Mock()
        prompt_textarea.wait_for.side_effect = [TimeoutError(), None]
        page.locator.return_value.first = prompt_textarea

        with (
            patch.object(
                chatgpt_worker,
                "get_chatgpt_load_state",
                return_value={
                    "editor_present": False,
                    "conversation_turn_count": 0,
                    "full_page_retry": True,
                    "body_preview": "Try again",
                },
            ),
            patch.object(
                chatgpt_worker,
                "click_chatgpt_full_page_retry",
                return_value=True,
            ) as click_retry,
        ):
            result = chatgpt_worker.wait_for_chatgpt_composer(page)

        self.assertIs(result, prompt_textarea)
        click_retry.assert_called_once_with(page)
        page.reload.assert_not_called()
        page.goto.assert_not_called()

    def test_composer_wait_reloads_same_page_when_retry_button_does_not_recover(self):
        page = Mock()
        page.url = "https://chatgpt.com/g/g-p-test/c/conversation"
        prompt_textarea = Mock()
        prompt_textarea.wait_for.side_effect = [
            TimeoutError(),
            TimeoutError(),
            None,
        ]
        page.locator.return_value.first = prompt_textarea

        with (
            patch.object(
                chatgpt_worker,
                "get_chatgpt_load_state",
                return_value={
                    "editor_present": False,
                    "conversation_turn_count": 0,
                    "full_page_retry": True,
                    "body_preview": "Try again",
                },
            ),
            patch.object(
                chatgpt_worker,
                "click_chatgpt_full_page_retry",
                return_value=True,
            ) as click_retry,
        ):
            result = chatgpt_worker.wait_for_chatgpt_composer(page)

        self.assertIs(result, prompt_textarea)
        click_retry.assert_called_once_with(page)
        page.reload.assert_called_once_with(
            wait_until="domcontentloaded",
            timeout=60000,
        )
        page.goto.assert_not_called()

    def test_composer_wait_is_bounded_and_reports_no_prompt_was_sent(self):
        page = Mock()
        page.url = "https://chatgpt.com/g/g-p-test/project"
        prompt_textarea = Mock()
        prompt_textarea.wait_for.side_effect = TimeoutError()
        page.locator.return_value.first = prompt_textarea

        with patch.object(
            chatgpt_worker,
            "get_chatgpt_load_state",
            return_value={
                "editor_present": False,
                "conversation_turn_count": 0,
                "full_page_retry": False,
                "body_preview": "",
            },
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "No prompt was sent",
            ):
                chatgpt_worker.wait_for_chatgpt_composer(page)

        self.assertEqual(prompt_textarea.wait_for.call_count, 3)
        self.assertEqual(page.reload.call_count, 2)
        page.goto.assert_not_called()

    def test_composer_wait_does_not_recover_when_editor_is_ready(self):
        page = Mock()
        prompt_textarea = Mock()
        page.locator.return_value.first = prompt_textarea

        with patch.object(
            chatgpt_worker,
            "get_chatgpt_load_state",
        ) as get_state:
            result = chatgpt_worker.wait_for_chatgpt_composer(page)

        self.assertIs(result, prompt_textarea)
        get_state.assert_not_called()
        page.reload.assert_not_called()
        page.goto.assert_not_called()

    def test_composer_wait_stops_for_manual_login_without_reloading(self):
        page = Mock()
        prompt_textarea = Mock()
        prompt_textarea.wait_for.side_effect = TimeoutError()
        page.locator.return_value.first = prompt_textarea

        with patch.object(
            chatgpt_worker,
            "get_chatgpt_load_state",
            return_value={
                "editor_present": False,
                "conversation_turn_count": 0,
                "full_page_retry": False,
                "login_required": True,
                "challenge_present": False,
                "body_preview": "Log in",
            },
        ):
            with self.assertRaises(ChatGPTAttentionRequiredError):
                chatgpt_worker.wait_for_chatgpt_composer(page)

        page.reload.assert_not_called()

    def test_narrative_sanitizer_removes_isolated_editorial_lines(self):
        first_paragraph = (
            "Lực lượng được tổ chức linh hoạt để thích nghi với chiến trường "
            "và giữ được sức mạnh trong những thời điểm quyết định. " * 3
        )
        second_paragraph = (
            "Cách bố trí này tạo ra thế chủ động, buộc đối phương phải phân "
            "tán nguồn lực và không thể duy trì ưu thế lâu dài. " * 3
        )
        editorial_note = (
            "Làm rõ vai trò từng lực lượng\n"
            "Tăng nhịp kể và sức hút"
        )

        result = chatgpt_worker.sanitize_narrative_response(
            f"{first_paragraph}\n\n{editorial_note}\n\n{second_paragraph}"
        )

        self.assertNotIn(editorial_note, result)
        self.assertIn(first_paragraph.strip(), result)
        self.assertIn(second_paragraph.strip(), result)

    def test_narrative_sanitizer_preserves_short_complete_sentences(self):
        first_paragraph = (
            "Lực lượng được tổ chức linh hoạt để thích nghi với chiến trường "
            "và giữ được sức mạnh trong những thời điểm quyết định. " * 3
        )
        second_paragraph = (
            "Cách bố trí này tạo ra thế chủ động, buộc đối phương phải phân "
            "tán nguồn lực và không thể duy trì ưu thế lâu dài. " * 3
        )

        result = chatgpt_worker.sanitize_narrative_response(
            f"{first_paragraph}\n\nNhưng chưa hết.\n\n{second_paragraph}"
        )

        self.assertIn("Nhưng chưa hết.", result)

    def test_narrative_sanitizer_removes_standalone_structural_label(self):
        narrative = (
            "Năm 1954, đất nước đứng trước nhiều lựa chọn có ảnh hưởng lâu dài "
            "và câu chuyện được kể lại theo đúng trình tự lịch sử. " * 3
        )

        result = chatgpt_worker.sanitize_narrative_response(
            f"Mở đầu\n\n{narrative}"
        )

        self.assertFalse(result.startswith("Mở đầu"))
        self.assertIn(narrative.strip(), result)

    def test_narrative_sanitizer_preserves_natural_sentence_with_label_words(self):
        first_paragraph = (
            "Bối cảnh lịch sử được trình bày rõ ràng để người nghe hiểu được "
            "những quyết định quan trọng trong giai đoạn này. " * 3
        )
        second_paragraph = (
            "Các sự kiện tiếp theo được nối liền theo trình tự và giữ nguyên "
            "những dữ kiện cần thiết của câu chuyện. " * 3
        )
        bridge = "Mở đầu câu chuyện là một quyết định đầy bất ngờ."

        result = chatgpt_worker.sanitize_narrative_response(
            f"{first_paragraph}\n\n{bridge}\n\n{second_paragraph}"
        )

        self.assertIn(bridge, result)

    def test_narrative_sanitizer_removes_editorial_lines_at_section_end(self):
        narrative = (
            "Điều giữ hai con người ở lại với nhau vẫn là sự tôn trọng, "
            "khả năng lắng nghe và mong muốn cùng nhau gìn giữ gia đình. " * 3
        )
        editorial_note = (
            "Chia đoạn dài thành các nhịp dễ đọc\n"
            "Thêm câu chuyển ý giữa các luận điểm"
        )

        result = chatgpt_worker.sanitize_narrative_response(
            f"{narrative}\n\n{editorial_note}"
        )

        self.assertNotIn(editorial_note, result)
        self.assertIn(narrative.strip(), result)

    def test_narrative_sanitizer_never_erases_the_only_response_block(self):
        self.assertEqual(
            chatgpt_worker.sanitize_narrative_response("Body complete"),
            "Body complete",
        )

    def test_video_script_builder_sanitizes_all_narrative_sections(self):
        long_paragraph = (
            "Diễn biến được kể liên tục, đầy đủ sự kiện và giữ đúng trình tự "
            "để người nghe có thể theo dõi câu chuyện một cách tự nhiên. " * 3
        )
        state = {
            "intro": f"Ghi chú biên tập\n\n{long_paragraph}",
            "body_parts": [
                f"{long_paragraph}\n\nTăng nhịp kể\n\n{long_paragraph}"
            ],
            "outro": f"Làm rõ thông điệp cuối\n\n{long_paragraph}",
            "metadata": "Metadata",
            "chapters": "00:00 - Mở đầu",
        }

        script = chatgpt_worker.build_video_script(state)

        self.assertNotIn("Ghi chú biên tập", script)
        self.assertNotIn("Tăng nhịp kể", script)
        self.assertNotIn("Làm rõ thông điệp cuối", script)
        self.assertIn("Metadata", script)
        self.assertIn("00:00 - Mở đầu", script)

    def test_complete_script_sanitizer_preserves_non_narrative_sections(self):
        long_paragraph = (
            "Lực lượng được tổ chức linh hoạt để thích nghi với chiến trường "
            "và giữ được sức mạnh trong những thời điểm quyết định. " * 3
        )
        script = (
            f"### [INTRO]\n{long_paragraph}\n\n"
            f"### [BODY]\n{long_paragraph}\n\n"
            "Làm rõ vai trò từng lực lượng\n"
            "Tăng nhịp kể và sức hút\n\n"
            f"{long_paragraph}\n\n"
            f"### [OUTRO]\n{long_paragraph}\n\n"
            "### [METADATA & QUIZ]\n"
            "TIÊU ĐỀ: Giữ nguyên metadata\n\n"
            "### [CHAPTERS]\n00:00 - Mở đầu\n"
        )

        result = chatgpt_worker.sanitize_generated_script(script)

        self.assertNotIn("Làm rõ vai trò từng lực lượng", result)
        self.assertNotIn("Tăng nhịp kể và sức hút", result)
        self.assertIn("TIÊU ĐỀ: Giữ nguyên metadata", result)
        self.assertIn("00:00 - Mở đầu", result)

    def test_video_id_is_forwarded_for_durable_worker_checkpoints(self):
        completed_process = Mock(
            returncode=0,
            stdout=b"Generated script",
            stderr=b"",
        )
        with patch.object(
            chatgpt_service.subprocess,
            "run",
            return_value=completed_process,
        ) as run_worker:
            chatgpt_service.process_prompt_via_chatgpt(
                "transcript",
                "version-key",
                video_id=122,
            )

        worker_env = run_worker.call_args.kwargs["env"]
        self.assertEqual(worker_env["PROMPT_VERSION"], "version-key")
        self.assertEqual(worker_env["VIDEO_ID"], "122")
        source_root = Path(chatgpt_service.__file__).resolve().parents[2]
        self.assertIn(str(source_root), worker_env["PYTHONPATH"].split(os.pathsep))
        self.assertEqual(
            Path(run_worker.call_args.kwargs["cwd"]),
            source_root.parent,
        )

    def test_worker_environment_preserves_existing_python_path(self):
        completed_process = Mock(
            returncode=0,
            stdout=b"Generated script",
            stderr=b"",
        )
        existing_path = str(Path.cwd() / "existing-python-path")
        with (
            patch.dict(os.environ, {"PYTHONPATH": existing_path}),
            patch.object(
                chatgpt_service.subprocess,
                "run",
                return_value=completed_process,
            ) as run_worker,
        ):
            chatgpt_service.process_prompt_via_chatgpt("transcript")

        worker_paths = run_worker.call_args.kwargs["env"]["PYTHONPATH"].split(
            os.pathsep
        )
        self.assertIn(existing_path, worker_paths)

    def test_subprocess_attention_marker_preserves_the_error_type(self):
        completed_process = Mock(
            returncode=1,
            stdout=b"",
            stderr=(
                b"diagnostic\n###CHATGPT_ATTENTION_REQUIRED###"
                b"ChatGPT login expired"
            ),
        )

        with patch.object(
            chatgpt_service.subprocess,
            "run",
            return_value=completed_process,
        ):
            with self.assertRaisesRegex(
                ChatGPTAttentionRequiredError,
                "login expired",
            ):
                chatgpt_service.process_prompt_via_chatgpt("transcript")

    def test_pipeline_snapshot_is_forwarded_to_the_worker(self):
        pipeline = {
            "metadata": False,
            "chapters": True,
            "thumbnail_with_text": False,
            "thumbnail_without_text": True,
            "audio": False,
        }
        completed_process = Mock(
            returncode=0,
            stdout=b"Generated script",
            stderr=b"",
        )
        with patch.object(
            chatgpt_service.subprocess,
            "run",
            return_value=completed_process,
        ) as run_worker:
            chatgpt_service.process_prompt_via_chatgpt(
                "transcript",
                "version-key",
                video_id=122,
                pipeline=pipeline,
            )

        worker_env = run_worker.call_args.kwargs["env"]
        self.assertEqual(
            json.loads(worker_env["PROMPT_PIPELINE_JSON"]),
            pipeline,
        )

    def test_disabled_optional_pipeline_steps_send_no_chatgpt_prompt(self):
        page = Mock()
        page.url = "https://chatgpt.com/g/g-p-test/project"
        context = Mock()
        context.pages = [page]
        state = {
            "chat_url": "",
            "current_step": "outro",
            "expected_body_parts": 1,
            "outline_parts": ["Part one"],
            "intro": "Intro complete",
            "body_parts": ["Body complete"],
            "outro": "Outro complete",
            "metadata": "",
            "chapters": "",
            "thumb_text": "",
            "thumb_notext": "",
            "pipeline": {
                "title": False,
                "slug": False,
                "description": False,
                "tags": False,
                "pinned_comment": False,
                "quiz": False,
                "metadata": False,
                "chapters": False,
                "thumbnail_with_text": False,
                "thumbnail_without_text": False,
                "audio": False,
            },
        }
        playwright_manager = Mock()
        playwright_manager.__enter__ = Mock(
            return_value=Mock(chromium=Mock())
        )
        playwright_manager.__exit__ = Mock(return_value=False)

        with (
            patch.object(chatgpt_worker, "gpt_profile_dir", return_value=Path.cwd()),
            patch.object(
                chatgpt_worker,
                "sync_playwright",
                return_value=playwright_manager,
            ),
            patch.object(
                chatgpt_worker,
                "launch_chatgpt_context",
                return_value=context,
            ),
            patch.object(chatgpt_worker, "ensure_expected_project_page"),
            patch.object(chatgpt_worker, "send_prompt") as send_prompt,
            patch.object(chatgpt_worker, "send_thumbnail_prompt") as send_thumbnail,
            patch.object(chatgpt_worker, "persist_generation_state"),
        ):
            result = chatgpt_worker._run_complete("Transcript", state)

        send_prompt.assert_not_called()
        send_thumbnail.assert_not_called()
        self.assertEqual(result["pipeline"], state["pipeline"])
        self.assertTrue(result["complete_for_audio"])

    def test_video_pipeline_recovers_pending_metadata_without_resending(self):
        page = Mock(url="https://chatgpt.com/c/saved-chat")
        context = Mock(pages=[page])
        state = {
            "chat_url": page.url,
            "current_step": "title",
            "expected_body_parts": 1,
            "outline_parts": ["Part one"],
            "intro": "Intro complete",
            "body_parts": ["Body complete"],
            "outro": "Outro complete",
            "title": "",
            "slug": "",
            "description": "",
            "tags": "",
            "pinned_comment": "",
            "quiz": "",
            "chapters": "",
            "thumb_text": "",
            "thumb_notext": "",
            "pending_prompt": {"step": "title", "fingerprint": "saved"},
            "pipeline": {
                "title": True,
                "slug": False,
                "description": False,
                "hashtags": False,
                "tags": False,
                "pinned_comment": False,
                "quiz": False,
                "chapters": False,
                "thumbnail_with_text": False,
                "thumbnail_without_text": False,
                "audio": False,
            },
        }
        playwright_manager = Mock()
        playwright_manager.__enter__ = Mock(
            return_value=Mock(chromium=Mock())
        )
        playwright_manager.__exit__ = Mock(return_value=False)

        with (
            patch.object(chatgpt_worker, "gpt_profile_dir", return_value=Path.cwd()),
            patch.object(
                chatgpt_worker,
                "sync_playwright",
                return_value=playwright_manager,
            ),
            patch.object(
                chatgpt_worker,
                "launch_chatgpt_context",
                return_value=context,
            ),
            patch.object(chatgpt_worker, "check_chatgpt_page_attention"),
            patch.object(chatgpt_worker, "ensure_expected_conversation_page"),
            patch.object(
                chatgpt_worker,
                "ensure_expected_project_conversation_page",
            ),
            patch.object(chatgpt_worker, "wait_for_conversation_history"),
            patch.object(
                chatgpt_worker,
                "_pending_prompt_matches",
                return_value=True,
            ),
            patch.object(
                chatgpt_worker,
                "recover_pending_prompt_response",
                return_value="Metadata recovered",
            ) as recover_response,
            patch.object(chatgpt_worker, "send_prompt") as send_prompt,
            patch.object(chatgpt_worker, "persist_generation_state"),
        ):
            result = chatgpt_worker._run_complete("Transcript", state)

        self.assertIn("Metadata recovered", result["script"])
        self.assertNotIn("pending_prompt", state)
        recover_response.assert_called_once()
        send_prompt.assert_not_called()

    def test_long_worker_is_not_limited_by_a_total_process_timeout(self):
        completed_process = Mock(
            returncode=0,
            stdout=b"Generated script\n###CHAT_URL###\nhttps://chatgpt.com/c/test",
            stderr=b"",
        )

        with patch.object(
            chatgpt_service.subprocess,
            "run",
            return_value=completed_process,
        ) as run_worker:
            result = chatgpt_service.process_prompt_via_chatgpt("transcript")

        self.assertNotIn("timeout", run_worker.call_args.kwargs)
        self.assertEqual(result["script"], "Generated script")
        self.assertEqual(result["chat_url"], "https://chatgpt.com/c/test")

    def test_partial_worker_metadata_is_returned_to_the_backend(self):
        completed_process = Mock(
            returncode=0,
            stdout=(
                b"Partial script\n"
                b"###CHAT_URL###https://chatgpt.com/c/test\n"
                b"###WORKER_META###"
                b'{"warning":"chapters: timed out",'
                b'"failed_step":"chapters",'
                b'"complete_for_audio":true}'
            ),
            stderr=b"",
        )

        with patch.object(
            chatgpt_service.subprocess,
            "run",
            return_value=completed_process,
        ):
            result = chatgpt_service.process_prompt_via_chatgpt("transcript")

        self.assertEqual(result["script"], "Partial script")
        self.assertEqual(result["chat_url"], "https://chatgpt.com/c/test")
        self.assertEqual(result["failed_step"], "chapters")
        self.assertTrue(result["complete_for_audio"])
        self.assertIn("timed out", result["warning"])

    def test_chapter_timeout_preserves_complete_core_script(self):
        def fail_at_chapters(_transcript, state):
            state.update(
                {
                    "chat_url": "https://chatgpt.com/c/test",
                    "current_step": "chapters",
                    "expected_body_parts": 2,
                    "intro": "Intro complete",
                    "body_parts": ["Body one", "Body two"],
                    "outro": "Outro complete",
                    "title": "Title complete",
                    "slug": "slug-complete",
                    "description": "Description complete",
                    "tags": "#Tags",
                    "pinned_comment": "Pinned complete",
                    "quiz": "Quiz complete",
                }
            )
            raise TimeoutError("generation did not finish")

        with patch.object(
            chatgpt_worker,
            "_run_complete",
            side_effect=fail_at_chapters,
        ):
            result = chatgpt_worker.run("transcript")

        self.assertTrue(result["complete_for_audio"])
        self.assertEqual(result["failed_step"], "chapters")
        self.assertIn("Body one\n\nBody two", result["script"])
        self.assertIn("Title complete", result["script"])
        self.assertIn("Title complete", result["script"])

    def test_body_timeout_never_marks_partial_script_ready_for_audio(self):
        def fail_during_body(_transcript, state):
            state.update(
                {
                    "chat_url": "https://chatgpt.com/c/test",
                    "current_step": "body 2/3",
                    "expected_body_parts": 3,
                    "intro": "Intro complete",
                    "body_parts": ["Only body one"],
                }
            )
            raise TimeoutError("generation did not finish")

        with patch.object(
            chatgpt_worker,
            "_run_complete",
            side_effect=fail_during_body,
        ):
            result = chatgpt_worker.run("transcript")

        self.assertFalse(result["complete_for_audio"])
        self.assertEqual(result["failed_step"], "body 2/3")
        self.assertIn("Only body one", result["script"])

    def test_outline_failure_with_a_created_chat_is_recoverable(self):
        def fail_after_chat_created(_transcript, state):
            state.update({
                "chat_url": "https://chatgpt.com/c/created-chat",
                "current_step": "outline",
            })
            raise RuntimeError("response arrived late")

        with patch.object(
            chatgpt_worker,
            "_run_complete",
            side_effect=fail_after_chat_created,
        ):
            result = chatgpt_worker.run("transcript")

        self.assertFalse(result["complete_for_audio"])
        self.assertEqual(result["chat_url"], "https://chatgpt.com/c/created-chat")
        self.assertEqual(result["failed_step"], "outline")

    def test_run_restores_matching_checkpoint_before_opening_chat(self):
        transcript = "saved transcript"
        checkpoint = {
            "chat_url": "https://chatgpt.com/c/saved",
            "current_step": "body 1/3",
            "expected_body_parts": 3,
            "outline_parts": ["one", "two", "three"],
            "intro": "Saved intro",
            "body_parts": ["Saved body"],
            "outro": "",
            "metadata": "",
            "chapters": "",
            "thumb_text": "",
            "thumb_notext": "",
            "transcript_fingerprint": hashlib.sha256(
                transcript.encode("utf-8")
            ).hexdigest(),
        }

        def inspect_state(_transcript, state):
            self.assertEqual(state["chat_url"], checkpoint["chat_url"])
            self.assertEqual(state["body_parts"], ["Saved body"])
            return {
                "script": "resumed",
                "chat_url": state["chat_url"],
                "warning": "",
                "failed_step": "",
                "complete_for_audio": True,
            }

        with (
            patch.dict("os.environ", {"VIDEO_ID": "122"}),
            patch.object(chatgpt_worker, "load_checkpoint", return_value=checkpoint),
            patch.object(chatgpt_worker, "_run_complete", side_effect=inspect_state),
        ):
            result = chatgpt_worker.run(transcript)

        self.assertEqual(result["script"], "resumed")

    def test_queue_pipeline_snapshot_wins_when_recovering_a_checkpoint(self):
        transcript = "saved transcript"
        pipeline = {
            "title": False,
            "slug": False,
            "description": False,
            "hashtags": False,
            "tags": False,
            "pinned_comment": False,
            "quiz": False,
            "chapters": True,
            "thumbnail_with_text": False,
            "thumbnail_without_text": False,
            "audio": True,
            "video_render": False,
            "youtube_upload": False,
            "youtube_schedule": False,
        }
        checkpoint = {
            "chat_url": "https://chatgpt.com/c/saved",
            "current_step": "body 1/2",
            "expected_body_parts": 2,
            "outline_parts": ["one", "two"],
            "intro": "Saved intro",
            "body_parts": ["Saved body"],
            "outro": "",
            "pipeline": {
                key: True for key in pipeline
            },
            "transcript_fingerprint": hashlib.sha256(
                transcript.encode("utf-8")
            ).hexdigest(),
        }

        def inspect_state(_transcript, state):
            self.assertEqual(state["pipeline"], pipeline)
            return {
                "script": "resumed",
                "chat_url": state["chat_url"],
                "warning": "",
                "failed_step": "",
                "complete_for_audio": True,
                "pipeline": state["pipeline"],
            }

        with (
            patch.dict(
                "os.environ",
                {
                    "VIDEO_ID": "122",
                    "PROMPT_PIPELINE_JSON": json.dumps(pipeline),
                },
            ),
            patch.object(chatgpt_worker, "load_checkpoint", return_value=checkpoint),
            patch.object(chatgpt_worker, "_run_complete", side_effect=inspect_state),
        ):
            result = chatgpt_worker.run(transcript)

        self.assertEqual(result["pipeline"], pipeline)


    def test_dedup_consecutive_paragraphs_removes_exact_repeats(self):
        paragraph = (
            "Muốn hiểu một con người, thưa quý vị, đừng chỉ nhìn cách họ đối xử "
            "với mình trong những lúc đang yêu thương, chiều chuộng và cố gắng. " * 2
        ).strip()
        other_paragraph = (
            "Và có một nguyên tắc rất quan trọng mà tôi muốn nhấn mạnh, đó là ngay "
            "từ khi bắt đầu một mối quan hệ, chúng ta phải biết đặt ra giới hạn. " * 2
        ).strip()
        doubled_text = f"{paragraph}\n\n{paragraph}\n\n{other_paragraph}\n\n{other_paragraph}"
        
        result = chatgpt_worker.dedup_consecutive_paragraphs(doubled_text)
        expected = f"{paragraph}\n\n{other_paragraph}"
        self.assertEqual(result, expected)

    def test_narrative_sanitizer_removes_consecutive_duplicate_prose(self):
        paragraph = (
            "Xin chào quý vị khán giả đang theo dõi kênh Thấu Hiểu Hôn Nhân, Thưa "
            "quý vị, có những cuộc hôn nhân tan vỡ không phải vì người ta hết yêu. " * 2
        ).strip()
        doubled = f"{paragraph}\n\n{paragraph}"
        result = chatgpt_worker.sanitize_narrative_response(doubled)
        self.assertEqual(result, paragraph)

    def test_outline_filter_removes_intro_and_outro_keywords(self):
        outline_parts = [
            "Mở bài: Xin chào quý vị khán giả",
            "Phần 1: Những dấu hiệu ban đầu cần lưu ý",
            "Phần 2: Cách ứng xử phù hợp",
            "Kết luận: Tóm tắt bài học",
        ]
        filtered = [
            p for p in outline_parts
            if not any(k in p.lower()[:100] for k in chatgpt_worker.OUTLINE_INTRO_OUTRO_KEYWORDS)
        ]
        self.assertEqual(len(filtered), 2)
        self.assertEqual(filtered, [
            "Phần 1: Những dấu hiệu ban đầu cần lưu ý",
            "Phần 2: Cách ứng xử phù hợp",
        ])

    def test_narrative_sanitizer_removes_canvas_headers_and_suggestion_chips(self):
        long_paragraph = (
            "Chiến tranh biên giới Tây Nam là một cuộc chiến đặc biệt cả về tốc độ, "
            "quy mô lực lượng và thời gian kéo dài sau đó. Quân đội nhân dân Việt Nam "
            "đã huy động lực lượng lớn cùng với quân giải phóng Campuchia tiến công. " * 2
        ).strip()
        raw_text = (
            f"Nội dung chính\n\n"
            f"{long_paragraph}\n\n"
            f"Thu gọn\n"
            f"Mở đầu bằng cú móc ngắn hơn\n"
            f"Giảm tiết lộ kết quả chiến dịch\n"
            f"Làm rõ mốc thời gian then chốt"
        )
        result = chatgpt_worker.sanitize_narrative_response(raw_text)
        self.assertEqual(result, long_paragraph)
        self.assertNotIn("Nội dung chính", result)
        self.assertNotIn("Thu gọn", result)
        self.assertNotIn("Mở đầu bằng cú móc", result)

    def test_narrative_sanitizer_removes_inline_canvas_headers(self):
        long_paragraph = (
            "Chỉ trong chưa đầy một tháng, một thế trận tưởng như sẽ kéo dài đã sụp đổ "
            "với tốc độ khiến cả chiến trường Campuchia đảo chiều. " * 3
        ).strip()
        raw_text = f"Mở đầu video: {long_paragraph}"
        result = chatgpt_worker.sanitize_narrative_response(raw_text)
        self.assertEqual(result, long_paragraph)
        self.assertFalse(result.startswith("Mở đầu video"))

    def test_dedup_consecutive_paragraphs_removes_subset_duplicates(self):
        p1 = "Đây là một đoạn văn bản rất dài về lịch sử quân sự của chiến trường biên giới Tây Nam trong năm 1979 với nhiều sự kiện."
        p2 = "Đây là một đoạn văn bản rất dài về lịch sử quân sự của chiến trường biên giới Tây Nam trong năm 1979 với nhiều sự kiện. Đoạn này có thêm thông tin chi tiết hơn về các cánh quân."
        result = chatgpt_worker.dedup_consecutive_paragraphs(f"{p1}\n\n{p2}")
        self.assertEqual(result, p2)


if __name__ == "__main__":
    unittest.main()

