import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import unittest
from auto_yt.services.chatgpt_worker import (
    clean_text,
    remove_citation_artifacts,
    sanitize_narrative_response,
    sanitize_generated_script,
)


class TestSanitizeCitations(unittest.TestCase):
    def test_remove_citation_pill_artifacts(self):
        # Digital Library +1 artifact attached to line
        raw = (
            "Liên minh tiếp tục nhận được sự hậu thuẫn ở những mức độ khác nhau từ Trung Quốc, Hoa Kỳ.\n"
            "Digital Library\n"
            "+1\n\n"
            "Bên trong Khmer Đỏ, sự suy yếu ngày càng lộ rõ."
        )
        cleaned = remove_citation_artifacts(raw)
        self.assertNotIn("Digital Library", cleaned)
        self.assertNotIn("+1", cleaned)
        self.assertIn("Liên minh tiếp tục", cleaned)
        self.assertIn("Bên trong Khmer Đỏ", cleaned)

    def test_remove_wikipedia_citation_pill(self):
        raw = "Chiến dịch kết thúc thắng lợi.\nWikipedia\n+3\n\nToàn bộ lực lượng rút lui an toàn."
        cleaned = remove_citation_artifacts(raw)
        self.assertNotIn("Wikipedia", cleaned)
        self.assertNotIn("+3", cleaned)
        self.assertIn("Chiến dịch kết thúc thắng lợi.", cleaned)
        self.assertIn("Toàn bộ lực lượng rút lui an toàn.", cleaned)

    def test_remove_openai_internal_source_tags(self):
        raw = "Vào năm 1979【4:0†source】, quân tình nguyện Việt Nam đã tiến vào Phnom Penh【12†source】."
        cleaned = remove_citation_artifacts(raw)
        self.assertEqual(
            cleaned,
            "Vào năm 1979, quân tình nguyện Việt Nam đã tiến vào Phnom Penh.",
        )

    def test_remove_search_status_lines(self):
        raw = (
            "Searched 4 sites\n"
            "Đã tìm kiếm 5 trang web:\n"
            "Thành phố Phnom Penh được giải phóng ngày 7 tháng 1 năm 1979."
        )
        cleaned = clean_text(raw)
        self.assertNotIn("Searched 4 sites", cleaned)
        self.assertNotIn("Đã tìm kiếm", cleaned)
        self.assertIn("Thành phố Phnom Penh được giải phóng", cleaned)

    def test_remove_isolated_plus_count_line(self):
        raw = "Đoạn văn thứ nhất.\n\n+2\n\nĐoạn văn thứ hai."
        cleaned = clean_text(raw)
        self.assertNotIn("+2", cleaned)
        self.assertIn("Đoạn văn thứ nhất.", cleaned)
        self.assertIn("Đoạn văn thứ hai.", cleaned)

    def test_sanitize_generated_script_with_citations(self):
        script = (
            "### [INTRO]\n"
            "Chào mừng quý vị đến với phân tích lịch sử.\n\n"
            "### [BODY]\n"
            "Liên minh nhận được sự hậu thuẫn quốc tế.\n"
            "Digital Library\n"
            "+1\n\n"
            "Bên trong Khmer Đỏ, sự suy yếu ngày càng lộ rõ.\n\n"
            "### [OUTRO]\n"
            "Cảm ơn quý vị đã theo dõi video.\n\n"
            "### [TIÊU ĐỀ]\n"
            "PÔN PỐT VÀ SỰ SỤP ĐỔ CỦA KHMER ĐỎ\n"
        )
        result = sanitize_generated_script(script)
        self.assertNotIn("Digital Library", result)
        self.assertNotIn("+1", result)
        self.assertIn("Liên minh nhận được sự hậu thuẫn quốc tế.", result)
        self.assertIn("Bên trong Khmer Đỏ, sự suy yếu ngày càng lộ rõ.", result)
        self.assertIn("### [TIÊU ĐỀ]\nPÔN PỐT VÀ SỰ SỤP ĐỔ CỦA KHMER ĐỎ", result)


if __name__ == "__main__":
    unittest.main()
