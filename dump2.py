import sys
import time
from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto('https://chatgpt.com', wait_until='networkidle')
        time.sleep(5)
        # Take a screenshot
        page.screenshot(path='chatgpt_screenshot.png')
        
        # Dump HTML of the form area
        try:
            html = page.evaluate('() => { const form = document.querySelector("form"); return form ? form.innerHTML : "No form found"; }')
            with open('chatgpt_form.html', 'w', encoding='utf-8') as f:
                f.write(html)
        except Exception as e:
            print("Error:", e)
            
        browser.close()

if __name__ == '__main__':
    run()
