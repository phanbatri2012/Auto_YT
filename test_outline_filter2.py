import sys
sys.path.insert(0, 'src')
from auto_yt.services.chatgpt_worker import split_outline_parts

# Test with UTF-8 Vietnamese characters as they appear in actual ChatGPT output
test_outline_vn = """[PHAN]
Mở bài: Xin chào quý vị khán giả đang theo dõi kênh Thấu Hiểu Hôn Nhân

[PHAN]
Nội dung chính 1: Phần thứ nhất của video, câu chuyện bắt đầu từ đây và kể về hành trình dài

[PHAN]
Nội dung chính 2: Phần thứ hai tiếp theo với nhiều chi tiết hấp dẫn và sâu sắc hơn

[PHAN]
Kết bài: Cảm ơn quý vị đã xem video này"""

parts = split_outline_parts(test_outline_vn)
print(f"split_outline_parts returned {len(parts)} parts:")
for i, p in enumerate(parts):
    print(f"  [{i}]: {p[:100]}")

# Test the filter
print("\n--- Testing Vietnamese filter ---")
filtered = []
removed = 0
for part in parts:
    header = part.lower()[:100]
    print(f"  header: '{header[:60]}'")
    if any(x in header for x in ["mở bài", "mở đầu", "intro", "kết bài", "kết luận", "outro"]):
        removed += 1
        print(f"  REMOVED: {part[:60]}")
    else:
        filtered.append(part)

print(f"\nAfter filter: {len(filtered)} body parts, removed {removed}")
