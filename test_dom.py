import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            'data/chrome_user_data/PROFILE_GPT_1',
            headless=True
        )
        page = context.pages[0]
        print('Going to chatgpt...')
        await page.goto('https://chatgpt.com/c/6a5e1358-3a84-83ec-893e-f094519c3719', wait_until='networkidle')
        await page.wait_for_selector('article', timeout=15000)
        html = await page.evaluate('document.body.innerHTML')
        import re
        roles = re.findall(r'data-message-author-role="([^"]+)"', html)
        print('Roles found:', set(roles))
        if not roles:
            print('No data-message-author-role found. Let us find generic data attributes...')
        
        # Check how article messages are identified
        articles = await page.locator('article').count()
        print(f"Total articles (messages): {articles}")
        for i in range(articles):
            html = await page.locator('article').nth(i).evaluate('el => el.outerHTML')
            print(f"Article {i} snippet:", html[:150])
            
        await context.close()
asyncio.run(main())
