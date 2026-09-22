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

# Let's test updating with custom_labels
update_url = f'https://graph.facebook.com/v26.0/{video_id}'
test_data = {
    'access_token': token,
    'custom_labels': json.dumps(["Lịch sử Việt Nam", "Thiếu tướng Kim Tuấn", "Khmer Đỏ", "Chiến tranh Tây Nam"]),
}
req = urllib.request.Request(
    update_url,
    data=urllib.parse.urlencode(test_data).encode('utf-8'),
    headers={'User-Agent': 'NexusStudio/1.0', 'Content-Type': 'application/x-www-form-urlencoded'},
    method='POST'
)
try:
    with urllib.request.urlopen(req) as resp:
        print("Update custom_labels result:", json.loads(resp.read().decode('utf-8')))
except Exception as exc:
    print("Update custom_labels ERROR:", security_logging.redact_sensitive(exc))

# Let's check the video fields again
fields = 'id,title,description,content_tags,custom_labels,content_category'
query = urllib.parse.urlencode({'fields': fields})
url = f'https://graph.facebook.com/v26.0/{video_id}?{query}'
try:
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(request) as resp:
        print("GET video fields:", json.loads(resp.read().decode('utf-8')))
except Exception as exc:
    print("GET video fields ERROR:", security_logging.redact_sensitive(exc))
