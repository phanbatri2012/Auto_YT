import json
import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt.services import database
from auto_yt.services import chatgpt_worker
from auto_yt.services import youtube_comments
from auto_yt.services.chatgpt_runtime import (
    CHATGPT_LOGIN_REQUIRED_MESSAGE,
    ChatGPTAttentionRequiredError,
)
from auto_yt import main


class YouTubeCommentDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            database,
            "DB_PATH",
            Path(self.temp_directory.name) / "database.db",
        )
        self.database_patch.start()
        database.init_db()
        self.video_id = database.save_video(
            "https://youtube.com/watch?v=source123",
            "Original title",
            "Transcript",
            "### [INTRO]\nIntro\n### [BODY]\nBody\n### [OUTRO]\nOutro",
            chat_url="https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            prompt_version="prompt-a",
        )
        self.channel = database.save_youtube_channel(
            channel_id="UC123",
            title="Channel A",
            access_token_encrypted="encrypted-access",
            refresh_token_encrypted="encrypted-refresh",
        )

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def test_publication_keeps_source_link_and_marks_video_published(self):
        publication = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="published123",
            published_url="https://youtu.be/published123",
            published_title="Published title",
        )

        video = database.get_video(self.video_id)
        listed = database.list_video_publications(self.video_id)

        self.assertEqual(video["url"], "https://youtube.com/watch?v=source123")
        self.assertEqual(video["is_published"], 1)
        self.assertEqual(publication["youtube_video_id"], "published123")
        self.assertEqual(listed[0]["channel_title"], "Channel A")
        self.assertEqual(listed[0]["chat_url"], video["chat_url"])

    def test_unassigned_publication_is_listed_and_can_be_deleted(self):
        publication = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=None,
            youtube_video_id="unverified123",
            published_url="https://www.youtube.com/watch?v=unverified123",
        )

        listed = database.list_video_publications(self.video_id)

        self.assertIsNone(publication["youtube_channel_id"])
        self.assertIsNone(listed[0]["youtube_channel_id"])
        self.assertIsNone(listed[0]["channel_title"])
        self.assertEqual(database.get_video(self.video_id)["is_published"], 1)
        self.assertTrue(database.delete_video_publication(publication["id"]))
        self.assertEqual(database.get_video(self.video_id)["is_published"], 0)

    def test_unassigned_publication_can_be_verified_but_not_reassigned(self):
        database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=None,
            youtube_video_id="upgrade123",
            published_url="https://www.youtube.com/watch?v=upgrade123",
        )

        verified = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="upgrade123",
            published_url="https://www.youtube.com/watch?v=upgrade123",
        )
        other_channel = database.save_youtube_channel(
            channel_id="UC456",
            title="Channel B",
        )

        self.assertEqual(verified["youtube_channel_id"], self.channel["id"])
        with self.assertRaisesRegex(ValueError, "video hoặc kênh khác"):
            database.save_video_publication(
                video_id=self.video_id,
                youtube_channel_id=None,
                youtube_video_id="upgrade123",
                published_url="https://www.youtube.com/watch?v=upgrade123",
            )
        with self.assertRaisesRegex(ValueError, "video hoặc kênh khác"):
            database.save_video_publication(
                video_id=self.video_id,
                youtube_channel_id=other_channel["id"],
                youtube_video_id="upgrade123",
                published_url="https://www.youtube.com/watch?v=upgrade123",
            )

    def test_verified_import_assigns_existing_unassigned_publication(self):
        original_chat = database.get_video(self.video_id)["chat_url"]
        publication = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=None,
            youtube_video_id="bind123",
            published_url="https://www.youtube.com/watch?v=bind123",
        )

        reservation = database.reserve_comment_import_video(
            youtube_channel_id=self.channel["id"],
            youtube_video_id="bind123",
            published_url="https://www.youtube.com/watch?v=bind123",
            title="Verified title",
            description="Verified description",
            published_at="2026-09-14T00:00:00Z",
            prompt_version="other-prompt",
        )

        self.assertTrue(reservation["assigned_channel"])
        self.assertFalse(reservation["created_publication"])
        self.assertEqual(reservation["publication"]["id"], publication["id"])
        self.assertEqual(
            reservation["publication"]["youtube_channel_id"], self.channel["id"]
        )
        self.assertEqual(reservation["video"]["id"], self.video_id)
        self.assertEqual(reservation["video"]["chat_url"], original_chat)
        other_channel = database.save_youtube_channel(
            channel_id="UC-other-import",
            title="Other import channel",
        )
        with self.assertRaisesRegex(ValueError, "kênh khác"):
            database.reserve_comment_import_video(
                youtube_channel_id=other_channel["id"],
                youtube_video_id="bind123",
                published_url="https://www.youtube.com/watch?v=bind123",
                title="Wrong channel",
                description="",
                published_at="",
                prompt_version="prompt-a",
            )

    def test_nullable_channel_migration_preserves_publication_and_comments(self):
        publication = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="migration123",
            published_url="https://www.youtube.com/watch?v=migration123",
        )
        database.upsert_youtube_comment(
            publication["id"],
            {"comment_id": "migration-comment", "text": "Keep this comment"},
        )
        conn = sqlite3.connect(str(database.DB_PATH))
        conn.executescript(
            '''
            CREATE TABLE video_publications_old_schema (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                youtube_channel_id INTEGER NOT NULL,
                youtube_video_id TEXT NOT NULL UNIQUE,
                published_url TEXT NOT NULL,
                published_title TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                FOREIGN KEY(youtube_channel_id) REFERENCES youtube_channels(id) ON DELETE CASCADE
            );
            INSERT INTO video_publications_old_schema (
                id, video_id, youtube_channel_id, youtube_video_id,
                published_url, published_title, published_at, created_at, updated_at
            )
            SELECT id, video_id, youtube_channel_id, youtube_video_id,
                   published_url, published_title, published_at, created_at, updated_at
            FROM video_publications;
            DROP TABLE video_publications;
            ALTER TABLE video_publications_old_schema RENAME TO video_publications;
            CREATE INDEX idx_video_publications_video ON video_publications(video_id);
            CREATE INDEX idx_video_publications_channel ON video_publications(youtube_channel_id);
            '''
        )
        conn.close()

        database.init_db()

        conn = sqlite3.connect(str(database.DB_PATH))
        channel_column = next(
            row
            for row in conn.execute("PRAGMA table_info(video_publications)")
            if row[1] == "youtube_channel_id"
        )
        index_names = {
            row[1] for row in conn.execute("PRAGMA index_list(video_publications)")
        }
        preserved_publication = conn.execute(
            "SELECT id, youtube_video_id FROM video_publications WHERE id = ?",
            (publication["id"],),
        ).fetchone()
        preserved_comment = conn.execute(
            "SELECT publication_id, text FROM youtube_comments WHERE comment_id = ?",
            ("migration-comment",),
        ).fetchone()
        conn.close()

        self.assertEqual(channel_column[3], 0)
        self.assertEqual(preserved_publication, (publication["id"], "migration123"))
        self.assertEqual(preserved_comment, (publication["id"], "Keep this comment"))
        self.assertIn("idx_video_publications_video", index_names)
        self.assertIn("idx_video_publications_channel", index_names)
        with self.assertRaises(sqlite3.IntegrityError):
            conn = sqlite3.connect(str(database.DB_PATH))
            try:
                conn.execute(
                    '''
                    INSERT INTO video_publications (
                        video_id, youtube_channel_id, youtube_video_id,
                        published_url, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        self.video_id,
                        None,
                        "migration123",
                        "https://youtu.be/migration123",
                        database.utc_now(),
                        database.utc_now(),
                    ),
                )
            finally:
                conn.close()
        backup_path = database.DB_PATH.with_name(
            f"{database.DB_PATH.name}"
            f"{database.NULLABLE_PUBLICATION_CHANNEL_BACKUP_SUFFIX}"
        )
        self.assertTrue(backup_path.exists())

    def test_comment_upsert_preserves_draft_and_is_deleted_with_video(self):
        publication = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="published123",
            published_url="https://youtu.be/published123",
        )
        comment = {
            "comment_id": "comment-1",
            "text": "Nội dung rất hay",
            "author_name": "Viewer",
            "published_at": "2026-09-10T01:00:00Z",
        }
        database.upsert_youtube_comment(publication["id"], comment)
        database.update_youtube_comment(
            "comment-1", status="draft_ready", draft_reply="Cảm ơn bạn"
        )

        comment["like_count"] = 9
        database.upsert_youtube_comment(publication["id"], comment)
        refreshed = database.get_youtube_comments(["comment-1"])[0]
        self.assertEqual(refreshed["status"], "draft_ready")
        self.assertEqual(refreshed["draft_reply"], "Cảm ơn bạn")
        self.assertEqual(refreshed["like_count"], 9)

        manifest = database.delete_video_with_dependencies(self.video_id)
        self.assertEqual(len(manifest["video_publications"]), 1)
        self.assertEqual(database.get_youtube_comments(["comment-1"]), [])

    def test_removing_last_publication_restores_unpublished_status(self):
        publication = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="published123",
            published_url="https://youtu.be/published123",
        )

        self.assertTrue(database.delete_video_publication(publication["id"]))
        self.assertEqual(database.get_video(self.video_id)["is_published"], 0)

    def test_comments_can_be_filtered_by_internal_video_id(self):
        first_publication = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="published123",
            published_url="https://youtu.be/published123",
        )
        database.upsert_youtube_comment(
            first_publication["id"],
            {"comment_id": "comment-first", "text": "First video"},
        )
        second_video_id = database.save_video(
            "https://youtube.com/watch?v=source456",
            "Second title",
            "Transcript",
            "Script",
        )
        second_publication = database.save_video_publication(
            video_id=second_video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="published456",
            published_url="https://youtu.be/published456",
        )
        database.upsert_youtube_comment(
            second_publication["id"],
            {"comment_id": "comment-second", "text": "Second video"},
        )

        filtered = database.list_youtube_comments(video_id=second_video_id)

        self.assertEqual([item["comment_id"] for item in filtered], ["comment-second"])

    def test_legacy_import_is_idempotent_and_hidden_from_dashboard(self):
        first = database.reserve_comment_import_video(
            youtube_channel_id=self.channel["id"],
            youtube_video_id="legacy123",
            published_url="https://www.youtube.com/watch?v=legacy123",
            title="Legacy video",
            description="Legacy description",
            published_at="2026-01-01T00:00:00Z",
            prompt_version="prompt-a",
        )
        second = database.reserve_comment_import_video(
            youtube_channel_id=self.channel["id"],
            youtube_video_id="legacy123",
            published_url="https://www.youtube.com/watch?v=legacy123",
            title="Changed title must not duplicate",
            description="Changed description",
            published_at="2026-01-01T00:00:00Z",
            prompt_version="prompt-a",
        )

        self.assertTrue(first["created_video"])
        self.assertFalse(second["created_video"])
        self.assertEqual(first["video"]["id"], second["video"]["id"])
        self.assertEqual(database.get_all_videos(limit=100)["total"], 1)
        identities = database.list_video_publication_identities()
        self.assertEqual(
            [item["youtube_video_id"] for item in identities],
            ["legacy123"],
        )

    def test_legacy_import_preserves_existing_manual_video_and_chat(self):
        original_chat = database.get_video(self.video_id)["chat_url"]
        reservation = database.reserve_comment_import_video(
            youtube_channel_id=self.channel["id"],
            youtube_video_id="source123",
            published_url="https://www.youtube.com/watch?v=source123",
            title="Remote title",
            description="Remote description",
            published_at="",
            prompt_version="other-prompt",
            existing_video_id=self.video_id,
        )

        self.assertFalse(reservation["created_video"])
        self.assertEqual(reservation["video"]["id"], self.video_id)
        self.assertEqual(reservation["video"]["chat_url"], original_chat)
        self.assertEqual(reservation["video"]["prompt_version"], "prompt-a")

    def test_comment_ids_request_supports_large_batches_and_enforces_5000_limit(self):
        from pydantic import ValidationError

        req_139 = main.CommentIdsRequest(comment_ids=[f"comment_{i}" for i in range(139)])
        self.assertEqual(len(req_139.comment_ids), 139)

        req_5000 = main.CommentIdsRequest(comment_ids=[f"comment_{i}" for i in range(5000)])
        self.assertEqual(len(req_5000.comment_ids), 5000)

        with self.assertRaises(ValidationError):
            main.CommentIdsRequest(comment_ids=[f"comment_{i}" for i in range(5001)])

    def test_publish_endpoint_processes_large_batch_selection(self):
        publication = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="published_batch",
            published_url="https://youtu.be/published_batch",
            published_title="Published Batch",
        )

        comment_ids = []
        for i in range(139):
            cid = f"batch_comment_{i}"
            comment_ids.append(cid)
            database.upsert_youtube_comment(
                publication["id"],
                {
                    "comment_id": cid,
                    "author_name": f"Viewer {i}",
                    "text": f"Bình luận số {i}",
                    "published_at": "2026-09-22T10:00:00Z",
                },
            )
            database.update_youtube_comment(
                cid,
                status="draft_ready",
                draft_reply=f"Cảm ơn bạn đã xem video {i}!",
                risk_level="low",
            )

        with patch.object(main, "_kick_comment_queue"):
            result = main.publish_youtube_comment_replies(
                main.CommentIdsRequest(comment_ids=comment_ids)
            )

        self.assertTrue(result["success"])
        self.assertEqual(len(result["job_ids"]), 139)

        jobs = database.list_system_jobs(limit=None, job_type="comment_publish")
        self.assertEqual(len(jobs), 139)


class YouTubeCommentHelperTests(unittest.TestCase):
    def test_comment_prompt_accepts_question_mark_without_following_space(self):
        prompt = youtube_comments.build_comment_reply_prompt(
            [
                {
                    "comment_id": "comment-no-space",
                    "author_name": "Viewer",
                    "text": "Chỉ khác nhau về thời điểm mà thôi?VD như sau.",
                }
            ]
        )

        chatgpt_worker.validate_prompt_text(prompt)

    def test_extracts_common_youtube_video_urls(self):
        self.assertEqual(
            youtube_comments.extract_youtube_video_id(
                "https://www.youtube.com/watch?v=abc_DEF-12"
            ),
            "abc_DEF-12",
        )
        self.assertEqual(
            youtube_comments.extract_youtube_video_id("https://youtu.be/abc_DEF-12?t=1"),
            "abc_DEF-12",
        )
        self.assertEqual(
            youtube_comments.extract_youtube_video_id(
                "https://youtube.com/shorts/abc_DEF-12"
            ),
            "abc_DEF-12",
        )

    def test_builds_and_parses_a_strict_batched_reply_prompt(self):
        comments = [
            {"comment_id": "a", "author_name": "A", "text": "Ý này đúng không?"},
            {"comment_id": "b", "author_name": "B", "text": "Cảm ơn kênh"},
        ]
        prompt = youtube_comments.build_comment_reply_prompt(
            comments, "Trả lời ngắn gọn"
        )
        self.assertIn("CHÍNH phiên chat video này", prompt)
        self.assertIn("Trả lời ngắn gọn", prompt)
        self.assertIn(json.dumps(comments[0]["comment_id"]), prompt)

        parsed = youtube_comments.parse_comment_reply_response(
            '```json\n[{"comment_id":"a","reply":"Đúng bạn nhé"},'
            '{"comment_id":"b","reply":"Cảm ơn bạn"}]\n```',
            {"a", "b"},
        )
        self.assertEqual(parsed["a"], "Đúng bạn nhé")
        self.assertEqual(parsed["b"], "Cảm ơn bạn")

    def test_rejects_partial_chatgpt_reply_batch(self):
        with self.assertRaises(youtube_comments.YouTubeCommentsError):
            youtube_comments.parse_comment_reply_response(
                '[{"comment_id":"a","reply":"OK"}]', {"a", "b"}
            )

    def test_auto_reply_priority_skips_noise_and_prioritizes_questions(self):
        question = youtube_comments.assess_auto_reply_priority(
            "Vì sao sự kiện này lại diễn ra như vậy?", "question-1"
        )
        noise = youtube_comments.assess_auto_reply_priority("👏👏", "noise-1")

        self.assertTrue(question[2])
        self.assertGreater(question[0], noise[0])
        self.assertFalse(noise[2])

    def test_reply_prompt_uses_recent_replies_only_to_avoid_repetition(self):
        prompt = youtube_comments.build_comment_reply_prompt(
            [{"comment_id": "a", "text": "Tại sao vậy?"}],
            recent_replies=["Cảm ơn bạn đã theo dõi."],
        )

        self.assertIn("TRÁNH lặp", prompt)
        self.assertIn("không lặp lời cảm ơn khuôn mẫu", prompt)

    def test_compact_reply_sample_trims_cleanly(self):
        long_reply = (
            "📌 Góp ý này rất chính xác về bối cảnh lịch sử của sự kiện. "
            "Sau giải phóng năm 1975, tình hình biên giới Tây Nam vô cùng phức tạp "
            "với hàng loạt xung đột kéo dài nhiều năm tiếp theo."
        )
        compact = youtube_comments._compact_reply_sample(long_reply, max_chars=120)
        self.assertEqual(
            compact,
            "📌 Góp ý này rất chính xác về bối cảnh lịch sử của sự kiện.",
        )

        long_sentence_no_period = "Một câu dài không có dấu chấm phân tách câu rõ ràng nhưng vẫn phải được cắt gãy gọn gàng theo ranh giới từ mà không đứt chữ"
        compact_word = youtube_comments._compact_reply_sample(long_sentence_no_period, max_chars=60)
        self.assertTrue(compact_word.endswith("..."))
        self.assertLessEqual(len(compact_word), 63)

    def test_reply_prompt_scales_recent_replies_dynamically(self):
        ten_replies = [f"Câu trả lời số {i} rất chi tiết về lịch sử." for i in range(10)]

        # Single comment gets at most 2 samples
        single_prompt = youtube_comments.build_comment_reply_prompt(
            [{"comment_id": "1", "text": "Comment lẻ"}],
            recent_replies=ten_replies,
        )
        self.assertIn("Câu trả lời số 0", single_prompt)
        self.assertIn("Câu trả lời số 1", single_prompt)
        self.assertNotIn("Câu trả lời số 2", single_prompt)

        # Batch comments get at most 4 samples
        batch_prompt = youtube_comments.build_comment_reply_prompt(
            [
                {"comment_id": "1", "text": "Cmt 1"},
                {"comment_id": "2", "text": "Cmt 2"},
            ],
            recent_replies=ten_replies,
        )
        self.assertIn("Câu trả lời số 0", batch_prompt)
        self.assertIn("Câu trả lời số 3", batch_prompt)
        self.assertNotIn("Câu trả lời số 4", batch_prompt)

    @patch("auto_yt.services.youtube_comments._request_json")
    def test_lists_all_channel_upload_pages(self, request_json):
        request_json.side_effect = [
            {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}]},
            {
                "items": [{
                    "snippet": {"title": "Newest", "resourceId": {"videoId": "video-1"}},
                    "contentDetails": {"videoId": "video-1"},
                }],
                "nextPageToken": "page-2",
            },
            {
                "items": [{
                    "snippet": {"title": "Older", "resourceId": {"videoId": "video-2"}},
                    "contentDetails": {"videoId": "video-2"},
                }]
            },
        ]

        videos = youtube_comments.list_channel_videos("token", "UC123")

        self.assertEqual(
            [item["youtube_video_id"] for item in videos],
            ["video-1", "video-2"],
        )
        self.assertIn("pageToken=page-2", request_json.call_args_list[2].args[0])

    @patch("auto_yt.services.youtube_comments._request_json")
    def test_video_details_are_batched_and_keep_description(self, request_json):
        ids = [f"video-{index}" for index in range(51)]
        request_json.side_effect = [
            {"items": [{
                "id": video_id,
                "snippet": {
                    "channelId": "UC123",
                    "channelTitle": "Channel A",
                    "title": video_id,
                    "description": f"Description {video_id}",
                },
                "status": (
                    {
                        "privacyStatus": "private",
                        "publishAt": "2026-09-20T12:00:00Z",
                    }
                    if video_id == ids[0]
                    else {"privacyStatus": "public"}
                ),
            } for video_id in ids[:50]]},
            {"items": [{
                "id": ids[50],
                "snippet": {
                    "channelId": "UC123",
                    "title": ids[50],
                    "description": "Last description",
                },
            }]},
        ]

        details = youtube_comments.get_videos_details("token", ids)

        self.assertEqual(len(details), 51)
        self.assertEqual(details[-1]["description"], "Last description")
        self.assertEqual(details[0]["channel_title"], "Channel A")
        self.assertTrue(details[0]["is_scheduled"])
        self.assertEqual(details[0]["scheduled_publish_at"], "2026-09-20T12:00:00Z")
        self.assertFalse(details[-1]["is_scheduled"])
        self.assertIn("part=snippet%2Cstatus", request_json.call_args_list[0].args[0])
        self.assertEqual(request_json.call_count, 2)


class YouTubeCommentJobTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            database,
            "DB_PATH",
            Path(self.temp_directory.name) / "database.db",
        )
        self.database_patch.start()
        database.init_db()
        self.video_id = database.save_video(
            "https://youtube.com/watch?v=source123",
            "Original title",
            "Transcript",
            "### [INTRO]\nIntro\n### [BODY]\nBody\n### [OUTRO]\nOutro",
            chat_url="https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
            prompt_version="prompt-a",
        )
        self.channel = database.save_youtube_channel(
            channel_id="UC123",
            title="Channel A",
            access_token_encrypted="encrypted-access",
            refresh_token_encrypted="encrypted-refresh",
        )
        self.publication = database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="published123",
            published_url="https://youtu.be/published123",
        )
        database.upsert_youtube_comment(
            self.publication["id"],
            {
                "comment_id": "comment-1",
                "text": "Nội dung rất hay",
                "author_name": "Viewer",
            },
        )

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def test_draft_job_uses_existing_video_chat_and_saves_reply(self):
        jobs = main._enqueue_comment_draft_jobs(["comment-1"])
        claimed = database.claim_next_system_job("comment_draft")

        with (
            patch("auto_yt.services.chatgpt_worker.generate_comment_replies") as generate,
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch.object(
                main,
                "_get_youtube_access_token",
                return_value=(self.channel, "access-token"),
            ),
            patch.object(youtube_comments, "find_channel_reply", return_value=None),
        ):
            generate.return_value = {"comment-1": "Cảm ơn bạn đã theo dõi."}
            self.assertTrue(main._execute_comment_draft_job(claimed))

        saved = database.get_youtube_comments(["comment-1"])[0]
        finished_job = database.get_system_job(jobs[0]["id"])
        self.assertEqual(saved["status"], "draft_ready")
        self.assertEqual(saved["draft_reply"], "Cảm ơn bạn đã theo dõi.")
        self.assertEqual(finished_job["status"], "done")
        generate.assert_called_once()
        self.assertEqual(
            generate.call_args.args[0],
            "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc",
        )

    def test_missing_chat_url_is_blocked_without_creating_a_new_chat(self):
        video_id = database.save_video(
            "https://youtube.com/watch?v=source456",
            "No chat",
            "Transcript",
            "Script",
        )
        publication = database.save_video_publication(
            video_id=video_id,
            youtube_channel_id=self.channel["id"],
            youtube_video_id="published456",
            published_url="https://youtu.be/published456",
        )
        database.upsert_youtube_comment(
            publication["id"], {"comment_id": "comment-no-chat", "text": "Hello"}
        )

        jobs = main._enqueue_comment_draft_jobs(["comment-no-chat"])
        saved = database.get_youtube_comments(["comment-no-chat"])[0]

        self.assertEqual(jobs, [])
        self.assertEqual(saved["status"], "error")
        self.assertIn("Chat Gốc", saved["error"])

    def test_repeated_draft_click_does_not_queue_a_second_chat_prompt(self):
        first_jobs = main._enqueue_comment_draft_jobs(["comment-1"])
        second_jobs = main._enqueue_comment_draft_jobs(["comment-1"])

        self.assertEqual(len(first_jobs), 1)
        self.assertEqual(second_jobs, [])
        self.assertEqual(
            database.get_youtube_comments(["comment-1"])[0]["status"],
            "drafting",
        )

    def test_active_draft_payload_prevents_duplicate_after_status_reset(self):
        first_jobs = main._enqueue_comment_draft_jobs(["comment-1"])
        database.pause_system_job(first_jobs[0]["id"])
        database.update_youtube_comment("comment-1", status="pending", error="")

        second_jobs = main._enqueue_comment_draft_jobs(["comment-1"])

        self.assertEqual(second_jobs, [])
        self.assertEqual(
            database.get_youtube_comments(["comment-1"])[0]["status"],
            "drafting",
        )

    def test_existing_newer_duplicate_job_is_skipped_before_external_calls(self):
        original_jobs = main._enqueue_comment_draft_jobs(["comment-1"])
        database.pause_system_job(original_jobs[0]["id"])
        duplicate = main._create_comment_system_job(
            "comment_draft",
            title="Duplicate",
            payload={"comment_ids": ["comment-1"]},
            video_id=self.video_id,
            prompt_version="prompt-a",
        )
        claimed = database.claim_next_system_job("comment_draft")

        with patch.object(main, "_get_youtube_access_token") as get_token:
            self.assertTrue(main._execute_comment_draft_job(claimed))

        finished = database.get_system_job(duplicate["id"])
        self.assertEqual(finished["status"], "done")
        self.assertTrue(finished["result"]["duplicate_job"])
        get_token.assert_not_called()

    def test_startup_reconciliation_closes_legacy_duplicate_draft_job(self):
        original_jobs = main._enqueue_comment_draft_jobs(["comment-1"])
        database.pause_system_job(original_jobs[0]["id"])
        duplicate = main._create_comment_system_job(
            "comment_draft",
            title="Duplicate",
            payload={"comment_ids": ["comment-1"]},
            video_id=self.video_id,
            prompt_version="prompt-a",
        )

        reconciled = main._reconcile_active_comment_draft_job_duplicates()

        self.assertEqual(reconciled, 1)
        self.assertEqual(database.get_system_job(duplicate["id"])["status"], "done")

    def test_ambiguous_publish_is_never_queued_again_blindly(self):
        database.update_youtube_comment(
            "comment-1",
            status="reconcile_required",
            draft_reply="Cảm ơn bạn đã theo dõi.",
            error="Không rõ YouTube đã nhận câu trả lời hay chưa.",
        )

        jobs = main._enqueue_comment_publish_jobs(["comment-1"])

        self.assertEqual(jobs, [])
        saved = database.get_youtube_comments(["comment-1"])[0]
        self.assertEqual(saved["status"], "reconcile_required")

    def test_draft_worker_skips_reply_already_present_on_youtube(self):
        jobs = main._enqueue_comment_draft_jobs(["comment-1"])
        claimed = database.claim_next_system_job("comment_draft")

        with (
            patch.object(
                main,
                "_get_youtube_access_token",
                return_value=(self.channel, "access-token"),
            ),
            patch.object(
                youtube_comments,
                "find_channel_reply",
                return_value={"reply_id": "reply-manual", "text": "Đã trả lời thủ công"},
            ),
            patch("auto_yt.services.chatgpt_worker.generate_comment_replies") as generate,
        ):
            self.assertTrue(main._execute_comment_draft_job(claimed))

        saved = database.get_youtube_comments(["comment-1"])[0]
        finished_job = database.get_system_job(jobs[0]["id"])
        self.assertEqual(saved["status"], "replied")
        self.assertEqual(saved["reply_youtube_id"], "reply-manual")
        self.assertEqual(finished_job["status"], "done")
        generate.assert_not_called()

    def test_publish_worker_checks_youtube_before_inserting_reply(self):
        database.update_youtube_comment(
            "comment-1",
            status="draft_ready",
            draft_reply="Cảm ơn bạn đã theo dõi.",
        )
        jobs = main._enqueue_comment_publish_jobs(["comment-1"])
        claimed = database.claim_next_system_job("comment_publish")

        with (
            patch.object(
                main,
                "_next_comment_publish_time",
                return_value=(
                    datetime.datetime.now(datetime.timezone.utc),
                    "test ngay lập tức",
                ),
            ),
            patch.object(
                main,
                "_get_youtube_access_token",
                return_value=(self.channel, "access-token"),
            ),
            patch.object(
                youtube_comments,
                "find_channel_reply",
                return_value={"reply_id": "reply-manual", "text": "Đã trả lời thủ công"},
            ),
            patch.object(youtube_comments, "publish_reply") as publish,
        ):
            main._execute_comment_publish_job(claimed)

        saved = database.get_youtube_comments(["comment-1"])[0]
        finished_job = database.get_system_job(jobs[0]["id"])
        self.assertEqual(saved["status"], "replied")
        self.assertEqual(finished_job["status"], "done")
        publish.assert_not_called()

    def test_sync_finds_channel_reply_missing_from_embedded_subset(self):
        sync_job = main._create_comment_system_job(
            "comment_sync",
            title="Channel A",
            payload={"channel_id": self.channel["id"]},
        )
        claimed = database.claim_next_system_job("comment_sync")
        remote_comment = {
            "comment_id": "comment-1",
            "thread_id": "thread-1",
            "youtube_video_id": "published123",
            "author_channel_id": "UC-viewer",
            "author_name": "Viewer",
            "text": "Nội dung rất hay",
            "can_reply": True,
            "total_reply_count": 2,
            "existing_reply_id": "",
            "existing_reply_text": "",
        }

        with (
            patch.object(
                main,
                "_get_youtube_access_token",
                return_value=(self.channel, "access-token"),
            ),
            patch.object(
                youtube_comments,
                "list_channel_comment_threads",
                return_value=[remote_comment],
            ),
            patch.object(
                youtube_comments,
                "find_channel_reply",
                return_value={"reply_id": "reply-older", "text": "Kênh đã trả lời"},
            ),
        ):
            main._execute_comment_sync_job(claimed)

        saved = database.get_youtube_comments(["comment-1"])[0]
        finished_job = database.get_system_job(sync_job["id"])
        self.assertEqual(saved["status"], "replied")
        self.assertEqual(saved["reply_youtube_id"], "reply-older")
        self.assertEqual(finished_job["status"], "done")
        self.assertIsNone(database.claim_next_system_job("comment_draft"))

    def test_sync_ignores_publication_without_channel(self):
        database.save_video_publication(
            video_id=self.video_id,
            youtube_channel_id=None,
            youtube_video_id="unassigned-sync",
            published_url="https://www.youtube.com/watch?v=unassigned-sync",
        )
        sync_job = main._create_comment_system_job(
            "comment_sync",
            title="Channel A",
            payload={"channel_id": self.channel["id"]},
        )
        claimed = database.claim_next_system_job("comment_sync")
        remote_comment = {
            "comment_id": "unassigned-comment",
            "thread_id": "unassigned-thread",
            "youtube_video_id": "unassigned-sync",
            "author_channel_id": "UC-viewer",
            "text": "Must be ignored",
        }

        with (
            patch.object(
                main,
                "_get_youtube_access_token",
                return_value=(self.channel, "access-token"),
            ),
            patch.object(
                youtube_comments,
                "list_channel_comment_threads",
                return_value=[remote_comment],
            ),
        ):
            main._execute_comment_sync_job(claimed)

        finished_job = database.get_system_job(sync_job["id"])
        self.assertEqual(database.get_youtube_comments(["unassigned-comment"]), [])
        self.assertEqual(finished_job["status"], "done")
        self.assertEqual(finished_job["result"]["ignored"], 1)

    def test_scheduler_respects_minimum_interval_for_every_channel(self):
        database.update_youtube_channel(
            self.channel["id"],
            reply_window_start="00:00",
            reply_window_end="00:00",
            reply_interval_minutes=5,
        )
        now = datetime.datetime(2026, 9, 11, 3, 0, tzinfo=datetime.timezone.utc)
        database.upsert_youtube_comment(
            self.publication["id"],
            {
                "comment_id": "already-replied",
                "text": "Một câu hỏi trước",
                "published_at": "2026-09-11T01:00:00+00:00",
                "existing_reply_id": "reply-1",
                "existing_reply_text": "Câu trả lời trước",
                "existing_reply_published_at": (
                    now - datetime.timedelta(minutes=1)
                ).isoformat(),
            },
        )
        comment = database.get_youtube_comments(["comment-1"])[0]
        channel = database.get_youtube_channel(self.channel["id"])

        publish_at, reason = main._next_comment_publish_time(comment, channel, now)

        self.assertEqual(publish_at, now + datetime.timedelta(minutes=4))
        self.assertIn("giãn cách", reason)

    def test_future_retry_job_does_not_block_a_new_due_job(self):
        first = database.create_system_job(
            "publish-future",
            "comment_publish",
            "Future",
            {"comment_ids": ["comment-1"]},
        )
        database.update_system_job(
            first["id"],
            status="retry_wait",
            next_retry_at=(
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(hours=1)
            ).isoformat(),
        )
        database.create_system_job(
            "publish-now",
            "comment_publish",
            "Now",
            {"comment_ids": ["comment-1"]},
        )

        claimed = database.claim_next_system_job("comment_publish")

        self.assertEqual(claimed["id"], "publish-now")

    def test_automatic_sync_failure_is_backed_off_in_one_durable_job(self):
        job = main._create_comment_system_job(
            "comment_sync",
            title="Channel A",
            payload={"channel_id": self.channel["id"], "automatic": True},
        )

        with (
            patch.object(
                main,
                "_execute_comment_sync_job",
                side_effect=RuntimeError("temporary YouTube failure"),
            ),
            patch.object(
                main.security_logging,
                "safe_user_error",
                return_value="Tác vụ bình luận thất bại. Mã lỗi: test.",
            ),
        ):
            main._comment_queue_worker()

        saved = database.get_system_job(job["id"])
        self.assertEqual(saved["status"], "retry_wait")
        self.assertEqual(saved["recovery_count"], 1)
        self.assertIn("đồng bộ bình luận YouTube", saved["progress"])
        self.assertIsNone(database.claim_next_system_job("comment_sync"))

    def test_expired_login_starts_automatic_login_for_comment_job(self):
        job = main._create_comment_system_job(
            "comment_draft",
            title="Comment login",
            payload={"comment_ids": ["comment-1"]},
        )
        login_error = ChatGPTAttentionRequiredError(
            CHATGPT_LOGIN_REQUIRED_MESSAGE
        )

        with (
            patch.object(
                main,
                "_execute_comment_draft_job",
                side_effect=login_error,
            ),
            patch.object(
                main,
                "_schedule_automatic_chatgpt_login",
                return_value=True,
            ) as schedule_login,
        ):
            main._comment_queue_worker()

        saved = database.get_system_job(job["id"])
        self.assertEqual(saved["status"], "paused")
        self.assertEqual(saved["result"]["automatic_login"], "pending")
        schedule_login.assert_called_once_with(job["id"], login_error)

    def test_future_sync_retry_does_not_block_another_due_channel(self):
        future = database.create_system_job(
            "sync-future",
            "comment_sync",
            "Future channel",
            {"channel_id": self.channel["id"], "automatic": True},
        )
        database.update_system_job(
            future["id"],
            status="retry_wait",
            next_retry_at=(
                datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(hours=1)
            ).isoformat(),
        )
        database.create_system_job(
            "sync-now",
            "comment_sync",
            "Due channel",
            {"channel_id": 999, "automatic": True},
        )

        claimed = database.claim_next_system_job("comment_sync")

        self.assertEqual(claimed["id"], "sync-now")

    def test_legacy_import_job_creates_one_chat_and_persists_context(self):
        reservation = database.reserve_comment_import_video(
            youtube_channel_id=self.channel["id"],
            youtube_video_id="legacy-job",
            published_url="https://www.youtube.com/watch?v=legacy-job",
            title="Legacy job title",
            description="Legacy description",
            published_at="",
            prompt_version="prompt-a",
        )
        video_id = reservation["video"]["id"]
        job = main._create_comment_system_job(
            "comment_video_import",
            title="Legacy job title",
            payload={
                "published_url": "https://www.youtube.com/watch?v=legacy-job",
                "description": "Legacy description",
            },
            video_id=video_id,
            prompt_version="prompt-a",
        )
        claimed = database.claim_next_system_job("comment_video_import")
        expected_chat = "https://chatgpt.com/g/g-p-1234567890abcdef1234567890abcdef-x/c/chat-1"

        with (
            patch.object(main, "get_video_transcript", return_value="Full transcript"),
            patch.object(main, "_try_start_chatgpt_operation", return_value=True),
            patch.object(main, "_finish_chatgpt_operation"),
            patch(
                "auto_yt.services.chatgpt_worker.initialize_comment_video_chat",
                return_value=expected_chat,
            ) as initialize_chat,
        ):
            completed = main._execute_comment_video_import_job(claimed)

        self.assertTrue(completed)
        saved_video = database.get_video(video_id)
        self.assertEqual(saved_video["transcript"], "Full transcript")
        self.assertEqual(saved_video["chat_url"], expected_chat)
        self.assertEqual(database.get_system_job(job["id"])["status"], "done")
        initialize_chat.assert_called_once()

    def test_bulk_import_reuses_a_manually_added_video_by_youtube_id(self):
        request = main.CommentVideoImportRequest(
            channel_id=self.channel["id"],
            prompt_version="prompt-a",
            youtube_video_ids=["source123"],
        )
        remote = {
            "youtube_video_id": "source123",
            "channel_id": "UC123",
            "title": "Remote title",
            "description": "Remote description",
            "published_at": "",
        }
        with (
            patch.object(
                main,
                "_read_prompts_config",
                return_value={
                    "active_version": "prompt-a",
                    "versions": {
                        "prompt-a": {
                            "name": "Prompt A",
                            "default_youtube_channel_id": "UC123",
                        }
                    },
                },
            ),
            patch.object(
                main,
                "_get_youtube_access_token",
                return_value=(self.channel, "token"),
            ),
            patch.object(
                main.youtube_comments,
                "get_videos_details",
                return_value=[remote],
            ),
        ):
            result = main.start_comment_video_import(request)

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["linked"], 1)
        self.assertEqual(result["already_ready"], 1)
        self.assertEqual(result["queued"], 0)
        self.assertEqual(database.get_video(self.video_id)["chat_url"], (
            "https://chatgpt.com/c/12345678-1234-1234-1234-123456789abc"
        ))


class YouTubeOAuthIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.oauth_path_patch = patch.object(
            youtube_comments,
            "OAUTH_CONFIG_PATH",
            Path(self.temp_directory.name) / "youtube_oauth.json",
        )
        self.oauth_path_patch.start()

    def tearDown(self):
        self.oauth_path_patch.stop()
        self.temp_directory.cleanup()

    def test_saving_a_new_client_preserves_the_legacy_client(self):
        youtube_comments.OAUTH_CONFIG_PATH.write_text(
            json.dumps({
                "client_id": "client-old",
                "client_secret_encrypted": "enc:old-secret",
                "redirect_uri": youtube_comments.DEFAULT_REDIRECT_URI,
            }),
            encoding="utf-8",
        )

        with (
            patch.object(
                youtube_comments,
                "encrypt_secret",
                side_effect=lambda value: f"enc:{value}",
            ),
            patch.object(
                youtube_comments,
                "decrypt_secret",
                side_effect=lambda value: value.removeprefix("enc:"),
            ),
        ):
            saved = youtube_comments.save_oauth_config(
                "client-new",
                "new-secret",
                youtube_comments.DEFAULT_REDIRECT_URI,
                "OAuth GKVS",
            )
            old_config = youtube_comments.load_oauth_config("client-old")
            new_config = youtube_comments.load_oauth_config("client-new")

        raw_store = json.loads(
            youtube_comments.OAUTH_CONFIG_PATH.read_text(encoding="utf-8")
        )
        self.assertEqual(saved["client_id"], "client-new")
        self.assertEqual(saved["client_name"], "OAuth GKVS")
        self.assertEqual(raw_store["active_client_id"], "client-new")
        self.assertEqual(set(raw_store["clients"]), {"client-old", "client-new"})
        self.assertEqual(old_config["client_secret"], "old-secret")
        self.assertEqual(new_config["client_secret"], "new-secret")

    def test_refresh_uses_the_client_permanently_bound_to_the_channel(self):
        channel = {
            "id": 7,
            "oauth_client_id": "client-a",
            "access_token_encrypted": "expired-access",
            "refresh_token_encrypted": "refresh-token",
            "token_expiry": "2000-01-01T00:00:00+00:00",
        }
        persisted = []
        with (
            patch.object(
                youtube_comments,
                "decrypt_secret",
                side_effect=lambda value: value,
            ),
            patch.object(
                youtube_comments,
                "encrypt_secret",
                side_effect=lambda value: f"enc:{value}",
            ),
            patch.object(
                youtube_comments,
                "load_oauth_config",
                return_value={
                    "client_id": "client-a",
                    "client_secret": "secret-a",
                    "redirect_uri": youtube_comments.DEFAULT_REDIRECT_URI,
                },
            ) as load_config,
            patch.object(
                youtube_comments,
                "refresh_access_token",
                return_value={"access_token": "fresh-access", "expires_in": 3600},
            ) as refresh,
        ):
            token = youtube_comments.access_token_for_channel(
                channel,
                lambda *values: persisted.append(values),
            )

        self.assertEqual(token, "fresh-access")
        load_config.assert_called_once_with("client-a")
        self.assertEqual(refresh.call_args.kwargs["config"]["client_id"], "client-a")
        self.assertEqual(persisted[0][3], "client-a")

    def test_legacy_channel_discovers_and_persists_matching_client(self):
        channel = {
            "id": 8,
            "oauth_client_id": "",
            "access_token_encrypted": "expired-access",
            "refresh_token_encrypted": "refresh-token",
            "token_expiry": "2000-01-01T00:00:00+00:00",
        }
        configs = [
            {"client_id": "client-wrong", "client_secret": "wrong"},
            {"client_id": "client-right", "client_secret": "right"},
        ]
        persisted = []

        def refresh(_refresh_token, *, config, **_kwargs):
            if config["client_id"] == "client-wrong":
                raise youtube_comments.OAuthTokenRefreshError("unauthorized_client")
            return {"access_token": "fresh-access", "expires_in": 3600}

        with (
            patch.object(youtube_comments, "decrypt_secret", side_effect=lambda value: value),
            patch.object(
                youtube_comments,
                "encrypt_secret",
                side_effect=lambda value: f"enc:{value}",
            ),
            patch.object(youtube_comments, "iter_oauth_configs", return_value=configs),
            patch.object(youtube_comments, "refresh_access_token", side_effect=refresh),
        ):
            token = youtube_comments.access_token_for_channel(
                channel,
                lambda *values: persisted.append(values),
            )

        self.assertEqual(token, "fresh-access")
        self.assertEqual(persisted[0][3], "client-right")


class YouTubeOAuthDatabaseBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_patch = patch.object(
            database,
            "DB_PATH",
            Path(self.temp_directory.name) / "database.db",
        )
        self.database_patch.start()
        database.init_db()

    def tearDown(self):
        self.database_patch.stop()
        self.temp_directory.cleanup()

    def test_reconnect_without_new_refresh_token_cannot_replace_client_binding(self):
        channel = database.save_youtube_channel(
            channel_id="UC-isolated",
            title="Isolated channel",
            refresh_token_encrypted="refresh-a",
            oauth_client_id="client-a",
        )
        database.save_youtube_channel(
            channel_id="UC-isolated",
            title="Isolated channel",
            access_token_encrypted="access-b",
            refresh_token_encrypted="",
            oauth_client_id="client-b",
        )

        stored = database.get_youtube_channel(channel["id"], include_tokens=True)
        self.assertEqual(stored["refresh_token_encrypted"], "refresh-a")
        self.assertEqual(stored["oauth_client_id"], "client-a")


class YouTubeOAuthStateTests(unittest.TestCase):
    def test_start_keeps_other_live_states_and_captures_client_snapshot(self):
        main._youtube_oauth_states.clear()
        main._youtube_oauth_states["other-state"] = {
            "expires_at": main.time.time() + 60,
            "oauth_config": {"client_id": "client-other"},
        }
        config = {
            "client_id": "client-a",
            "client_secret": "secret-a",
            "redirect_uri": youtube_comments.DEFAULT_REDIRECT_URI,
        }
        with (
            patch.object(youtube_comments, "load_oauth_config", return_value=config),
            patch.object(youtube_comments, "create_oauth_state", return_value="new-state"),
            patch.object(youtube_comments, "create_pkce_pair", return_value=("verifier", "challenge")),
            patch.object(
                youtube_comments,
                "build_authorization_url",
                return_value="https://accounts.google.com/oauth",
            ) as build_url,
            patch.object(
                main.gpm_service,
                "get_gpm_profile_detail",
                return_value={
                    "name": "OAuth profile",
                    "raw_proxy": "127.0.0.1:8899:user:pass",
                },
            ),
        ):
            response = main.start_youtube_oauth(
                client_id="client-a", gpm_profile_id="gpm-oauth"
            )

        self.assertEqual(response["authorization_url"], "https://accounts.google.com/oauth")
        self.assertIn("other-state", main._youtube_oauth_states)
        self.assertEqual(
            main._youtube_oauth_states["new-state"]["oauth_config"]["client_id"],
            "client-a",
        )
        self.assertEqual(build_url.call_args.kwargs["config"]["client_id"], "client-a")
        self.assertNotIn("gpm_proxy_info", response)
        main._youtube_oauth_states.clear()

    def test_reconnect_state_is_locked_to_the_expected_channel(self):
        main._youtube_oauth_states.clear()
        config = {
            "client_id": "client-a",
            "client_secret": "secret-a",
            "redirect_uri": youtube_comments.DEFAULT_REDIRECT_URI,
        }
        with (
            patch.object(youtube_comments, "load_oauth_config", return_value=config),
            patch.object(youtube_comments, "create_oauth_state", return_value="locked-state"),
            patch.object(youtube_comments, "create_pkce_pair", return_value=("verifier", "challenge")),
            patch.object(
                youtube_comments,
                "build_authorization_url",
                return_value="https://accounts.google.com/oauth",
            ),
            patch.object(
                database,
                "get_youtube_channel_by_channel_id",
                return_value={
                    "channel_id": "UC-target",
                    "title": "Target channel",
                    "gpm_profile_id": "gpm-target",
                    "gpm_profile_name": "Target profile",
                    "gpm_proxy_info": "127.0.0.1:8899:user:pass",
                },
            ),
        ):
            main.start_youtube_oauth(
                client_id="client-a",
                expected_channel_id="UC-target",
            )

        state = main._youtube_oauth_states["locked-state"]
        self.assertEqual(state["expected_channel_id"], "UC-target")
        self.assertEqual(state["expected_channel_title"], "Target channel")
        main._youtube_oauth_states.clear()

    def test_reconnect_rejects_a_different_authenticated_channel(self):
        main._youtube_oauth_states.clear()
        main._youtube_oauth_states["locked-state"] = {
            "expires_at": main.time.time() + 60,
            "code_verifier": "verifier",
            "oauth_config": {
                "client_id": "client-a",
                "client_secret": "secret-a",
                "redirect_uri": youtube_comments.DEFAULT_REDIRECT_URI,
            },
            "expected_channel_id": "UC-target",
            "expected_channel_title": "Target channel",
        }
        with (
            patch.object(
                youtube_comments,
                "exchange_authorization_code",
                return_value={"access_token": "access", "refresh_token": "refresh"},
            ),
            patch.object(
                youtube_comments,
                "get_authenticated_channels",
                return_value=[{"channel_id": "UC-other", "title": "Other channel"}],
            ),
            patch.object(database, "save_youtube_channel") as save_channel,
            patch.object(main.security_logging, "report_exception", return_value="oauth-test"),
        ):
            response = main.finish_youtube_oauth(code="code", state="locked-state")

        self.assertEqual(response.status_code, 400)
        self.assertIn("oauth-test", response.body.decode("utf-8"))
        save_channel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
