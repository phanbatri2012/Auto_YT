import sys, re, uuid, base64
sys.path.insert(0, 'src')
from auto_yt.services import database as db
from auto_yt.paths import THUMBNAILS_DIR, gpt_profile_dir
from playwright.sync_api import sync_playwright

THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

img1_chatgpt = 'https://chatgpt.com/backend-api/estuary/content?id=file_00000000df3082079be3292c2a4fc530&ts=495705&p=fs&cid=1&sig=e03454881ac25093cf98565dad2f3becbfaa33b93ba9942cbaa535eb5c65984d&v=0'
img2_chatgpt = 'https://chatgpt.com/backend-api/estuary/content?id=file_000000000d3081faaff28e22e21c9875&ts=495705&p=fs&cid=1&sig=e35a80551ae03b316b1517cfc3d81cb4b8cf2bb7b41fa59551617a2718c46d3f&v=0'

profile_dir = gpt_profile_dir('PROFILE_GPT_1')

JS_FETCH = """
async (url) => {
    const res = await fetch(url, { credentials: 'include' });
    const buf = await res.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
}
"""

def download(page, url):
    try:
        b64 = page.evaluate(JS_FETCH, url)
        filename = f'thumb_{uuid.uuid4().hex[:12]}.png'
        (THUMBNAILS_DIR / filename).write_bytes(base64.b64decode(b64))
        local = f'/api/thumbnails/{filename}'
        print(f'OK: {local}')
        return local
    except Exception as e:
        print(f'FAIL: {e}')
        return ''

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        str(profile_dir), headless=True,
        args=['--disable-blink-features=AutomationControlled'],
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto('https://chatgpt.com', wait_until='domcontentloaded')

    p1 = download(page, img1_chatgpt)
    p2 = download(page, img2_chatgpt)
    context.close()

if p1 and p2:
    v = db.get_video(3)
    script = v['generated_script']
    script = re.sub(
        r'### \[THUMBNAIL CÓ CHỮ\]\n.*?(?=\n### \[|\Z)',
        f'### [THUMBNAIL CÓ CHỮ]\n[IMAGE_URL:{p1}]\n',
        script, flags=re.DOTALL
    )
    script = re.sub(
        r'### \[THUMBNAIL KHÔNG CHỮ\]\n.*?(?=\n### \[|\Z)',
        f'### [THUMBNAIL KHÔNG CHỮ]\n[IMAGE_URL:{p2}]\n',
        script, flags=re.DOTALL
    )
    db.update_script(3, script)
    print('DB updated!')
else:
    print('Download failed, DB not updated.')
