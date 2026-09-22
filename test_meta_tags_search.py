import sys
import json
import urllib.request
import urllib.parse
import unittest

if __name__ != '__main__':
    raise unittest.SkipTest('manual Meta diagnostic script')

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'src')
from auto_yt.services import database as db, security_logging

db.init_db()
s = db.get_fb_crossposter_runtime_settings('827353423805386')
token = s['target_access_token'].strip().strip('\"\' *')

video_id = '2294832461313672'

# Let's search what interests exist for History, Military, Vietnam, Cambodia, etc.
keywords = ["Lịch sử", "Lịch sử Việt Nam", "Quân sự", "Chiến tranh", "Campuchia", "Khmer Đỏ", "Quân đội nhân dân Việt Nam"]
print("--- SEARCHING AD INTERESTS ---")
for kw in keywords:
    query = urllib.parse.urlencode({'type': 'adinterest', 'q': kw})
    url = f'https://graph.facebook.com/v26.0/search?{query}'
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print(f"Keyword '{kw}':", [(item.get('id'), item.get('name'), item.get('topic')) for item in data.get('data', [])[:3]])
    except Exception as exc:
        print(f"Keyword '{kw}' ERROR:", security_logging.redact_sensitive(exc))

# Let's check adinterestvalid for these keywords
print("\n--- VALIDATING WITH ADINTERESTVALID ---")
query_valid = urllib.parse.urlencode({
    'type': 'adinterestvalid',
    'interest_list': json.dumps(keywords, ensure_ascii=False),
})
url_valid = f'https://graph.facebook.com/v26.0/search?{query_valid}'
req_valid = urllib.request.Request(
    url_valid,
    headers={'Authorization': f'Bearer {token}'},
)
try:
    with urllib.request.urlopen(req_valid) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        print("adinterestvalid result:", data)
except Exception as exc:
    print("adinterestvalid ERROR:", security_logging.redact_sensitive(exc))
