from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

# --- Constants -----------------------------------------------------------

CHATGPT_URL = "https://chatgpt.com"
AUTH_DOMAIN = "auth.openai.com"

# Timeouts (ms)
NAVIGATION_TIMEOUT_MS = 60_000
CLOUDFLARE_WAIT_MS = 15_000   # max wait for Cloudflare challenge to clear
LOGIN_BUTTON_TIMEOUT_MS = 10_000
INPUT_TIMEOUT_MS = 10_000
POST_LOGIN_TIMEOUT_MS = 45_000

# Selectors – stable attributes only, no react-aria-* IDs
SEL_LOGIN_BUTTON = (
    'button:has-text("Log in"), '
    'a:has-text("Log in"), '
    '[data-testid="login-button"]'
)
SEL_EMAIL_INPUT    = 'input[name="email"], input[autocomplete="email"]'
SEL_PASSWORD_INPUT = 'input[name="current-password"], input[name="password"], input[name="Passwd"]'
SEL_SUBMIT_BUTTON  = 'button[type="submit"]'
SEL_MFA_INPUT      = 'input[inputmode="numeric"], input[name="code"], input[autocomplete="one-time-code"]'
SEL_PROFILE_BUTTON = '[data-testid="accounts-profile-button"]'
SEL_BOOTSTRAP      = 'script#client-bootstrap'

# OpenAI/Auth0 error selectors observed in the current auth.openai.com flow.
# Wrong password appears as text inside li._error_* without role=alert/data-testid.
SEL_ERROR_TEXT = (
    'text=/Incorrect email address or password|'
    'Invalid code|Incorrect code|Code expired|Try again|'
    'Verify you are human|Checking if the site connection is secure|'
    'verify your identity|verify your email|device verification/i'
)
SEL_CLOUDFLARE_OR_CAPTCHA = (
    'iframe[src*="turnstile"], '
    'iframe[src*="challenges"], '
    'iframe[src*="captcha"], '
    'iframe[title*="Cloudflare"], '
    'iframe[title*="Widget"], '
    '#cf-turnstile, '
    '.cf-turnstile, '
    '[data-testid*="captcha"]'
)

SEL_COOKIE_BANNER_ACCEPT = 'button:has-text("Accept all"), button:has-text("Reject non-essential")'

# Cloudflare injects this title while the JS challenge runs
_CLOUDFLARE_TITLES = {"just a moment...", "checking your browser"}


class ChatGPTLoginError(Exception):
    """Raised when login fails for a known reason."""


class ChatGPTLoginCredentialError(ChatGPTLoginError):
    """Wrong email or password."""


class ChatGPTLoginMFAError(ChatGPTLoginError):
    """MFA required but no totp_secret provided, or TOTP rejected."""


class ChatGPTLoginVerifyError(ChatGPTLoginError):
    """Anti-bot / captcha / extra verification required."""


class ChatGPTLoginDeviceVerificationError(ChatGPTLoginError):
    """New-device verification or email confirmation required."""


# --- Helpers -------------------------------------------------------------

async def _dismiss_cookie_banner(page) -> None:
    """Dismiss the cookie consent banner if present (blocks click on login)."""
    try:
        btn = page.locator(SEL_COOKIE_BANNER_ACCEPT).first
        if await btn.is_visible(timeout=500):
            await btn.click(timeout=500)
            logger.info("Dismissed cookie consent banner")
            await asyncio.sleep(0.2)
    except Exception:
        pass


async def _wait_past_cloudflare(page, timeout_ms: int = CLOUDFLARE_WAIT_MS) -> None:
    """
    Poll until the page title is no longer the Cloudflare challenge title.
    Raises ChatGPTLoginVerifyError if the challenge doesn't clear in time.
    This is a best-effort guard; the challenge self-resolves in headed mode.
    """
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        if not await _is_cloudflare_or_captcha(page):
            return
        await asyncio.sleep(1)
    raise ChatGPTLoginVerifyError(
        "Cloudflare challenge did not clear. "
        "Run in headed mode (headless=False) and ensure the browser is not detected."
    )


