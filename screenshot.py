import time
from playwright.sync_api import sync_playwright
import os
import sys

# Add src to path so we can import auto_yt
sys.path.insert(0, os.path.abspath('src'))
from auto_yt.services.chatgpt_worker import gpt_profile_dir, DEFAULT_GPT_PROFILE

def take_screenshot():
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://chatgpt.com/c/6a5d955d-1974-83ec-940e-2ed4680d6d14", wait_until="domcontentloaded")
        time.sleep(10)
        page.screenshot(path="debug_chat2.png", full_page=True)
        context.close()

if __name__ == "__main__":
    take_screenshot()
