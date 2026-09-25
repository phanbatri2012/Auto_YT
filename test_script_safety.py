import sys
import unittest
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from auto_yt.services.chatgpt_worker import split_outline_parts


class ScriptSafetyTests(unittest.TestCase):
    def test_split_outline_parts_standard_phan(self):
        outline = """[PHAN]
Đoạn 1 nội dung câu chuyện lịch sử.

[PHAN]
Đoạn 2 diễn biến trận đánh.

[PHAN]
Đoạn 3 kết cục của chiến dịch."""
        parts = split_outline_parts(outline)
        self.assertEqual(len(parts), 3)
        self.assertIn("Đoạn 1", parts[0])
        self.assertIn("Đoạn 2", parts[1])
        self.assertIn("Đoạn 3", parts[2])

    def test_split_outline_parts_vietnamese_phan(self):
        outline = """[PHẦN]
Đoạn 1 với nhãn tiếng Việt có dấu.

[Phần]
Đoạn 2 với nhãn viết hoa/thường.

[PHAN]
Đoạn 3 với nhãn không dấu."""
        parts = split_outline_parts(outline)
        self.assertEqual(len(parts), 3)
        self.assertIn("Đoạn 1", parts[0])
        self.assertIn("Đoạn 2", parts[1])
        self.assertIn("Đoạn 3", parts[2])

    def test_split_outline_parts_numbered_headers(self):
        outline = """PHẦN 1: Bối cảnh ban đầu
Nội dung phần 1.

PHẦN 2: Diễn biến tiếp theo
Nội dung phần 2.

PHẦN 3: Kết thúc
Nội dung phần 3."""
        parts = split_outline_parts(outline)
        self.assertEqual(len(parts), 3)

    def test_split_outline_parts_with_writing_artifacts(self):
        outline = """:::writing{variant="document" id="58314" title="Nội dung chia phần"}
[PHAN]
Pol Pot từng đứng đầu một chế độ...

[PHAN]
Pol Pot tên thật là Saloth Sar..."""
        parts = split_outline_parts(outline)
        self.assertEqual(len(parts), 2)
        self.assertNotIn(":::writing", parts[0])
        self.assertIn("Pol Pot từng đứng đầu", parts[0])
    def test_split_outline_parts_pol_pot_27_parts(self):
        pol_pot_sample = """[PHAN]
Phan 1
[PHAN]
Phan 2
[PHAN]
Phan 3
[PHAN]
Phan 4
[PHAN]
Phan 5
[PHAN]
Phan 6
[PHAN]
Phan 7
[PHAN]
Phan 8
[PHAN]
Phan 9
[PHAN]
Phan 10
[PHAN]
Phan 11
[PHAN]
Phan 12
[PHAN]
Phan 13
[PHAN]
Phan 14
[PHAN]
Phan 15
[PHAN]
Phan 16
[PHAN]
Phan 17
[PHAN]
Phan 18
[PHAN]
Phan 19
[PHAN]
Phan 20
[PHAN]
Phan 21
[PHAN]
Phan 22
[PHAN]
Phan 23
[PHAN]
Phan 24
[PHAN]
Phan 25
[PHAN]
Phan 26
[PHAN]
Phan 27"""
        parts = split_outline_parts(pol_pot_sample)
        self.assertEqual(len(parts), 27)


if __name__ == "__main__":
    unittest.main()