async def _is_cloudflare_or_captcha(page) -> bool:
    """Return True when Cloudflare/Turnstile/captcha challenge is visible."""
    try:
        title = (await page.title()).strip().lower()
        if title in _CLOUDFLARE_TITLES:
            return True
    except Exception:
        pass

    try:
        if await page.locator(SEL_CLOUDFLARE_OR_CAPTCHA).count() > 0:
            return True
    except Exception:
        pass

    try:
        body = (await page.locator("body").first.inner_text(timeout=1_000)).lower()
        return any(
            text in body
            for text in (
                "verify you are human",
                "checking if the site connection is secure",
                "cloudflare",
                "captcha",
            )
        )
    except Exception:
        return False


async def _raise_if_cloudflare_or_captcha(page) -> None:
    if await _is_cloudflare_or_captcha(page):
        title = await page.title()
        raise ChatGPTLoginVerifyError(
            f"Cloudflare/captcha challenge detected. URL: {page.url}; title: {title}"
        )


async def _get_visible_error_text(page) -> str | None:
    """Return a visible auth error text from common OpenAI/Auth0 locations."""
    error_selectors = (
        '[role="alert"]',
        '[data-testid*="error"]',
        '[aria-live="assertive"]',
        '[aria-live="polite"]',
        'li[class*="error"]',
        'div[class*="error"]',
        SEL_ERROR_TEXT,
    )
    for selector in error_selectors:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=800):
                text = (await locator.inner_text(timeout=1_000)).strip()
                if text:
                    return text
        except Exception:
            continue
    return None


async def _raise_known_auth_error(page, stage: str) -> None:
    """Raise a precise exception for known auth error states."""
    await _raise_if_cloudflare_or_captcha(page)

    text = await _get_visible_error_text(page)
    if not text:
        return

    lower = text.lower()
    if "incorrect email address or password" in lower or "incorrect password" in lower:
        raise ChatGPTLoginCredentialError(f"Wrong email or password ({stage}): {text}")
    if any(word in lower for word in ("invalid code", "incorrect code", "code expired", "try again")):
        raise ChatGPTLoginMFAError(f"TOTP rejected or expired ({stage}): {text}")
    if any(word in lower for word in ("verify your email", "verify your identity", "device verification", "new device")):
        raise ChatGPTLoginDeviceVerificationError(f"Device/email verification required ({stage}): {text}")
    if any(word in lower for word in ("verify you are human", "cloudflare", "captcha")):
        raise ChatGPTLoginVerifyError(f"Human verification required ({stage}): {text}")

    raise ChatGPTLoginError(f"Auth error ({stage}): {text}")


async def _fill_and_submit(page, selector: str, value: str, timeout_ms: int = INPUT_TIMEOUT_MS) -> None:
    """Wait for a visible input, fill it, then click the submit button or press Enter."""
    field = page.locator(selector).first
    await field.wait_for(state="visible", timeout=timeout_ms)
    await field.fill(value)
    await asyncio.sleep(0.3)
    # Press Enter (robust for Google SSO and many forms)
    await field.press("Enter")
    # Fallback to click if a submit button is explicitly visible
    try:
        submit = page.locator(SEL_SUBMIT_BUTTON).first
        if await submit.is_visible(timeout=500):
            await submit.click(timeout=500)
    except Exception:
        pass


async def _get_bootstrap_data(page) -> dict | None:
    """Parse the JSON inside <script id='client-bootstrap'>."""
    try:
        content = await page.locator(SEL_BOOTSTRAP).first.inner_text(timeout=3_000)
        return json.loads(content)
    except Exception:
        return None


async def _verify_logged_in(page) -> dict:
    """
    Return {"logged_in": bool, "user": dict | None}.

    Primary:  client-bootstrap authStatus == "logged_in".
    Fallback: profile button visible + not on auth domain.
    """
    bootstrap = await _get_bootstrap_data(page)
    if bootstrap:
        auth_status = bootstrap.get("authStatus")
        session = bootstrap.get("session")
        if auth_status == "logged_in" and isinstance(session, dict):
            user_info = session.get("user") or {}
            return {
                "logged_in": True,
                "user": {
                    "email": user_info.get("email", ""),
                    "name":  user_info.get("name", ""),
                    "plan":  user_info.get("plan", ""),
                },
            }
        if auth_status == "logged_out":
            return {"logged_in": False, "user": None}

    # DOM fallback
    try:
        await page.locator(SEL_PROFILE_BUTTON).first.wait_for(state="visible", timeout=5_000)
        if AUTH_DOMAIN not in page.url:
            return {"logged_in": True, "user": {"email": "", "name": "", "plan": ""}}
    except Exception:
        pass

    return {"logged_in": False, "user": None}


