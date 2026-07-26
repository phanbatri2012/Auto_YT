"""
Standalone script to send a prompt to ChatGPT using the authenticated Playwright session.
"""

import asyncio
import sys
from pathlib import Path

# Configure stdout for utf-8 to support emojis on Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Add src to sys.path so we can import auto_yt paths
sys.path.insert(0, str(Path(__file__).parent / "src"))

from playwright.async_api import async_playwright
from auto_yt.paths import gpt_profile_dir, DATA_DIR

DEFAULT_GPT_PROFILE = "PROFILE_GPT_1"

async def prompt_chatgpt(prompt_text: str):
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        print(f"Error: Profile directory {profile_dir} not found.")
        print("Please run the auto-login tool first to create an authenticated session.")
        return

    print("🚀 Launching browser with existing session...")
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False, # Set to True if Cloudflare is not blocking after login
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        
        print("🌐 Navigating to ChatGPT...")
        await page.goto("https://chatgpt.com", wait_until="domcontentloaded")
        
        print("⏳ Waiting for chat interface...")
        # ChatGPT's prompt textarea
        prompt_textarea = page.locator('#prompt-textarea').first
        try:
            await prompt_textarea.wait_for(state="visible", timeout=20000)
        except Exception:
            print("❌ Error: Could not find the prompt textarea. Are you sure you are logged in?")
            await context.close()
            return
            
        print(f"✍️ Sending prompt: '{prompt_text}'")
        await prompt_textarea.fill(prompt_text)
        await asyncio.sleep(0.5)
        
        # Click the send button
        send_btn = page.locator('[data-testid="send-button"]').first
        await send_btn.click()
        
        print("⏳ Waiting for response to complete...")
        
        # We wait for the assistant's message container to appear
        assistant_messages = page.locator('[data-message-author-role="assistant"]')
        try:
            # Wait for at least one assistant message to be visible
            await assistant_messages.last.wait_for(state="visible", timeout=15000)
        except Exception:
            print("❌ Error: Assistant message did not appear.")
            await context.close()
            return
            
        # Now wait for generation to finish. We can tell generation is finished when the stop button is gone
        stop_btn = page.locator('[data-testid="stop-button"]')
        try:
            if await stop_btn.count() > 0:
                await stop_btn.last.wait_for(state="hidden", timeout=60000)
        except Exception:
            pass
            
        # Wait a moment for markdown elements to fully render
        await asyncio.sleep(1)
        
        print("✅ Response generation complete!")
        
        # Extract the text of the LAST assistant message
        last_message = assistant_messages.last
        markdown_div = last_message.locator('.markdown').first
        
        if await markdown_div.count() > 0:
            response_text = await markdown_div.inner_text()
        else:
            response_text = await last_message.inner_text()
            
        print("\n" + "="*50)
        print("🤖 ChatGPT says:")
        print("="*50)
        print(response_text)
        print("="*50 + "\n")
        
        # Save to file
        out_file = DATA_DIR / "latest_response.txt"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out_file.write_text(response_text, encoding="utf-8")
        print(f"💾 Response saved to {out_file}")
        
        await context.close()

if __name__ == "__main__":
    test_question = "Tell me a short joke about programming in 1 sentence."
    # If the user provides an argument, use that as the prompt
    if len(sys.argv) > 1:
        test_question = " ".join(sys.argv[1:])
        
    asyncio.run(prompt_chatgpt(test_question))
