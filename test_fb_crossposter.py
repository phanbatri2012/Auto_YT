import datetime
import io
import json
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
import imageio_ffmpeg

sys.path.insert(0, str(Path(__file__).parent / "src"))

import auto_yt.services.database as db
from auto_yt.services import fb_crossposter_service


class FBCrossPosterUnitTests(unittest.TestCase):
    _original_db_path = None
    _test_db_path = None

    @classmethod
    def setUpClass(cls):
        cls._original_db_path = db.DB_PATH
        cls._test_db_path = Path(__file__).parent / "data" / "test_crossposter_temp.db"
        if cls._test_db_path.exists():
            cls._test_db_path.unlink()
        db.DB_PATH = cls._test_db_path
        db.init_db()

    @classmethod
    def tearDownClass(cls):
        db.DB_PATH = cls._original_db_path
        if cls._test_db_path and cls._test_db_path.exists():
            try:
                cls._test_db_path.unlink()
            except Exception:
                pass

    def setUp(self):
        # Clear queue before each test
        db.clear_fb_crossposter_queue(only_pending=False)

    def test_sanitize_description(self):
        raw = """
        Xin chào các bạn đến với video này!
        00:00 - Giới thiệu
        01:25 - Hướng dẫn chi tiết
        Đăng ký kênh YouTube tại https://www.youtube.com/@ChannelName để xem thêm.
        Link video khác: https://youtu.be/abcxyz123
        Ủng hộ chúng mình tại website: https://example.com/donate
        """
        cleaned = fb_crossposter_service.sanitize_description(raw)
        self.assertNotIn("youtube.com", cleaned)
        self.assertNotIn("youtu.be", cleaned)
        self.assertNotIn("00:00", cleaned)
        self.assertNotIn("01:25", cleaned)
        self.assertIn("Xin chào các bạn", cleaned)
        self.assertIn("https://example.com/donate", cleaned)

    def test_format_hashtags(self):
        tags = ["python programming", "auto yt", "AI Video", "antigravity!"]
        hashtags = fb_crossposter_service.format_hashtags(tags)
        self.assertIn("#PythonProgramming", hashtags)
        self.assertIn("#AutoYt", hashtags)
        self.assertIn("#AiVideo", hashtags)
        self.assertIn("#Antigravity", hashtags)

    def test_build_fb_caption(self):
        title = "Tập 1: Bắt đầu lập trình"
        raw_desc = "Mô tả tập 1 https://youtube.com/watch?v=123"
        tags = ["python", "tutorial"]
        template = "🎬 {title}\n\n{clean_description}\n\n{hashtags}"
        caption = fb_crossposter_service.build_fb_caption(
            title=title,
            raw_description=raw_desc,
            tags=tags,
            template=template
        )
        self.assertIn("🎬 Tập 1: Bắt đầu lập trình", caption)
        self.assertIn("Mô tả tập 1", caption)
        self.assertNotIn("https://youtube.com", caption)
        self.assertIn("#Python", caption)

    def test_caption_removes_duplicate_title_header_and_youtube_url(self):
        title = "Lịch sử Việt Nam"
        raw_desc = (
            "Lịch sử Việt Nam\n"
            "Nội dung mô tả video:\n"
            "Nội dung chính.\n"
            "00:00 - Mở đầu\n"
            "https://youtube.com/watch?v=abc"
        )
        caption = fb_crossposter_service.build_fb_caption(
            title,
            raw_desc,
            ["lịch sử Việt Nam"],
            "{title}\n\n{clean_description}\n\n{youtube_url}\n\n{hashtags}",
            "https://youtube.com/watch?v=abc",
        )
        self.assertEqual(caption.count(title), 1)
        self.assertNotIn("Nội dung mô tả video", caption)
        self.assertNotIn("00:00", caption)
        self.assertNotIn("youtube.com", caption)
        self.assertIn("Nội dung chính.", caption)

    def test_default_tags_are_prioritized_and_deduplicated(self):
        effective = fb_crossposter_service.get_effective_tags(
            ["Lịch sử", "Tag nguồn", "Tag cuối"],
            raw_description="#TagMoTa #LichSuKhac",
            max_count=5,
            default_tags=["Thương hiệu", "lịch sử", "#Thương Hiệu"],
        )

        self.assertEqual(
            effective,
            ["Thương hiệu", "lịch sử", "TagMoTa", "LichSuKhac", "Tag nguồn"],
        )

    def test_manual_caption_only_appends_missing_default_hashtags(self):
        caption = fb_crossposter_service.append_missing_default_hashtags(
            "Nội dung chỉnh tay\n\n#ThuongHieu",
            ["ThuongHieu", "Lịch sử Việt Nam"],
        )

        self.assertEqual(caption.count("#ThuongHieu"), 1)
        self.assertIn("#LịchSửViệtNam", caption)
        self.assertTrue(caption.startswith("Nội dung chỉnh tay"))

    def test_manual_caption_is_preserved_when_queue_metadata_is_refreshed(self):
        db.upsert_fb_crossposter_queue_items([{
            "youtube_id": "manual_caption",
            "original_title": "Tiêu đề cũ",
            "fb_description": "Caption tự động cũ",
            "fb_description_source": "auto",
        }])
        item = db.get_fb_crossposter_queue()["items"][0]
        db.update_fb_crossposter_queue_item(item["id"], {
            "fb_description": "Caption người dùng",
            "fb_description_source": "manual",
        })
        db.upsert_fb_crossposter_queue_items([{
            "youtube_id": "manual_caption",
            "original_title": "Tiêu đề mới",
            "original_description": "Mô tả đầy đủ",
            "original_tags": ["tag đầy đủ"],
            "fb_description": "Caption tự động mới",
            "fb_description_source": "auto",
        }])
        updated = db.get_fb_crossposter_queue_item(item["id"])
        self.assertEqual(updated["fb_description"], "Caption người dùng")
        self.assertEqual(updated["fb_description_source"], "manual")
        self.assertEqual(updated["original_description"], "Mô tả đầy đủ")
        self.assertEqual(updated["original_tags"], ["tag đầy đủ"])

    @patch("auto_yt.services.fb_crossposter_service._get_proxy_for_gpm_profile", return_value=None)
    @patch("auto_yt.services.fb_crossposter_service._build_urllib_opener")
    def test_content_tags_are_resolved_in_one_batch(self, mock_build_opener, _mock_proxy):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "data": [
                {"name": "Đồ họa 3D", "valid": True, "id": "101"},
                {"name": "không hợp lệ", "valid": False},
            ]
        }).encode("utf-8")
        response.__enter__.return_value = response
        mock_build_opener.return_value.open.return_value = response

        tag_ids, skipped = fb_crossposter_service.resolve_content_tag_ids(
            ["Đồ họa 3D", "không hợp lệ", "Đồ họa 3D"],
            "token",
        )

        self.assertEqual(tag_ids, ["101"])
        self.assertEqual(skipped, ["không hợp lệ"])
        request = mock_build_opener.return_value.open.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertNotIn("access_token", query)
        self.assertEqual(request.get_header("Authorization"), "Bearer token")
        self.assertEqual(query["type"], ["adinterestvalid"])
        self.assertEqual(
            json.loads(query["interest_list"][0]),
            ["Đồ họa 3D", "không hợp lệ"],
        )

    @patch("auto_yt.services.fb_crossposter_service._get_proxy_for_gpm_profile", return_value=None)
    @patch("auto_yt.services.fb_crossposter_service._build_urllib_opener")
    def test_content_tag_lookup_failure_does_not_abort(self, mock_build_opener, _mock_proxy):
        mock_build_opener.return_value.open.side_effect = OSError("network down")
        tag_ids, skipped = fb_crossposter_service.resolve_content_tag_ids(
            ["tag one", "tag two"],
            "token",
        )
        self.assertEqual(tag_ids, [])
        self.assertEqual(skipped, ["tag one", "tag two"])

    @patch("auto_yt.services.fb_crossposter_service._get_proxy_for_gpm_profile", return_value="http://proxy:8080")
    @patch("auto_yt.services.fb_crossposter_service._build_urllib_opener")
    def test_thumbnail_download_uses_source_proxy_and_normalizes_jpeg(
        self, mock_build_opener, _mock_proxy
    ):
        source_bytes = io.BytesIO()
        Image.new("RGBA", (20, 10), (255, 0, 0, 128)).save(source_bytes, format="PNG")
        response = MagicMock()
        response.read.return_value = source_bytes.getvalue()
        response.__enter__.return_value = response
        mock_build_opener.return_value.open.return_value = response

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "thumb.jpg"
            success = fb_crossposter_service.download_thumbnail(
                "https://i.ytimg.com/test.png",
                output,
                source_gpm_profile_id="source-profile",
            )
            self.assertTrue(success)
            self.assertTrue(output.read_bytes().startswith(b"\xff\xd8"))
        mock_build_opener.assert_called_once_with("http://proxy:8080")

    def test_thumbnail_conversion_creates_vertical_blurred_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "thumb.jpg"
            Image.new("RGB", (1600, 900), (230, 20, 20)).save(source, format="JPEG")

            result = fb_crossposter_service.convert_thumbnail_to_vertical(source)

            self.assertEqual(result, source)
            with Image.open(result) as converted:
                self.assertEqual(converted.size, (1080, 1920))
                self.assertEqual(converted.mode, "RGB")

    def test_video_conversion_creates_vertical_mp4_with_audio(self):
        ffmpeg_executable = (
            Path(fb_crossposter_service.ensure_ffmpeg_directory()) / "ffmpeg.exe"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.mp4"
            output = Path(temp_dir) / "vertical.mp4"
            generated = subprocess.run(
                [
                    str(ffmpeg_executable),
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=320x180:r=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:sample_rate=44100",
                    "-t",
                    "0.5",
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)

            fb_crossposter_service.convert_video_to_vertical(source, output)

            frames = imageio_ffmpeg.read_frames(str(output), pix_fmt="rgb24")
            metadata = next(frames)
            frames.close()
            self.assertEqual(metadata["size"], (1080, 1920))
            audio_check = subprocess.run(
                [
                    str(ffmpeg_executable),
                    "-v",
                    "error",
                    "-i",
                    str(output),
                    "-map",
                    "0:a:0",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(audio_check.returncode, 0, audio_check.stderr)

    def test_upsert_queue_and_deduplication(self):
        items = [
            {
                "youtube_id": "yt_video_001",
                "youtube_url": "https://youtube.com/watch?v=yt_video_001",
                "original_title": "Video Cũ Nhất 1",
                "original_description": "Mô tả 1",
                "original_tags": ["tag1"],
                "thumbnail_url": "https://img.youtube.com/1.jpg",
                "youtube_upload_date": "20230101",
                "sort_order": 1
            },
            {
                "youtube_id": "yt_video_002",
                "youtube_url": "https://youtube.com/watch?v=yt_video_002",
                "original_title": "Video Cũ Nhất 2",
                "original_description": "Mô tả 2",
                "original_tags": ["tag2"],
                "thumbnail_url": "https://img.youtube.com/2.jpg",
                "youtube_upload_date": "20230201",
                "sort_order": 2
            }
        ]

        # First sync
        res1 = db.upsert_fb_crossposter_queue_items(items)
        self.assertEqual(res1["inserted"], 2)
        self.assertEqual(res1["existing"], 0)

        # Second sync with 1 new item and 2 existing items
        items_extended = items + [{
            "youtube_id": "yt_video_003",
            "youtube_url": "https://youtube.com/watch?v=yt_video_003",
            "original_title": "Video Mới 3",
            "original_description": "Mô tả 3",
            "original_tags": ["tag3"],
            "thumbnail_url": "https://img.youtube.com/3.jpg",
            "youtube_upload_date": "20230301",
            "sort_order": 3
        }]
        res2 = db.upsert_fb_crossposter_queue_items(items_extended)
        self.assertEqual(res2["inserted"], 1)
        self.assertEqual(res2["existing"], 2)

        stats = db.get_fb_crossposter_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["pending"], 3)

    def test_recalculate_schedule(self):
        items = [
            {"youtube_id": f"yt_{i}", "original_title": f"Video {i}", "sort_order": i}
            for i in range(1, 5)
        ]
        db.upsert_fb_crossposter_queue_items(items)

        # Recalculate: 2 videos/day at 11:30 and 19:30, starting tomorrow
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        updated = db.recalculate_fb_queue_schedule(
            daily_quota=2,
            times_list=["11:30", "19:30"],
            start_date=tomorrow
        )
        self.assertEqual(updated, 4)

        queue = db.get_fb_crossposter_queue(page=1, page_size=10)
        items_result = queue["items"]
        self.assertEqual(len(items_result), 4)

        # First 2 items should be on tomorrow
        ts1 = items_result[0]["scheduled_publish_time"]
        ts2 = items_result[1]["scheduled_publish_time"]
        dt1 = datetime.datetime.fromtimestamp(ts1)
        dt2 = datetime.datetime.fromtimestamp(ts2)

        self.assertEqual(dt1.date(), tomorrow)
        self.assertEqual(dt2.date(), tomorrow)
        self.assertEqual(dt1.strftime("%H:%M"), "11:30")
        self.assertEqual(dt2.strftime("%H:%M"), "19:30")

        # Next 2 items should be on day after tomorrow
        day_after = tomorrow + datetime.timedelta(days=1)
        ts3 = items_result[2]["scheduled_publish_time"]
        dt3 = datetime.datetime.fromtimestamp(ts3)
        self.assertEqual(dt3.date(), day_after)
        self.assertEqual(dt3.strftime("%H:%M"), "11:30")

    def test_recalculate_schedule_collision_prevention(self):
        """Test that already occupied/published slots are skipped when recalculating schedule."""
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        day_after = tomorrow + datetime.timedelta(days=1)

        t1 = datetime.datetime.combine(tomorrow, datetime.time(11, 30))
        t2 = datetime.datetime.combine(tomorrow, datetime.time(19, 30))

        # 1. Existing items that are already published to Meta Cloud tomorrow
        items_existing = [
            {
                "youtube_id": "yt_published_1",
                "original_title": "Published 1",
                "sort_order": 1,
            },
            {
                "youtube_id": "yt_published_2",
                "original_title": "Published 2",
                "sort_order": 2,
            }
        ]
        db.upsert_fb_crossposter_queue_items(items_existing)
        q1 = db.get_fb_crossposter_queue()
        id1 = q1["items"][0]["id"]
        id2 = q1["items"][1]["id"]
        db.update_fb_crossposter_queue_item(id1, {
            "status": "published",
            "fb_post_id": "fb_post_111",
            "scheduled_publish_time": int(t1.timestamp())
        })
        db.update_fb_crossposter_queue_item(id2, {
            "status": "published",
            "fb_post_id": "fb_post_222",
            "scheduled_publish_time": int(t2.timestamp())
        })

        # 2. Add 2 new pending items
        items_new = [
            {"youtube_id": "yt_new_3", "original_title": "New Pending 3", "sort_order": 3},
            {"youtube_id": "yt_new_4", "original_title": "New Pending 4", "sort_order": 4},
        ]
        db.upsert_fb_crossposter_queue_items(items_new)

        # 3. Recalculate schedule starting from tomorrow
        updated = db.recalculate_fb_queue_schedule(
            daily_quota=2,
            times_list=["11:30", "19:30"],
            start_date=tomorrow
        )
        self.assertEqual(updated, 2)

        # Verify new items were skipped to day_after because tomorrow's slots were occupied!
        q2 = db.get_fb_crossposter_queue(page=1, page_size=10)
        items_by_id = {it["youtube_id"]: it for it in q2["items"]}

        new3_ts = items_by_id["yt_new_3"]["scheduled_publish_time"]
        new4_ts = items_by_id["yt_new_4"]["scheduled_publish_time"]
        dt3 = datetime.datetime.fromtimestamp(new3_ts)
        dt4 = datetime.datetime.fromtimestamp(new4_ts)

        self.assertEqual(dt3.date(), day_after)
        self.assertEqual(dt3.strftime("%H:%M"), "11:30")
        self.assertEqual(dt4.date(), day_after)
        self.assertEqual(dt4.strftime("%H:%M"), "19:30")

    def test_queue_status_actions(self):
        item = {
            "youtube_id": "yt_action_test",
            "original_title": "Action Test Video",
            "sort_order": 1
        }
        db.upsert_fb_crossposter_queue_items([item])
        q = db.get_fb_crossposter_queue()
        item_id = q["items"][0]["id"]

        # Skip
        db.update_fb_crossposter_queue_item(item_id, {"status": "skipped"})
        updated = db.get_fb_crossposter_queue_item(item_id)
        self.assertEqual(updated["status"], "skipped")

        # Edit title and caption
        db.update_fb_crossposter_queue_item(item_id, {
            "fb_title": "Tiêu đề Facebook mới",
            "fb_description": "Nội dung caption Facebook mới"
        })
        updated = db.get_fb_crossposter_queue_item(item_id)
        self.assertEqual(updated["fb_title"], "Tiêu đề Facebook mới")
        self.assertEqual(updated["fb_description"], "Nội dung caption Facebook mới")

        # Delete
        db.delete_fb_crossposter_queue_item(item_id)
        self.assertIsNone(db.get_fb_crossposter_queue_item(item_id))

    def test_multi_page_campaign_isolation(self):
        # Fanpage A
        items_a = [
            {"youtube_id": "yt_a1", "original_title": "Video A1", "sort_order": 1, "target_page_id": "page_A"},
            {"youtube_id": "yt_a2", "original_title": "Video A2", "sort_order": 2, "target_page_id": "page_A"},
        ]
        db.upsert_fb_crossposter_queue_items(items_a, target_page_id="page_A")
        db.save_fb_crossposter_settings({
            "target_fb_page_id": "page_A",
            "target_fb_page_name": "Fanpage A Gaming",
            "daily_quota": 2,
            "lead_time_minutes": 60,
            "convert_to_vertical": True,
            "default_tags": ["Fanpage A", "#Lịch sử", "fanpage a"],
        }, page_id="page_A")

        # Fanpage B
        items_b = [
            {"youtube_id": "yt_b1", "original_title": "Video B1", "sort_order": 1, "target_page_id": "page_B"},
        ]
        db.upsert_fb_crossposter_queue_items(items_b, target_page_id="page_B")
        db.save_fb_crossposter_settings({
            "target_fb_page_id": "page_B",
            "target_fb_page_name": "Fanpage B Music",
            "daily_quota": 1,
            "lead_time_minutes": 45,
            "convert_to_vertical": False,
            "default_tags": ["Fanpage B"],
        }, page_id="page_B")

        # Verify campaigns list
        campaigns = db.list_all_crossposter_campaigns()
        page_ids = [c["page_id"] for c in campaigns]
        self.assertIn("page_A", page_ids)
        self.assertIn("page_B", page_ids)

        # Verify isolated queues
        q_a = db.get_fb_crossposter_queue(target_page_id="page_A")
        self.assertEqual(q_a["total"], 2)
        self.assertEqual(q_a["items"][0]["target_page_id"], "page_A")

        q_b = db.get_fb_crossposter_queue(target_page_id="page_B")
        self.assertEqual(q_b["total"], 1)
        self.assertEqual(q_b["items"][0]["target_page_id"], "page_B")

        # Verify settings
        set_a = db.get_fb_crossposter_settings("page_A")
        self.assertEqual(set_a["target_fb_page_name"], "Fanpage A Gaming")
        self.assertEqual(set_a["lead_time_minutes"], 60)
        self.assertTrue(set_a["convert_to_vertical"])
        self.assertEqual(set_a["default_tags"], ["Fanpage A", "Lịch sử"])

        set_b = db.get_fb_crossposter_settings("page_B")
        self.assertEqual(set_b["target_fb_page_name"], "Fanpage B Music")
        self.assertEqual(set_b["lead_time_minutes"], 45)
        self.assertFalse(set_b["convert_to_vertical"])
        self.assertEqual(set_b["default_tags"], ["Fanpage B"])

    def test_page_access_token_is_encrypted_and_write_only(self):
        token = "EAATestSecretToken1234567890"
        db.save_fb_crossposter_settings({
            "target_fb_page_id": "secure_page",
            "target_fb_page_name": "Secure Page",
            "target_access_token": token,
        }, page_id="secure_page")

        safe_settings = db.get_fb_crossposter_settings("secure_page")
        self.assertNotIn("target_access_token", safe_settings)
        self.assertNotIn("target_access_token_encrypted", safe_settings)
        self.assertTrue(safe_settings["target_access_token_configured"])

        runtime_settings = db.get_fb_crossposter_runtime_settings("secure_page")
        self.assertEqual(runtime_settings["target_access_token"], token)

        with db.sqlite3.connect(str(db.DB_PATH)) as conn:
            stored = conn.execute(
                "SELECT target_access_token, target_access_token_encrypted "
                "FROM fb_crossposter_settings WHERE target_fb_page_id = ?",
                ("secure_page",),
            ).fetchone()
        self.assertEqual(stored[0], "")
        self.assertTrue(stored[1].startswith("dpapi:"))

        db.save_fb_crossposter_settings({
            "target_fb_page_id": "secure_page",
            "target_fb_page_name": "Renamed Secure Page",
        }, page_id="secure_page")
        self.assertEqual(
            db.get_fb_crossposter_runtime_settings("secure_page")["target_access_token"],
            token,
        )
        campaign = next(
            item
            for item in db.list_all_crossposter_campaigns()
            if item["page_id"] == "secure_page"
        )
        self.assertNotIn("target_access_token", campaign)
        self.assertNotIn("target_access_token_encrypted", campaign)
        self.assertTrue(campaign["target_access_token_configured"])

        missing_page = db.get_fb_crossposter_runtime_settings("missing_page")
        self.assertEqual(missing_page["target_access_token"], "")

    def test_legacy_plaintext_page_token_is_migrated(self):
        token = "EAALegacyToken1234567890"
        with db.sqlite3.connect(str(db.DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO fb_crossposter_settings "
                "(target_fb_page_id, target_access_token) VALUES (?, ?)",
                ("legacy_page", token),
            )
        db.init_db()

        with db.sqlite3.connect(str(db.DB_PATH)) as conn:
            stored = conn.execute(
                "SELECT target_access_token, target_access_token_encrypted "
                "FROM fb_crossposter_settings WHERE target_fb_page_id = ?",
                ("legacy_page",),
            ).fetchone()
        self.assertEqual(stored[0], "")
        self.assertTrue(stored[1].startswith("dpapi:"))
        self.assertEqual(
            db.get_fb_crossposter_runtime_settings("legacy_page")["target_access_token"],
            token,
        )

    @patch("urllib.request.build_opener")
    def test_resumable_upload_protocol(self, mock_build_opener):
        # Mock responses for start, transfer, finish, and separate thumbnail upload
        mock_opener = MagicMock()
        mock_build_opener.return_value = mock_opener

        resp_start = MagicMock()
        resp_start.read.return_value = b'{"upload_session_id": "session_123", "video_id": "vid_999", "start_offset": "0", "end_offset": "100"}'
        resp_start.__enter__.return_value = resp_start

        resp_transfer = MagicMock()
        resp_transfer.read.return_value = b'{"start_offset": "100", "end_offset": "100"}'
        resp_transfer.__enter__.return_value = resp_transfer

        resp_finish = MagicMock()
        resp_finish.read.return_value = b'{"success": true, "id": "vid_999"}'
        resp_finish.__enter__.return_value = resp_finish

        resp_thumb = MagicMock()
        resp_thumb.read.return_value = b'{"success": true}'
        resp_thumb.__enter__.return_value = resp_thumb

        mock_opener.open.side_effect = [resp_start, resp_transfer, resp_finish, resp_thumb]

        with tempfile.TemporaryDirectory() as temp_dir:
            test_video = Path(temp_dir) / "test_dummy_video.mp4"
            test_thumb = Path(temp_dir) / "test_thumb.jpg"
            test_video.write_bytes(b"0" * 100)
            test_thumb.write_bytes(b"thumbnail")
            res = fb_crossposter_service.upload_large_video_resumable(
                page_id="test_page",
                access_token="test_token",
                video_path=test_video,
                title="Test Large Video",
                description="Test Description",
                thumb_path=test_thumb,
                content_tag_ids=["101", "202"],
                chunk_size_bytes=50,
            )
            self.assertEqual(res.get("id"), "vid_999")
            self.assertEqual(mock_opener.open.call_count, 4)
            finish_request = mock_opener.open.call_args_list[2].args[0]
            self.assertTrue(
                finish_request.full_url.startswith(
                    "https://graph-video.facebook.com/v26.0/"
                )
            )
            self.assertEqual(
                finish_request.get_header("Content-type"),
                "application/x-www-form-urlencoded",
            )
            finish_fields = urllib.parse.parse_qs(finish_request.data.decode("utf-8"))
            self.assertEqual(finish_fields["upload_phase"], ["finish"])
            self.assertEqual(json.loads(finish_fields["content_tags"][0]), ["101", "202"])

            thumb_request = mock_opener.open.call_args_list[3].args[0]
            self.assertTrue(thumb_request.full_url.endswith("/vid_999/thumbnails"))
            self.assertIn(b'name="source"', thumb_request.data)
            self.assertIn(b'name="is_preferred"', thumb_request.data)

    @patch("auto_yt.services.fb_crossposter_service._get_proxy_for_gpm_profile", return_value=None)
    @patch("auto_yt.services.fb_crossposter_service._build_urllib_opener")
    def test_small_upload_sends_thumbnail_and_content_tags(
        self, mock_build_opener, _mock_proxy
    ):
        response = MagicMock()
        response.read.return_value = b'{"id":"small_123"}'
        response.__enter__.return_value = response
        mock_build_opener.return_value.open.return_value = response

        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "video.mp4"
            thumb = Path(temp_dir) / "thumb.jpg"
            video.write_bytes(b"video")
            thumb.write_bytes(b"thumbnail")
            result = fb_crossposter_service.upload_video_to_facebook(
                page_id="page",
                access_token="token",
                video_path=video,
                title="Title",
                description="Description",
                thumb_path=thumb,
                content_tag_ids=["303"],
            )

        self.assertEqual(result["id"], "small_123")
        request = mock_build_opener.return_value.open.call_args.args[0]
        self.assertTrue(
            request.full_url.startswith("https://graph-video.facebook.com/v26.0/")
        )
        self.assertIn(b'name="thumb"', request.data)
        self.assertIn(b'name="source"', request.data)
        self.assertIn(b'name="content_tags"', request.data)
        self.assertIn(b'["303"]', request.data)

    def test_jit_download_enriches_metadata_before_upload(self):
        item = {
            "id": 77,
            "target_page_id": "page",
            "youtube_id": "yt77",
            "youtube_url": "https://youtube.com/watch?v=yt77",
            "original_title": "Tiêu đề",
            "original_description": "",
            "original_tags": [],
            "thumbnail_url": "",
            "fb_title": "Tiêu đề",
            "fb_description": "Caption cũ",
            "fb_description_source": "auto",
            "scheduled_publish_time": int(
                (datetime.datetime.now() + datetime.timedelta(days=1)).timestamp()
            ),
        }
        settings = {
            "target_fb_page_id": "page",
            "target_access_token": "token",
            "source_gpm_profile_id": "source-profile",
            "target_gpm_profile_id": "target-profile",
            "post_template": "{title}\n\n{clean_description}\n\n{hashtags}",
            "convert_to_vertical": True,
            "default_tags": ["Thương hiệu", "lịch sử"],
        }
        source_info = {
            "title": "Tiêu đề",
            "description": (
                "Tiêu đề\nNội dung mô tả video:\nMô tả đầy đủ "
                "https://youtube.com/watch?v=yt77"
            ),
            "tags": ["lịch sử", "Việt Nam"],
            "thumbnail": "https://i.ytimg.com/yt77.jpg",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            downloader = MagicMock()
            downloader.__enter__.return_value = downloader

            def extract_info(_url, download):
                self.assertTrue(download)
                (temp_path / "yt77_77.mp4").write_bytes(b"video")
                return source_info

            downloader.extract_info.side_effect = extract_info

            def save_thumb(_url, output_path, source_gpm_profile_id=""):
                self.assertEqual(source_gpm_profile_id, "source-profile")
                output_path.write_bytes(b"thumbnail")
                return True

            def convert_video(_source_path, output_path):
                output_path.write_bytes(b"vertical video")
                return output_path

            with (
                patch.object(fb_crossposter_service, "TEMP_DOWNLOAD_DIR", temp_path),
                patch.object(fb_crossposter_service, "YoutubeDL", return_value=downloader),
                patch.object(fb_crossposter_service, "ensure_ffmpeg_directory", return_value=""),
                patch.object(db, "get_fb_crossposter_queue_item", return_value=dict(item)),
                patch.object(db, "get_fb_crossposter_runtime_settings", return_value=settings),
                patch.object(db, "update_fb_crossposter_queue_item") as update_item,
                patch.object(fb_crossposter_service, "_get_proxy_for_gpm_profile", return_value=None),
                patch.object(fb_crossposter_service, "download_thumbnail", side_effect=save_thumb),
                patch.object(
                    fb_crossposter_service,
                    "convert_video_to_vertical",
                    side_effect=convert_video,
                ) as convert_video_mock,
                patch.object(
                    fb_crossposter_service,
                    "convert_thumbnail_to_vertical",
                    side_effect=lambda path: path,
                ) as convert_thumbnail_mock,
                patch.object(
                    fb_crossposter_service,
                    "resolve_content_tag_ids",
                    return_value=(["101"], ["Việt Nam"]),
                ) as resolve_tags,
                patch.object(
                    fb_crossposter_service,
                    "upload_video_to_facebook",
                    return_value={"id": "fb77"},
                ) as upload_video,
                patch.object(
                    fb_crossposter_service,
                    "verify_facebook_publication",
                    return_value=(
                        "meta_scheduled",
                        {
                            "meta_published": 0,
                            "meta_video_status": "ready",
                            "meta_scheduled_publish_time": item["scheduled_publish_time"],
                            "meta_status_json": "{}",
                            "meta_verified_at": "now",
                        },
                    ),
                ),
            ):
                result = fb_crossposter_service.process_queue_item_jit(
                    77,
                    parent_task_id="batch",
                )

        self.assertEqual(result["fb_post_id"], "fb77")
        self.assertEqual(result["status"], "meta_scheduled")
        upload_kwargs = upload_video.call_args.kwargs
        self.assertTrue(upload_kwargs["video_path"].name.endswith("_vertical.mp4"))
        self.assertEqual(upload_kwargs["content_tag_ids"], ["101"])
        self.assertEqual(upload_kwargs["custom_labels"][:2], ["Thương hiệu", "lịch sử"])
        self.assertNotIn("youtube.com", upload_kwargs["description"])
        self.assertEqual(upload_kwargs["description"].count("Tiêu đề"), 1)
        self.assertIn("#ThươngHiệu", upload_kwargs["description"])
        convert_video_mock.assert_called_once()
        convert_thumbnail_mock.assert_called_once()
        self.assertEqual(resolve_tags.call_args.kwargs["default_tags"], ["Thương hiệu", "lịch sử"])
        metadata_updates = [
            call.args[1]
            for call in update_item.call_args_list
            if "original_description" in call.args[1]
        ][0]
        self.assertEqual(metadata_updates["original_tags_json"], '["lịch sử", "Việt Nam"]')
        self.assertEqual(metadata_updates["fb_description_source"], "auto")

    def test_vertical_conversion_failure_blocks_upload_and_cleans_temp_files(self):
        item = {
            "id": 78,
            "target_page_id": "page",
            "youtube_id": "yt78",
            "youtube_url": "https://youtube.com/watch?v=yt78",
            "original_title": "Video lỗi chuyển đổi",
            "original_description": "",
            "original_tags": [],
            "thumbnail_url": "",
            "fb_title": "Video lỗi chuyển đổi",
            "fb_description": "",
            "fb_description_source": "auto",
            "scheduled_publish_time": 0,
        }
        settings = {
            "target_fb_page_id": "page",
            "target_access_token": "token",
            "convert_to_vertical": True,
            "default_tags": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            downloader = MagicMock()
            downloader.__enter__.return_value = downloader

            def extract_info(_url, download):
                self.assertTrue(download)
                (temp_path / "yt78_78.mp4").write_bytes(b"video")
                return {
                    "title": item["original_title"],
                    "description": "Mô tả",
                    "tags": [],
                    "thumbnail": "https://i.ytimg.com/yt78.jpg",
                }

            downloader.extract_info.side_effect = extract_info

            def save_thumb(_url, output_path, source_gpm_profile_id=""):
                output_path.write_bytes(b"thumbnail")
                return True

            with (
                patch.object(fb_crossposter_service, "TEMP_DOWNLOAD_DIR", temp_path),
                patch.object(fb_crossposter_service, "YoutubeDL", return_value=downloader),
                patch.object(fb_crossposter_service, "ensure_ffmpeg_directory", return_value=""),
                patch.object(db, "get_fb_crossposter_queue_item", return_value=dict(item)),
                patch.object(db, "get_fb_crossposter_runtime_settings", return_value=settings),
                patch.object(db, "update_fb_crossposter_queue_item") as update_item,
                patch.object(fb_crossposter_service, "_get_proxy_for_gpm_profile", return_value=None),
                patch.object(fb_crossposter_service, "download_thumbnail", side_effect=save_thumb),
                patch.object(
                    fb_crossposter_service,
                    "convert_video_to_vertical",
                    side_effect=RuntimeError("ffmpeg failed"),
                ),
                patch.object(fb_crossposter_service, "upload_video_to_facebook") as upload_video,
            ):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
                    fb_crossposter_service.process_queue_item_jit(
                        78,
                        parent_task_id="batch",
                        publish_now=True,
                    )

            upload_video.assert_not_called()
            error_updates = [
                call.args[1]
                for call in update_item.call_args_list
                if call.args[1].get("status") == "error"
            ]
            self.assertEqual(len(error_updates), 1)
            self.assertIn("ffmpeg failed", error_updates[0]["error_message"])
            self.assertEqual(list(temp_path.iterdir()), [])

    @patch("auto_yt.services.fb_crossposter_service._get_proxy_for_gpm_profile", return_value=None)
    @patch("auto_yt.services.fb_crossposter_service._build_urllib_opener")
    def test_existing_video_repair_updates_and_verifies_all_metadata(
        self, mock_build_opener, _mock_proxy
    ):
        responses = []
        for payload in (
            {
                "description": "Old caption",
                "content_tags": {"data": []},
                "thumbnails": {"data": [{"id": "old", "is_preferred": True}]},
            },
            {"success": True},
            {"success": True},
            {
                "description": "New caption",
                "content_tags": {"data": [{"id": "101"}]},
                "thumbnails": {"data": [{"id": "new", "is_preferred": True}]},
            },
        ):
            response = MagicMock()
            response.read.return_value = json.dumps(payload).encode("utf-8")
            response.__enter__.return_value = response
            responses.append(response)
        mock_build_opener.return_value.open.side_effect = responses

        with tempfile.TemporaryDirectory() as temp_dir:
            thumb = Path(temp_dir) / "thumb.jpg"
            thumb.write_bytes(b"thumbnail")
            result = fb_crossposter_service.update_facebook_video_metadata(
                "video_123",
                "token",
                "New title",
                "New caption",
                content_tag_ids=["101"],
                custom_labels=["Lịch sử Việt Nam", "Khmer Đỏ"],
                thumb_path=thumb,
            )

        self.assertTrue(result["success"])
        requests = [call.args[0] for call in mock_build_opener.return_value.open.call_args_list]
        self.assertTrue(requests[1].full_url.startswith("https://graph.facebook.com/v26.0/"))
        update_fields = urllib.parse.parse_qs(requests[1].data.decode("utf-8"))
        self.assertEqual(update_fields["title"], ["New title"])
        self.assertEqual(update_fields["description"], ["New caption"])
        self.assertEqual(json.loads(update_fields["content_tags"][0]), ["101"])
        self.assertEqual(json.loads(update_fields["custom_labels"][0]), ["Lịch sử Việt Nam", "Khmer Đỏ"])
        self.assertTrue(requests[2].full_url.endswith("/video_123/thumbnails"))
        self.assertIn(b'name="source"', requests[2].data)
        self.assertIn(b'name="is_preferred"', requests[2].data)

    def test_repair_does_not_delete_original_when_replacement_fails(self):
        item = {
            "id": 88,
            "target_page_id": "page",
            "youtube_id": "yt88",
            "youtube_url": "https://youtube.com/watch?v=yt88",
            "original_title": "Title",
            "original_description": "",
            "original_tags": [],
            "fb_title": "Title",
            "fb_description": "",
            "fb_description_source": "auto",
            "fb_post_id": "old_video",
            "status": "meta_failed",
            "scheduled_publish_time": int(
                (datetime.datetime.now() - datetime.timedelta(hours=1)).timestamp()
            ),
        }
        settings = {
            "target_access_token": "token",
            "source_gpm_profile_id": "source",
            "target_gpm_profile_id": "target",
            "post_template": "{title}\n\n{clean_description}\n\n{hashtags}",
        }
        source_info = {
            "title": "Title",
            "description": "Description",
            "tags": ["Tag"],
            "thumbnail": "https://i.ytimg.com/yt88.jpg",
        }

        def save_thumb(_url, output_path, source_gpm_profile_id=""):
            output_path.write_bytes(b"thumbnail")
            return True

        with (
            patch.object(
                db,
                "get_fb_crossposter_queue_item",
                side_effect=[dict(item), dict(item), dict(item), dict(item)],
            ),
            patch.object(db, "get_fb_crossposter_runtime_settings", return_value=settings),
            patch.object(db, "update_fb_crossposter_queue_item"),
            patch.object(
                db,
                "reserve_next_fb_queue_slot",
                return_value=int((datetime.datetime.now() + datetime.timedelta(days=1)).timestamp()),
            ),
            patch.object(db, "append_fb_recovery_history"),
            patch.object(fb_crossposter_service, "_fetch_source_metadata", return_value=source_info),
            patch.object(fb_crossposter_service, "download_thumbnail", side_effect=save_thumb),
            patch.object(
                fb_crossposter_service,
                "get_facebook_video_metadata",
                return_value={
                    "id": "old_video",
                    "published": False,
                    "status": {"video_status": "error"},
                },
            ),
            patch.object(
                fb_crossposter_service,
                "resolve_content_tag_ids",
                return_value=(["101"], []),
            ),
            patch.object(
                fb_crossposter_service,
                "update_facebook_video_metadata",
                side_effect=RuntimeError("update rejected"),
            ),
            patch.object(
                fb_crossposter_service,
                "process_queue_item_jit",
                side_effect=RuntimeError("replacement failed"),
            ) as process_item,
            patch.object(fb_crossposter_service, "delete_facebook_video") as delete_video,
        ):
            with self.assertRaisesRegex(RuntimeError, "replacement failed"):
                fb_crossposter_service.repair_fb_queue_item(88)

        delete_video.assert_not_called()
        self.assertFalse(process_item.call_args.kwargs["publish_now"])

    def test_verified_replacement_is_kept_when_old_video_cleanup_fails(self):
        future_schedule = int((datetime.datetime.now() + datetime.timedelta(days=1)).timestamp())
        item = {
            "id": 89,
            "target_page_id": "page",
            "youtube_id": "yt89",
            "youtube_url": "https://youtube.com/watch?v=yt89",
            "original_title": "Title",
            "original_description": "Description",
            "original_tags": [],
            "fb_title": "Title",
            "fb_description": "Caption",
            "fb_description_source": "manual",
            "fb_post_id": "old_video",
            "status": "schedule_mismatch",
            "scheduled_publish_time": future_schedule,
        }
        settings = {
            "target_access_token": "token",
            "source_gpm_profile_id": "source",
            "target_gpm_profile_id": "target",
            "post_template": "{title}",
            "default_tags": [],
        }

        def save_thumb(_url, output_path, source_gpm_profile_id=""):
            output_path.write_bytes(b"thumbnail")
            return True

        with (
            patch.object(db, "get_fb_crossposter_queue_item", return_value=dict(item)),
            patch.object(db, "get_fb_crossposter_runtime_settings", return_value=settings),
            patch.object(db, "update_fb_crossposter_queue_item") as update_item,
            patch.object(db, "append_fb_recovery_history"),
            patch.object(
                fb_crossposter_service,
                "_fetch_source_metadata",
                return_value={
                    "title": "Title",
                    "description": "Description",
                    "tags": [],
                    "thumbnail": "https://i.ytimg.com/yt89.jpg",
                },
            ),
            patch.object(fb_crossposter_service, "download_thumbnail", side_effect=save_thumb),
            patch.object(fb_crossposter_service, "_caption_is_automatic", return_value=False),
            patch.object(fb_crossposter_service, "resolve_content_tag_ids", return_value=([], [])),
            patch.object(
                fb_crossposter_service,
                "get_facebook_video_metadata",
                side_effect=[
                    {
                        "id": "old_video",
                        "published": True,
                        "status": {
                            "video_status": "uploading",
                            "uploading_phase": {"status": "in_progress"},
                        },
                    },
                    {
                        "id": "new_video",
                        "description": "Caption",
                        "content_tags": {"data": []},
                        "thumbnails": {"data": [{"id": "thumb", "is_preferred": True}]},
                    },
                ],
            ),
            patch.object(
                fb_crossposter_service,
                "update_facebook_video_metadata",
                side_effect=RuntimeError("update rejected"),
            ),
            patch.object(
                fb_crossposter_service,
                "process_queue_item_jit",
                return_value={"status": "meta_scheduled", "fb_post_id": "new_video"},
            ),
            patch.object(
                fb_crossposter_service,
                "delete_facebook_video",
                side_effect=RuntimeError("delete temporarily failed"),
            ) as delete_video,
        ):
            result = fb_crossposter_service.repair_fb_queue_item(89)

        self.assertEqual(result["video_id"], "new_video")
        self.assertEqual(result["cleanup_status"], "old_delete_failed")
        delete_video.assert_called_once_with(
            "old_video",
            "token",
            target_gpm_profile_id="target",
        )
        cleanup_updates = [
            call.args[1] for call in update_item.call_args_list
            if call.args[1].get("cleanup_status") == "old_delete_failed"
        ]
        self.assertEqual(len(cleanup_updates), 1)

    def test_sanitize_description_removes_disclaimer_and_channel_intro(self):
        sample_desc = """
VỊ TƯỚNG VIỆT NAM DUY NHẤT HY SINH Ở CAMPUCHIA

📄 Nội dung mô tả video:
Một vị tướng duy nhất của Việt Nam đã hy sinh trên đất Campuchia – câu chuyện về Thiếu tướng Kim Tuấn.
Sau khi xem video, bạn có cảm nghĩ gì?
#TrieuTraiTimNhoOn #LichSuKhongTheQuen #GocKhuatVietSu

#tuongvietnamhysinhcampuchia
#toiackhmerdo
#chientranhbiengioitaynam
--------------------------
Góc Khuất Việt Sử là nơi tái hiện những sự kiện lịch sử và chính trị Việt Nam...
Đây không chỉ là nơi để "nghe kể sử", mà là nơi để hiểu sử.
#GocKhuatVietSu #chinhtrivietnam #tintuc24h
--------------------------
Lưu Ý: Các nội dung trong video được tổng hợp từ nhiều nguồn khác nhau trên internet...

#TriếtLýCuộcSống #TriếtLýTinhHoa #ChínhSáchXãHội #TinTứcMớiNhất
        """
        cleaned = fb_crossposter_service.sanitize_description(sample_desc)
        self.assertIn("Một vị tướng duy nhất", cleaned)
        self.assertIn("Sau khi xem video", cleaned)
        self.assertNotIn("Lưu Ý:", cleaned)
        self.assertNotIn("tổng hợp từ nhiều nguồn", cleaned)
        self.assertNotIn("Góc Khuất Việt Sử là nơi", cleaned)
        self.assertNotIn("TriếtLýCuộcSống", cleaned)
        self.assertNotIn("--------------------------", cleaned)

    def test_extract_hashtags_and_filter_blacklist(self):
        sample_desc = """
Một vị tướng đã hy sinh.
#tuongvietnamhysinhcampuchia #toiackhmerdo #chientranhbiengioitaynam
Lưu Ý: Disclaimer
#TriếtLýCuộcSống #ChínhSáchXãHội #TinTức24h
        """
        tags = fb_crossposter_service.extract_hashtags_from_description(sample_desc)
        self.assertIn("#tuongvietnamhysinhcampuchia", tags)
        self.assertIn("#toiackhmerdo", tags)
        self.assertIn("#chientranhbiengioitaynam", tags)
        self.assertNotIn("#TriếtLýCuộcSống", tags)
        self.assertNotIn("#TinTức24h", tags)

    def test_build_fb_caption_strictly_caps_at_5_hashtags(self):
        title = "Tướng Kim Tuấn Hy Sinh"
        raw_desc = """
Câu chuyện về vị tướng quả cảm.
#tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7
        """
        extra_tags = ["Lịch sử", "Quân sự", "Campuchia", "Khmer Đỏ", "Chiến tranh"]
        caption = fb_crossposter_service.build_fb_caption(
            title=title,
            raw_description=raw_desc,
            tags=extra_tags,
        )
        # Count hashtags in caption
        hashtags_in_caption = [w for w in caption.split() if w.startswith("#")]
        self.assertLessEqual(len(hashtags_in_caption), 5)
        self.assertIn("#Tag1", hashtags_in_caption)

    def test_unique_tag_keywords_enriches_from_description(self):
        raw_desc = "Video về #tuongvietnamhysinhcampuchia và #chientranhbiengioitaynam"
        keywords = fb_crossposter_service._unique_tag_keywords(
            tags=["Việt Nam"],
            raw_description=raw_desc,
        )
        self.assertIn("tuongvietnamhysinhcampuchia", keywords)
        self.assertIn("chientranhbiengioitaynam", keywords)
    def test_process_queue_item_jit_idempotency_guard(self):
        item = {
            "id": 99,
            "target_page_id": "page_123",
            "youtube_id": "yt99",
            "youtube_url": "https://youtube.com/watch?v=yt99",
            "original_title": "Already Published Title",
            "status": "published",
            "fb_post_id": "1641229374062033",
        }
        settings = {
            "target_fb_page_id": "page_123",
            "target_fb_page_name": "Test Fanpage",
            "target_access_token": "token_abc",
        }
        with (
            patch.object(db, "get_fb_crossposter_queue_item", return_value=item),
            patch.object(db, "get_fb_crossposter_runtime_settings", return_value=settings),
            patch.object(
                fb_crossposter_service,
                "reconcile_fb_queue_item",
                return_value={
                    "item_id": 99,
                    "fb_post_id": "1641229374062033",
                    "status": "published",
                },
            ),
            patch.object(fb_crossposter_service, "upload_video_to_facebook") as upload_mock,
        ):
            res = fb_crossposter_service.process_queue_item_jit(99)
            self.assertTrue(res.get("success"))
            self.assertTrue(res.get("already_published"))
            self.assertEqual(res.get("fb_post_id"), "1641229374062033")
            upload_mock.assert_not_called()

    def test_get_custom_labels_extracts_and_filters_tags(self):
        desc = "Khám phá lịch sử #ThiếuTướngKimTuấn #ChiếnTranhBiênGiới #Trending"
        tags = ["Việt Nam", "Lịch sử quân sự", "viral"]
        labels = fb_crossposter_service.get_custom_labels(tags=tags, raw_description=desc, max_count=5)
        self.assertIn("ThiếuTướngKimTuấn", labels)
        self.assertIn("ChiếnTranhBiênGiới", labels)
        self.assertIn("Việt Nam", labels)
        self.assertNotIn("Trending", labels)
        self.assertNotIn("viral", labels)
        self.assertLessEqual(len(labels), 5)

    def test_resolve_content_tag_ids_with_topic_interest_map(self):
        tags = ["lịch sử việt nam", "chiến tranh campuchia", "từ khóa không có"]
        tag_ids, skipped = fb_crossposter_service.resolve_content_tag_ids(tags=tags)
        # Should resolve using TOPIC_INTEREST_MAP
        self.assertIn("6003249578667", tag_ids)  # Lịch sử
        self.assertIn("6003209794830", tag_ids)  # Chiến tranh
        self.assertIn("từ khóa không có", skipped)

    @patch("auto_yt.services.fb_crossposter_service._get_proxy_for_gpm_profile", return_value=None)
    @patch("auto_yt.services.fb_crossposter_service._build_urllib_opener")
    def test_upload_video_to_facebook_includes_custom_labels(self, mock_build_opener, _mock_proxy):
        response = MagicMock()
        response.read.return_value = json.dumps({"id": "fb_vid_999"}).encode("utf-8")
        response.__enter__.return_value = response
        mock_build_opener.return_value.open.return_value = response

        with tempfile.TemporaryDirectory() as temp_dir:
            dummy_video = Path(temp_dir) / "test.mp4"
            dummy_video.write_bytes(b"dummy video data")

            res = fb_crossposter_service.upload_video_to_facebook(
                page_id="123456",
                access_token="EAAtesttoken",
                video_path=dummy_video,
                title="Test Title",
                description="Test Desc",
                content_tag_ids=["6003249578667"],
                custom_labels=["Lịch sử Việt Nam", "Thiếu tướng Kim Tuấn"],
            )

        self.assertEqual(res.get("id"), "fb_vid_999")
        request = mock_build_opener.return_value.open.call_args.args[0]
        self.assertIn(b'name="custom_labels"', request.data)
        self.assertIn("Lịch sử Việt Nam".encode("utf-8"), request.data)
        self.assertIn(b'name="content_tags"', request.data)

    def test_is_transient_meta_error_detection(self):
        self.assertTrue(fb_crossposter_service.is_transient_meta_error('{"error":{"code":2,"error_subcode":1363047,"is_transient":true}}', 400))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error('{"error":{"code":4,"message":"Application request limit reached"}}', 400))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error('{"error":{"code":17,"message":"User request limit reached"}}', 400))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error('{"error":{"code":341,"message":"Temporarily blocked"}}', 400))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error('{"error":{"error_subcode":1363030}}', 400))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error('{"error":{"error_subcode":1363019}}', 400))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error("Service temporarily unavailable", 400))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error("Request timed out", 400))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error("Internal Server Error", 500))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error("Bad Gateway", 502))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error("Too Many Requests", 429))
        self.assertTrue(fb_crossposter_service.is_transient_meta_error("Request Timeout", 408))
        self.assertFalse(fb_crossposter_service.is_transient_meta_error('{"error":{"code":190,"message":"Session has expired"}}', 400))

    @patch("time.sleep", return_value=None)
    @patch("auto_yt.services.fb_crossposter_service._get_proxy_for_gpm_profile", return_value=None)
    @patch("auto_yt.services.fb_crossposter_service._build_urllib_opener")
    def test_upload_large_video_resumable_retries_transient_finish_error(self, mock_build_opener, _mock_proxy, _mock_sleep):
        # Setup mock responses:
        # Phase 1: start
        start_resp = MagicMock()
        start_resp.read.return_value = json.dumps({"upload_session_id": "sess_123", "video_id": "vid_123", "start_offset": 0, "end_offset": 30 * 1024 * 1024}).encode("utf-8")
        start_resp.__enter__.return_value = start_resp

        # Phase 2: transfer chunk
        transfer_resp = MagicMock()
        transfer_resp.read.return_value = json.dumps({"start_offset": 30 * 1024 * 1024, "end_offset": 30 * 1024 * 1024}).encode("utf-8")
        transfer_resp.__enter__.return_value = transfer_resp

        # Phase 3 attempt 1 & 2: Transient 400 error (code 2, subcode 1363047)
        err_msg = json.dumps({
            "error": {
                "message": "Service temporarily unavailable",
                "type": "OAuthException",
                "is_transient": True,
                "code": 2,
                "error_subcode": 1363047
            }
        }).encode("utf-8")
        http_err1 = urllib.error.HTTPError(
            url="https://graph-video.facebook.com/v26.0/123/videos",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=io.BytesIO(err_msg)
        )
        http_err2 = urllib.error.HTTPError(
            url="https://graph-video.facebook.com/v26.0/123/videos",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=io.BytesIO(err_msg)
        )

        # Phase 3 attempt 3: Success
        finish_resp = MagicMock()
        finish_resp.read.return_value = json.dumps({"success": True, "id": "vid_123"}).encode("utf-8")
        finish_resp.__enter__.return_value = finish_resp

        mock_build_opener.return_value.open.side_effect = [
            start_resp,
            transfer_resp,
            http_err1,
            http_err2,
            finish_resp
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            large_video = Path(temp_dir) / "large.mp4"
            # 30 MB to trigger resumable
            large_video.write_bytes(b"0" * (30 * 1024 * 1024))

            res = fb_crossposter_service.upload_large_video_resumable(
                page_id="123456",
                access_token="EAAtesttoken",
                video_path=large_video,
                title="Large Video",
                description="Large Desc",
                chunk_size_bytes=30 * 1024 * 1024,
            )

        self.assertEqual(res.get("id"), "vid_123")
        self.assertEqual(mock_build_opener.return_value.open.call_count, 5)

    def test_meta_schedule_requires_matching_remote_schedule(self):
        scheduled_time = int(
            (datetime.datetime.now() + datetime.timedelta(days=2)).timestamp()
        )
        status, fields = fb_crossposter_service.classify_facebook_publication(
            {
                "id": "video_1",
                "published": False,
                "scheduled_publish_time": scheduled_time,
                "status": {
                    "video_status": "ready",
                    "uploading_phase": {"status": "complete"},
                },
            },
            scheduled_time,
        )
        self.assertEqual(status, "meta_scheduled")
        self.assertEqual(fields["meta_scheduled_publish_time"], scheduled_time)

    def test_meta_object_still_uploading_is_not_a_scheduled_success(self):
        scheduled_time = int(
            (datetime.datetime.now() + datetime.timedelta(days=2)).timestamp()
        )
        status, _fields = fb_crossposter_service.classify_facebook_publication(
            {
                "id": "video_2",
                "published": True,
                "status": {
                    "video_status": "uploading",
                    "uploading_phase": {"status": "in_progress"},
                },
            },
            scheduled_time,
        )
        self.assertEqual(status, "processing")

    def test_past_meta_schedule_not_published_is_an_error(self):
        scheduled_time = int(
            (datetime.datetime.now() - datetime.timedelta(hours=1)).timestamp()
        )
        with self.assertRaisesRegex(RuntimeError, "đã qua"):
            fb_crossposter_service.classify_facebook_publication(
                {
                    "id": "video_past",
                    "published": False,
                    "scheduled_publish_time": scheduled_time,
                    "status": {
                        "uploading_phase": {"status": "complete"},
                        "processing_phase": {"status": "complete"},
                    },
                },
                scheduled_time,
            )

    def test_active_schedule_slot_is_unique_per_fanpage(self):
        db.upsert_fb_crossposter_queue_items(
            [
                {"youtube_id": "slot_1", "original_title": "Slot 1"},
                {"youtube_id": "slot_2", "original_title": "Slot 2"},
            ],
            target_page_id="page_unique",
        )
        items = db.get_fb_crossposter_queue(
            target_page_id="page_unique", page_size=10
        )["items"]
        scheduled_time = int(
            (datetime.datetime.now() + datetime.timedelta(days=3)).timestamp()
        )
        db.update_fb_crossposter_queue_item(
            items[0]["id"],
            {"status": "scheduled", "scheduled_publish_time": scheduled_time},
        )
        with self.assertRaisesRegex(ValueError, "đã được một video khác giữ"):
            db.update_fb_crossposter_queue_item(
                items[1]["id"],
                {"status": "scheduled", "scheduled_publish_time": scheduled_time},
            )

    def test_reserve_next_repair_slot_is_future_and_collision_safe(self):
        db.save_fb_crossposter_settings(
            {
                "target_fb_page_id": "page_repair",
                "daily_quota": 1,
                "schedule_times": ["23:59"],
                "lead_time_minutes": 30,
            },
            page_id="page_repair",
        )
        db.upsert_fb_crossposter_queue_items(
            [
                {"youtube_id": "repair_slot_1", "original_title": "Repair 1"},
                {"youtube_id": "repair_slot_2", "original_title": "Repair 2"},
            ],
            target_page_id="page_repair",
        )
        items = db.get_fb_crossposter_queue(
            target_page_id="page_repair",
            page_size=10,
        )["items"]

        first_slot = db.reserve_next_fb_queue_slot(items[0]["id"])
        second_slot = db.reserve_next_fb_queue_slot(items[1]["id"])

        self.assertGreater(first_slot, int(datetime.datetime.now().timestamp()) + 29 * 60)
        self.assertGreater(second_slot, first_slot)
        self.assertNotEqual(first_slot, second_slot)

    def test_inspect_meta_schedule_mismatch_is_actionable_state(self):
        requested = int((datetime.datetime.now() + datetime.timedelta(days=1)).timestamp())
        actual = requested + 3600
        status, fields, message = fb_crossposter_service.inspect_facebook_publication(
            {
                "id": "video_mismatch",
                "published": False,
                "scheduled_publish_time": actual,
                "status": {
                    "video_status": "ready",
                    "uploading_phase": {"status": "complete"},
                    "publishing_phase": {"status": "complete"},
                },
            },
            requested,
        )

        self.assertEqual(status, "schedule_mismatch")
        self.assertEqual(fields["meta_scheduled_publish_time"], actual)
        self.assertIn("sai lịch", message)

    def test_cleanup_rejects_valid_meta_schedule(self):
        item = {
            "id": 91,
            "target_page_id": "page",
            "fb_post_id": "valid_video",
        }
        with (
            patch.object(db, "get_fb_crossposter_queue_item", return_value=item),
            patch.object(
                fb_crossposter_service,
                "reconcile_fb_queue_item",
                return_value={
                    "status": "meta_scheduled",
                    "fb_post_id": "valid_video",
                },
            ),
            patch.object(fb_crossposter_service, "delete_facebook_video") as delete_video,
        ):
            with self.assertRaisesRegex(ValueError, "Không được xóa"):
                fb_crossposter_service.cleanup_failed_meta_video_and_reschedule(91)

        delete_video.assert_not_called()

    def test_publish_existing_meta_video_does_not_upload_duplicate(self):
        item = {
            "id": 92,
            "target_page_id": "page",
            "fb_post_id": "scheduled_video",
        }
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"success": true}'
        response.__exit__.return_value = False
        opener = MagicMock()
        opener.open.return_value = response
        with (
            patch.object(db, "get_fb_crossposter_queue_item", return_value=item),
            patch.object(
                db,
                "get_fb_crossposter_runtime_settings",
                return_value={"target_access_token": "token", "target_gpm_profile_id": "profile"},
            ),
            patch.object(db, "update_fb_crossposter_queue_item") as update_item,
            patch.object(db, "append_fb_recovery_history"),
            patch.object(
                fb_crossposter_service,
                "reconcile_fb_queue_item",
                return_value={"status": "meta_scheduled", "fb_post_id": "scheduled_video"},
            ),
            patch.object(fb_crossposter_service, "_get_proxy_for_gpm_profile", return_value=None),
            patch.object(fb_crossposter_service, "_build_urllib_opener", return_value=opener),
            patch.object(
                fb_crossposter_service,
                "verify_facebook_publication",
                return_value=("published", {"meta_published": 1}),
            ),
            patch.object(fb_crossposter_service, "upload_video_to_facebook") as upload_video,
        ):
            result = fb_crossposter_service.publish_existing_meta_video_now(92)

        self.assertEqual(result["status"], "published")
        upload_video.assert_not_called()
        self.assertEqual(update_item.call_args.args[1]["scheduled_publish_time"], 0)

    def test_format_facebook_api_error_missing_object_vs_permissions(self):
        # Error when object does not exist (contains the phrase 'missing permissions')
        missing_err = (
            '{"error":{"message":"Unsupported get request. Object with ID \'2516067995540325\' does not exist, '
            'cannot be loaded due to missing permissions, or does not support this operation.",'
            '"type":"GraphMethodException","code":100,"error_subcode":33}}'
        )
        formatted_missing = fb_crossposter_service.format_facebook_api_error(missing_err, 400)
        self.assertIn("không tồn tại hoặc đã bị xóa", formatted_missing)
        self.assertNotIn("thiếu quyền đăng bài", formatted_missing)

        # Genuine permission error
        perm_err = '{"error":{"message":"(#200) Requires pages_manage_posts permission","code":200}}'
        formatted_perm = fb_crossposter_service.format_facebook_api_error(perm_err, 400)
        self.assertIn("thiếu quyền đăng bài", formatted_perm)

    def test_inspect_facebook_publication_stalled_from_remote_timestamp(self):
        # Video created 5 hours ago, still in uploading_phase: in_progress
        five_hours_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=5)
        metadata = {
            "id": "1400610205380249",
            "created_time": five_hours_ago.strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "updated_time": five_hours_ago.strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "published": True,
            "status": {
                "video_status": "uploading",
                "uploading_phase": {"status": "in_progress"},
                "processing_phase": {"status": "not_started"},
                "publishing_phase": {"status": "not_started"},
            },
        }
        status, fields, msg = fb_crossposter_service.inspect_facebook_publication(metadata, 1790308800)
        self.assertEqual(status, "stalled")
        self.assertIn("quá 2 giờ", msg)

    def test_delete_facebook_video_already_deleted_returns_success(self):
        http_error = urllib.error.HTTPError(
            url="https://graph.facebook.com/v20.0/2516067995540325",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=io.BytesIO(
                b'{"error":{"message":"Unsupported delete request. Object does not exist","type":"GraphMethodException","code":100,"error_subcode":33}}'
            ),
        )
        opener = MagicMock()
        opener.open.side_effect = http_error
        with (
            patch.object(fb_crossposter_service, "_get_proxy_for_gpm_profile", return_value=None),
            patch.object(fb_crossposter_service, "_build_urllib_opener", return_value=opener),
        ):
            res = fb_crossposter_service.delete_facebook_video("2516067995540325", "EAAToken")
            self.assertTrue(res.get("success"))
            self.assertTrue(res.get("already_deleted"))

    def test_cleanup_failed_meta_video_when_already_missing_on_meta(self):
        item = {
            "id": 93,
            "target_page_id": "page93",
            "fb_post_id": "missing_vid",
            "upload_video_id": "",
            "scheduled_publish_time": 0,
            "status": "meta_failed",
        }
        with (
            patch.object(db, "get_fb_crossposter_queue_item", return_value=item),
            patch.object(
                db,
                "get_fb_crossposter_runtime_settings",
                return_value={"target_access_token": "token", "target_gpm_profile_id": ""},
            ),
            patch.object(
                fb_crossposter_service,
                "reconcile_fb_queue_item",
                return_value={"status": "missing", "fb_post_id": "missing_vid"},
            ),
            patch.object(db, "reserve_next_fb_queue_slot", return_value=1799999999) as reserve_slot,
            patch.object(db, "append_fb_recovery_history"),
        ):
            result = fb_crossposter_service.cleanup_failed_meta_video_and_reschedule(93)
            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "scheduled")
            self.assertEqual(result["scheduled_publish_time"], 1799999999)
            reserve_slot.assert_called_once_with(93, minimum_lead_minutes=30, clear_meta_object=True)

    def test_reserve_next_fb_queue_slot_respects_max_horizon(self):
        db.save_fb_crossposter_settings({
            "target_fb_page_id": "page_horizon_test",
            "schedule_times": ["10:00", "16:00"],
            "daily_quota": 2,
            "lead_time_minutes": 30,
        })
        items = [{
            "youtube_id": "yt_horizon_1",
            "original_title": "Test Horizon 1",
            "sort_order": 1,
            "target_page_id": "page_horizon_test",
        }]
        db.upsert_fb_crossposter_queue_items(items, target_page_id="page_horizon_test")
        item_row = db.get_fb_crossposter_queue(target_page_id="page_horizon_test")["items"][0]
        slot = db.reserve_next_fb_queue_slot(item_row["id"], max_horizon_days=70)
        now_ts = int(datetime.datetime.now().timestamp())
        self.assertGreater(slot, now_ts + 1800)
        self.assertLessEqual(slot, now_ts + (70 * 86400) + 86400)

    def test_get_next_queue_items_for_pre_schedule_orders_chronologically(self):
        page_id = "page_presched_sort"
        now_ts = int(datetime.datetime.now().timestamp())
        items = [
            {"youtube_id": "yt_far", "original_title": "Far Future", "sort_order": 1, "target_page_id": page_id},
            {"youtube_id": "yt_near", "original_title": "Near Future", "sort_order": 2, "target_page_id": page_id},
            {"youtube_id": "yt_unset", "original_title": "Unset Time", "sort_order": 3, "target_page_id": page_id},
        ]
        db.upsert_fb_crossposter_queue_items(items, target_page_id=page_id)
        rows = db.get_fb_crossposter_queue(target_page_id=page_id)["items"]
        row_map = {r["youtube_id"]: r["id"] for r in rows}

        # Set scheduled times
        db.update_fb_crossposter_queue_item(row_map["yt_far"], {"scheduled_publish_time": now_ts + 86400 * 10, "status": "scheduled"})
        db.update_fb_crossposter_queue_item(row_map["yt_near"], {"scheduled_publish_time": now_ts + 86400 * 2, "status": "scheduled"})
        db.update_fb_crossposter_queue_item(row_map["yt_unset"], {"scheduled_publish_time": 0, "status": "pending"})

        fetched = db.get_next_queue_items_for_pre_schedule(page_id, count=3)
        self.assertEqual(len(fetched), 3)
        # yt_near (2 days away) should come first, then yt_far (10 days away), then yt_unset
        self.assertEqual(fetched[0]["youtube_id"], "yt_near")
        self.assertEqual(fetched[1]["youtube_id"], "yt_far")
        self.assertEqual(fetched[2]["youtube_id"], "yt_unset")

    def test_reconcile_auto_finishes_resumable_session(self):
        page_id = "page_autofinish"
        now_ts = int(datetime.datetime.now().timestamp())
        items = [{
            "youtube_id": "yt_auto_finish",
            "original_title": "Auto Finish Video",
            "sort_order": 1,
            "target_page_id": page_id,
        }]
        db.upsert_fb_crossposter_queue_items(items, target_page_id=page_id)
        item_id = db.get_fb_crossposter_queue(target_page_id=page_id)["items"][0]["id"]
        db.update_fb_crossposter_queue_item(item_id, {
            "upload_session_id": "sess_12345",
            "upload_video_id": "meta_vid_12345",
            "upload_phase": "finish_failed",
            "scheduled_publish_time": now_ts + 7200,
            "status": "processing",
        })

        meta_before = {
            "id": "meta_vid_12345",
            "published": True,
            "status": {"video_status": "uploading", "uploading_phase": {"status": "in_progress"}},
        }
        meta_after = {
            "id": "meta_vid_12345",
            "published": False,
            "scheduled_publish_time": datetime.datetime.fromtimestamp(now_ts + 7200, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "status": {"video_status": "processing", "uploading_phase": {"status": "complete"}},
        }

        opener = MagicMock()
        resp = MagicMock()
        resp.read.return_value = json.dumps({"success": True}).encode("utf-8")
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = None
        opener.open.return_value = resp

        with (
            patch.object(db, "get_fb_crossposter_runtime_settings", return_value={"target_access_token": "token", "target_gpm_profile_id": ""}),
            patch.object(fb_crossposter_service, "_get_proxy_for_gpm_profile", return_value=None),
            patch.object(fb_crossposter_service, "_build_urllib_opener", return_value=opener),
            patch.object(fb_crossposter_service, "get_facebook_video_metadata", side_effect=[meta_before, meta_after]),
        ):
            res = fb_crossposter_service.reconcile_fb_queue_item(item_id)
            self.assertEqual(res["status"], "meta_scheduled")
            self.assertEqual(res["fb_post_id"], "meta_vid_12345")


if __name__ == "__main__":
    unittest.main()