async def _detect_error_state(page) -> str | None:
    """Return visible error banner text, or None."""
    return await _get_visible_error_text(page)


# --- Main ----------------------------------------------------------------

async def login_gpt_auto(account: dict, page) -> dict:
    """
    Log in to chatgpt.com using Playwright.

    Parameters
    ----------
    account : dict
        Required keys: "email", "password".
        Optional key:  "totp_secret" (base-32 string) for MFA accounts.
    page : playwright.async_api.Page
        Playwright page from a dedicated browser context (one per account).
        The browser MUST be launched with headless=False to pass Cloudflare.

    Returns
    -------
    dict  {"success": True, "cookies": list[dict], "user": dict}
        cookies — full context cookie jar (all domains)
        user    — {"email": str, "name": str, "plan": str}

    Raises
    ------
    ChatGPTLoginCredentialError  — wrong email or password
    ChatGPTLoginMFAError         — MFA issue
    ChatGPTLoginVerifyError      — Cloudflare / captcha / anti-bot wall
    ChatGPTLoginError            — any other login failure
    """
    email       = str(account.get("email", "")).strip()
    password    = str(account.get("password", ""))
    totp_secret = str(account.get("totp_secret") or "").strip() or None

    if not email or not password:
        raise ChatGPTLoginError("Missing 'email' or 'password' in account dict.")

    context = page.context

    # --- Step 1: Navigate and wait past Cloudflare -----------------------
    logger.info("[1] Navigating to %s", CHATGPT_URL)
    await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    await _wait_past_cloudflare(page)
    await asyncio.sleep(1)

    # Already logged in? (e.g. existing cookies from a previous run)
    verify = await _verify_logged_in(page)
    if verify["logged_in"]:
        logger.info("[1] Already logged in as %s", verify["user"])
        return {"success": True, "cookies": await context.cookies(), "user": verify["user"]}

    # --- Step 2: Click "Log in" ------------------------------------------
    logger.info("[2] Clicking Log in")
    await _dismiss_cookie_banner(page)
    # Close sidebar if open — it overlaps the login button
    try:
        close_sidebar = page.locator('[data-testid="close-sidebar-button"]').first
        if await close_sidebar.is_visible(timeout=500):
            await close_sidebar.click(timeout=500)
            await asyncio.sleep(0.2)
    except Exception:
        pass
    # Use only the specific testid button (avoids the header duplicate)
    login_btn = page.locator('[data-testid="login-button"], button:has-text("Log in")').first
    email_input = page.locator(SEL_EMAIL_INPUT).first

    # Robust SPA click-and-wait loop
    form_appeared = False
    for attempt in range(4):
        try:
            if await email_input.is_visible(timeout=500):
                form_appeared = True
                break
        except Exception:
            pass
            
        try:
            if await login_btn.is_visible(timeout=2000):
                if attempt % 2 == 1:
                    await login_btn.dispatch_event("click")
                else:
                    await login_btn.click(timeout=2000)
        except Exception:
            pass 
            
        try:
            await email_input.wait_for(state="visible", timeout=3000)
            form_appeared = True
            break
        except Exception:
            pass

    if not form_appeared:
        try:
            title = await page.title()
        except Exception:
            title = "Unknown (Browser closed?)"
        
        try:
            url = page.url
        except Exception:
            url = "Unknown"
            
        raise ChatGPTLoginVerifyError(
            f"Login button clicked but email form did not appear after retries. "
            f"URL: {url}; title: {title}"
        )
        
    await asyncio.sleep(0.2)

    # --- Step 3: Email ---------------------------------------------------
    logger.info("[3] Entering email")
    await _fill_and_submit(page, SEL_EMAIL_INPUT, email)
    await asyncio.sleep(1.5)

    await _raise_known_auth_error(page, "email")

    # --- Step 4: Password ------------------------------------------------
    logger.info("[4] Entering password (and handling SSO intermediate screens)")
    
    password_input = page.locator(SEL_PASSWORD_INPUT).first
    for _ in range(8):
        try:
            if await password_input.is_visible(timeout=1500):
                break
        except Exception:
            pass
            
        # 1. Try clicking the account identifier if presented (Google "Choose an account")
        try:
            picker = page.locator(f'[data-identifier="{email}"], [data-email="{email}"]').first
            if await picker.is_visible(timeout=500):
                await picker.click(timeout=1000)
                await asyncio.sleep(1.5)
                continue
        except Exception:
            pass
            
        # 2. Check for Google "Next" button on the identifier screen (if email is already filled/shown)
        try:
            next_btn = page.locator('#identifierNext button').first
            if await next_btn.is_visible(timeout=500):
                await next_btn.click(timeout=1000)
                await asyncio.sleep(1.5)
                continue
        except Exception:
            pass
            
        # 3. If Google asks for email again
        try:
            email_field2 = page.locator('input[type="email"], input[name="identifier"]').first
            if await email_field2.is_visible(timeout=500):
                await email_field2.fill(email)
                await email_field2.press("Enter")
                await asyncio.sleep(1.5)
                continue
        except Exception:
            pass

    try:
        await _fill_and_submit(page, SEL_PASSWORD_INPUT, password)
    except Exception as exc:
        await _raise_known_auth_error(page, "password-form")
        raise ChatGPTLoginError(f"Password field not found: {exc}") from exc

    await asyncio.sleep(2)

    await _raise_known_auth_error(page, "password")

    # --- Step 5: MFA (TOTP) if prompted ----------------------------------
    try:
        mfa_input = page.locator(SEL_MFA_INPUT).first
        await mfa_input.wait_for(state="visible", timeout=5_000)
        # MFA prompt appeared
        if not totp_secret:
            raise ChatGPTLoginMFAError("MFA form appeared but no totp_secret in account dict.")
        import pyotp  # lazy — only needed when MFA is active
        totp_code = pyotp.TOTP(totp_secret).now()
        logger.info("[5] Submitting TOTP code")
        await mfa_input.fill(totp_code)
        await asyncio.sleep(0.3)
        await page.locator(SEL_SUBMIT_BUTTON).first.click()
        await asyncio.sleep(2)
        await _raise_known_auth_error(page, "mfa")
    except (ChatGPTLoginMFAError, ChatGPTLoginError, ChatGPTLoginDeviceVerificationError):
        raise
    except Exception:
        # No MFA form — normal flow
        pass

    # --- Step 6: Wait for redirect to chatgpt.com ------------------------
    logger.info("[6] Waiting for redirect back to chatgpt.com")
    try:
        await page.wait_for_url("**/chatgpt.com/**", timeout=POST_LOGIN_TIMEOUT_MS)
    except Exception:
        if AUTH_DOMAIN in page.url:
            await _raise_known_auth_error(page, "redirect")
            raise ChatGPTLoginVerifyError(
                f"Still on {AUTH_DOMAIN} after timeout. URL: {page.url}"
            )

    await asyncio.sleep(2)

    # --- Step 7: Verify --------------------------------------------------
    logger.info("[7] Verifying login")
    verify = await _verify_logged_in(page)
    if not verify["logged_in"]:
        # NextAuth hydration can lag — one reload retry
        await page.reload(wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        await asyncio.sleep(3)
        verify = await _verify_logged_in(page)

    if not verify["logged_in"]:
        raise ChatGPTLoginError(
            "Login flow completed but authStatus is not 'logged_in'. "
            "Possible anti-bot block or session issue."
        )

    # --- Step 8: Collect cookies -----------------------------------------
    cookies = await context.cookies()
    logger.info("[8] Done — captured %d cookies for %s", len(cookies), email)

    return {"success": True, "cookies": cookies, "user": verify["user"]}


async def restore_session(cookies: list[dict], page) -> dict:
    """
    Restore a previously captured session by injecting saved cookies.

    Returns the same dict shape as login_gpt_auto.
    Raises ChatGPTLoginError if the cookies are expired / invalid.
    """
    context = page.context
    await context.add_cookies(cookies)
    await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    await _wait_past_cloudflare(page)
    await asyncio.sleep(2)

    verify = await _verify_logged_in(page)
    if not verify["logged_in"]:
        raise ChatGPTLoginError("Session restore failed — cookies expired or invalid.")

    return {"success": True, "cookies": await context.cookies(), "user": verify["user"]}


# TODO: Cấu trúc thư mục lưu trữ local (vd chrome_user_data/PROFILE_X) — paths.py đã có gpt_profile_dir()
