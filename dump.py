import sys
import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://chatgpt.com', wait_until='networkidle')
    time.sleep(3)
    html = page.evaluate('() => { const el = document.querySelector("#prompt-textarea"); return el ? el.parentElement.innerHTML : "Not found"; }')
    with open('chat_input_html.txt', 'w', encoding='utf-8') as f:
        f.write(html)
    browser.close()
