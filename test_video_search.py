import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_yt.services import database


def build_script(title: str, description: str) -> str:
    return f"""
### [INTRO]
Nội dung mở đầu.

### [METADATA & QUIZ]
TIÊU ĐỀ: {title}

URL SLUG: video-moi

MÔ TẢ VIDEO:
{description}

HASHTAG: #HonNhan #GiaDinh

CÂU HỎI: Nội dung câu hỏi?

### [CHAPTERS]
00:00 - Mở đầu
""".strip()


class VideoSearchTests(unittest.TestCase):
    def test_searches_all_supported_fields_without_case_or_accents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "database.db"
            with patch.object(database, "DB_PATH", database_path):
                database.init_db()
                first_video_id = database.save_video(
                    "https://www.youtube.com/watch?v=first",
                    "Cô Dâu Trở Về",
                    "Transcript",
                    build_script(
                        "Bí Mật Trong Đêm",
                        "Hồ sơ gia đình hé lộ một sự thật bất ngờ.",
                    ),
                    prompt_version="family",
                )
                database.save_video(
                    "https://www.youtube.com/watch?v=second",
                    "Người Cha Già",
                    "Transcript",
                    build_script(
                        "Lời Hứa Cuối Cùng",
                        "Một cuộc đoàn tụ nhiều cảm xúc.",
                    ),
                    prompt_version="analysis",
                )

                for query in (
                    "https://www.youtube.com/watch?v=first",
                    "CO DAU TRO VE",
                    "bi mat trong dem",
                    "HO SO GIA DINH",
                ):
                    result = database.get_all_videos(search_query=query)
                    self.assertEqual(result["total"], 1)
                    self.assertEqual(result["items"][0]["id"], first_video_id)

    def test_search_combines_with_version_and_publication_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "database.db"
            with patch.object(database, "DB_PATH", database_path):
                database.init_db()
                published_id = database.save_video(
                    "https://www.youtube.com/watch?v=published",
                    "Video Một",
                    "Transcript",
                    build_script("Chuyện Nhà", "Bí mật gia đình được hé lộ."),
                    prompt_version="family",
                )
                database.toggle_published(published_id, 1)
                database.save_video(
                    "https://www.youtube.com/watch?v=unpublished",
                    "Video Hai",
                    "Transcript",
                    build_script("Chuyện Khác", "Bí mật gia đình chưa sáng tỏ."),
                    prompt_version="analysis",
                )

                result = database.get_all_videos(
                    is_published=1,
                    prompt_version="family",
                    search_query="bi mat gia dinh",
                )

                self.assertEqual(result["total"], 1)
                self.assertEqual(result["count_published"], 1)
                self.assertEqual(result["count_unpublished"], 0)
                self.assertEqual(result["items"][0]["id"], published_id)

    def test_updates_and_backfills_search_text_for_every_video(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "database.db"
            with patch.object(database, "DB_PATH", database_path):
                database.init_db()
                video_id = database.save_video(
                    "https://www.youtube.com/watch?v=generic",
                    "Tiêu Đề Gốc",
                    "Transcript",
                    build_script("Tiêu Đề Cũ", "Mô tả cũ."),
                )

                database.update_script(
                    video_id,
                    build_script("Tiêu Đề Mới", "Bằng chứng quan trọng."),
                )
                self.assertEqual(
                    database.get_all_videos(search_query="bang chung quan trong")[
                        "total"
                    ],
                    1,
                )
                self.assertEqual(
                    database.get_all_videos(search_query="mo ta cu")["total"],
                    0,
                )

                connection = sqlite3.connect(database_path)
                try:
                    connection.execute(
                        "UPDATE videos SET search_text = '' WHERE id = ?",
                        (video_id,),
                    )
                    connection.commit()
                finally:
                    connection.close()
                database.init_db()

                self.assertEqual(
                    database.get_all_videos(search_query="tieu de moi")["total"],
                    1,
                )


if __name__ == "__main__":
    unittest.main()
