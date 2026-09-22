import sys
sys.path.insert(0, 'src')
from auto_yt.services.chatgpt_worker import split_outline_parts

# Test with an outline that has intro/outro sections included
test_outline = """[PHAN]
Mo bai: Xin chao quy vi khan gia dang theo doi kenh

[PHAN]
Noi dung chinh 1: Phan thu nhat cua video

[PHAN]
Noi dung chinh 2: Phan thu hai cua video

[PHAN]
Ket bai: Cam on quy vi da xem video"""

parts = split_outline_parts(test_outline)
print(f"split_outline_parts returned {len(parts)} parts:")
for i, p in enumerate(parts):
    print(f"  [{i}]: {p[:80]}")

# Test the filter
print("\n--- Testing filter ---")
filtered = []
removed = 0
for part in parts:
    header = part.lower()[:100]
    if any(x in header for x in ["mo bai", "mo dau", "intro", "ket bai", "ket luan", "outro"]):
        removed += 1
        print(f"  REMOVED: {part[:60]}")
    else:
        filtered.append(part)

print(f"\nAfter filter: {len(filtered)} body parts, removed {removed}")
