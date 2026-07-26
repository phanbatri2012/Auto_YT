# Tool Auto-login GPT

This is the standalone ChatGPT Auto Login tool extracted from `auto_YT`.

## What It Does

- Opens a PyQt6 desktop window: `ChatGPT Auto Login`.
- Provides inputs for Email, Password, and optional TOTP Secret.
- Provides buttons: Save, Auto Login, Clear Account, Open Profile.
- Uses Playwright persistent Chromium profile at `data/chrome_user_data/PROFILE_GPT_1`.
- Stores account/session data locally in `data/account.json` and `data/session_chatgpt.json`.
- Restores saved ChatGPT cookies before attempting a full login.
- Does not bypass CAPTCHA, Cloudflare, device verification, or MFA.

## Install On Windows

```powershell
cd Tool-auto-login-GPT
py -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

```powershell
.venv\Scripts\activate
python run_app.py
```

## Reset Session

Close the app, then delete:

```powershell
Remove-Item -Recurse -Force data\chrome_user_data\PROFILE_GPT_1
Remove-Item -Force data\account.json, data\session_chatgpt.json
```

## Notes

- `data/` is intentionally not included in the transfer package because it may contain local cookies, sessions, and account data.
- If ChatGPT asks for device verification, complete it manually in the opened browser or with the `Open Profile` button.
