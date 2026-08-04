import sys
import time
import asyncio
import json
import os
import re
import uuid
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, Page
from auto_yt.paths import gpt_profile_dir, PROMPTS_PATH, THUMBNAILS_DIR
from auto_yt.default_prompts import DEFAULT_PROMPTS_DATA

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding='utf-8')

_HERE = __import__('pathlib').Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent.parent))

DEFAULT_GPT_PROFILE = "PROFILE_GPT_1"
CHATGPT_PROJECT_URL_ENV = "CHATGPT_PROJECT_URL"
DEFAULT_CHATGPT_PROJECT_URL = (
    "https://chatgpt.com/g/"
    "g-p-6a1f9204f2d88191b39b64eb7f2dbb97-dd-vn2-phan-tich/project"
)
PROFILE_WAIT_TIMEOUT_SECONDS = 20 * 60
PROFILE_RETRY_INTERVAL_SECONDS = 5
CHATGPT_RESPONSE_TIMEOUT_SECONDS = 20 * 60
THUMBNAIL_IMAGE_WAIT_TIMEOUT_SECONDS = 5 * 60
THUMBNAIL_TURN_WAIT_TIMEOUT_SECONDS = 30
THUMBNAIL_IMAGE_SELECTOR = (
    'img[src*="backend-api/estuary"], '
    'img[alt*="Generated image"], '
    'img[alt*="DALL"], '
    'img[src*="files/"]'
)
THUMBNAIL_RETRY_PROMPT = (
    "Sửa lại prompt sao cho không vi phạm. sau đó tạo lại thumbanil. "
    "chỉ cần xuất hình ảnh thumbnail."
)
PROFILE_BUSY_ERROR_MARKERS = (
    "Opening in existing browser session",
    "profile is already in use",
    "ProcessSingleton",
)
THUMBNAIL_GENERATION_ERROR_MARKERS = (
    "something went wrong",
    "please try again",
    "prompt may violate",
    "may violate our content",
    "may violate our guardrails",
    "content policies",
    "content policy",
    "image generation failed",
    "đã xảy ra lỗi",
    "hãy thử lại",
)


def is_thumbnail_generation_error_response(response_text: str) -> bool:
    normalized_response = response_text.strip().lower()
    return any(
        marker in normalized_response
        for marker in THUMBNAIL_GENERATION_ERROR_MARKERS
    )


def select_thumbnail_response_turn_number(
    visible_turns: list[tuple[int, str]],
    request_turn_number: int,
) -> int | None:
    response_turns = [
        turn_number
        for turn_number, role in visible_turns
        if role == "assistant" and turn_number > request_turn_number
    ]
    return min(response_turns, default=None)


def get_visible_conversation_turns(page: Page) -> list[tuple[int, str]]:
    visible_turns = []
    turns = page.locator('[data-testid^="conversation-turn-"]')
    for index in range(turns.count()):
        turn = turns.nth(index)
        test_id = turn.get_attribute("data-testid") or ""
        try:
            turn_number = int(test_id.rsplit("-", 1)[-1])
        except ValueError:
            continue

        role = turn.get_attribute("data-turn") or ""
        if not role:
            role_nodes = turn.locator("[data-message-author-role]")
            if role_nodes.count() > 0:
                role = (
                    role_nodes.first.get_attribute("data-message-author-role")
                    or ""
                )
        visible_turns.append((turn_number, role))
    return visible_turns


def get_latest_conversation_turn(page: Page, role: str) -> int:
    matching_turns = [
        turn_number
        for turn_number, turn_role in get_visible_conversation_turns(page)
        if turn_role == role
    ]
    return max(matching_turns, default=-1)


def wait_for_new_user_turn(page: Page, previous_user_turn: int) -> int:
    deadline = time.time() + THUMBNAIL_TURN_WAIT_TIMEOUT_SECONDS
    while time.time() < deadline:
        request_turn = get_latest_conversation_turn(page, "user")
        if request_turn > previous_user_turn:
            return request_turn
        time.sleep(0.25)
    raise RuntimeError("ChatGPT did not create a new thumbnail request turn.")


def wait_for_thumbnail_image(page: Page, request_turn: int, download_image) -> str:
    deadline = time.time() + THUMBNAIL_IMAGE_WAIT_TIMEOUT_SECONDS
    response_turn = None
    while time.time() < deadline:
        if response_turn is None:
            response_turn = select_thumbnail_response_turn_number(
                get_visible_conversation_turns(page),
                request_turn,
            )
        if response_turn is None:
            time.sleep(1)
            continue

        turn = page.locator(f'[data-testid="conversation-turn-{response_turn}"]')
        if turn.count() == 1:
            images = turn.locator(THUMBNAIL_IMAGE_SELECTOR)
            for index in range(images.count() - 1, -1, -1):
                image = images.nth(index)
                image_url = image.get_attribute("src") or ""
                image_ready = image.evaluate(
                    "image => image.complete && image.naturalWidth > 0"
                )
                generation_active = (
                    page.locator('[data-testid="stop-button"]').count() > 0
                )
                if image_url and image_ready and not generation_active:
                    return download_image(image_url) or image_url
        time.sleep(1)
    return ""


def send_thumbnail_prompt(page: Page, prompt_text: str, download_image) -> tuple[str, str]:
    previous_user_turn = get_latest_conversation_turn(page, "user")
    response_text = send_prompt(page, prompt_text)
    request_turn = wait_for_new_user_turn(page, previous_user_turn)
    if is_thumbnail_generation_error_response(response_text):
        return response_text, ""
    image_url = wait_for_thumbnail_image(page, request_turn, download_image)
    return response_text, image_url


def build_thumbnail_generation_prompt(
    base_prompt: str,
    thumbnail_type: str,
) -> str:
    return base_prompt


def get_chatgpt_project_url() -> str:
    project_url = os.environ.get(
        CHATGPT_PROJECT_URL_ENV,
        DEFAULT_CHATGPT_PROJECT_URL,
    ).strip()
    parsed_url = urlparse(project_url)
    path_parts = parsed_url.path.strip("/").split("/")
    is_project_url = (
        parsed_url.scheme == "https"
        and parsed_url.netloc == "chatgpt.com"
        and len(path_parts) == 3
        and path_parts[0] == "g"
        and path_parts[1].startswith("g-p-")
        and path_parts[2] == "project"
    )
    if not is_project_url:
        raise ValueError(
            f"{CHATGPT_PROJECT_URL_ENV} must be a ChatGPT Project URL."
        )
    return project_url.rstrip("/")


def ensure_expected_project_page(actual_url: str, project_url: str) -> None:
    actual = urlparse(actual_url)
    expected = urlparse(project_url)
    if (
        actual.scheme != expected.scheme
        or actual.netloc != expected.netloc
        or actual.path.rstrip("/") != expected.path.rstrip("/")
    ):
        raise RuntimeError(
            "ChatGPT did not stay on the configured Project page. "
            "No prompt was sent."
        )


def is_chatgpt_conversation_url(url: str) -> bool:
    parsed_url = urlparse(url)
    path_parts = parsed_url.path.strip("/").split("/")
    if parsed_url.scheme != "https" or parsed_url.netloc != "chatgpt.com":
        return False
    if len(path_parts) == 2 and path_parts[0] == "c":
        return True
    return (
        len(path_parts) == 4
        and path_parts[0] == "g"
        and path_parts[1].startswith("g-p-")
        and path_parts[2] == "c"
    )


def get_video_chat_url(chat_url: str) -> str:
    normalized_url = chat_url.strip().rstrip("/")
    if not is_chatgpt_conversation_url(normalized_url):
        raise RuntimeError(
            "Video does not have a valid ChatGPT conversation URL. "
            "No prompt was sent."
        )
    return normalized_url


def get_video_thumbnail_chat_url(chat_url: str) -> str:
    return get_video_chat_url(chat_url)


def ensure_expected_conversation_page(
    actual_url: str,
    conversation_url: str,
) -> None:
    actual = urlparse(actual_url)
    expected = urlparse(conversation_url)
    if (
        actual.scheme != expected.scheme
        or actual.netloc != expected.netloc
        or actual.path.rstrip("/") != expected.path.rstrip("/")
    ):
        raise RuntimeError(
            "ChatGPT did not stay on the video's conversation page. "
            "No prompt was sent."
        )


def launch_chatgpt_context(
    browser_type,
    profile_dir,
    wait_timeout: float = PROFILE_WAIT_TIMEOUT_SECONDS,
    retry_interval: float = PROFILE_RETRY_INTERVAL_SECONDS,
):
    deadline = time.monotonic() + wait_timeout

    while True:
        try:
            return browser_type.launch_persistent_context(
                str(profile_dir),
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 800},
            )
        except Exception as exc:
            error_message = str(exc)
            profile_is_busy = any(
                marker.lower() in error_message.lower()
                for marker in PROFILE_BUSY_ERROR_MARKERS
            )
            if not profile_is_busy:
                raise

            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise RuntimeError(
                    "ChatGPT browser profile is still busy after waiting "
                    f"{wait_timeout:g} seconds."
                ) from exc

            print(
                "ChatGPT browser profile is busy; waiting for the current "
                "video or thumbnail job to finish...",
                file=sys.stderr,
            )
            time.sleep(min(retry_interval, remaining_seconds))

def get_active_prompts():
    try:
        if PROMPTS_PATH.exists():
            data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
            # Allow env var override (for per-request version selection)
            import os
            version_override = os.environ.get("PROMPT_VERSION", "").strip()
            active_version = version_override if version_override else data.get("active_version", "default")
            if active_version in data["versions"]:
                return data["versions"][active_version]["prompts"]
            # Fallback to configured active version
            active_version = data.get("active_version", "default")
            return data["versions"][active_version]["prompts"]
    except Exception as e:
        print(f"Error loading prompts: {e}", file=sys.stderr)
    
    return DEFAULT_PROMPTS_DATA["versions"]["default"]["prompts"]


def build_metadata_generation_prompt(metadata_prompt: str) -> str:
    if not metadata_prompt.strip():
        raise RuntimeError("The selected prompt version has no metadata prompt.")
    return (
        f"{metadata_prompt.strip()}\n\n"
        "LƯU Ý QUAN TRỌNG: Hãy tạo lại đầy đủ toàn bộ phần metadata theo "
        "đúng yêu cầu trên, bao gồm TIÊU ĐỀ, URL SLUG, MÔ TẢ, HASHTAG, "
        "BÌNH LUẬN GHIM và QUIZ. Trả lời trực tiếp, không chào hỏi, không "
        "giải thích và không thêm nội dung ngoài metadata.\n\n"
        "BẮT BUỘC trình bày theo đúng mẫu nhãn sau:\n"
        "TIÊU ĐỀ: ...\n"
        "URL SLUG: ...\n"
        "MÔ TẢ VIDEO: ...\n"
        "HASHTAG: #... #... #...\n"
        "BÌNH LUẬN GHIM: ...\n"
        "CÂU HỎI: ...\n"
        "A. ...\nB. ...\nC. ...\nD. ...\n"
        "CÂU TRẢ LỜI ĐÚNG: ...\n"
        "GIẢI THÍCH: ..."
    )


def find_missing_metadata_sections(response_text: str) -> list[str]:
    metadata = response_text.strip()
    heading_prefix = r"(?im)^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*{1,2})?"
    required_patterns = {
        "TIÊU ĐỀ": heading_prefix + r"TIÊU ĐỀ",
        "URL SLUG": heading_prefix + r"(?:URL\s+SLUG|SLUG)",
        "MÔ TẢ": heading_prefix + r"MÔ TẢ",
        "HASHTAG": r"(?i)(?<!\w)#[a-z0-9_]+",
        "BÌNH LUẬN GHIM": heading_prefix + r"BÌNH LUẬN GHIM",
        "QUIZ": (
            heading_prefix
            + r"(?:CÂU HỎI(?:\s+(?:QUIZ|KHÁN GIẢ|TƯƠNG TÁC))?|QUIZ|THEO CÁC BẠN\b)"
        ),
    }
    return [
        label
        for label, pattern in required_patterns.items()
        if not re.search(pattern, metadata)
    ]


def validate_metadata_response(response_text: str) -> str:
    metadata = response_text.strip()
    missing_sections = find_missing_metadata_sections(metadata)
    if missing_sections:
        raise RuntimeError(
            "ChatGPT returned incomplete metadata (missing "
            + ", ".join(missing_sections)
            + "). The existing metadata was preserved."
        )
    return metadata


def build_metadata_retry_prompt(missing_sections: list[str]) -> str:
    return (
        "Phản hồi metadata vừa rồi chưa đầy đủ, còn thiếu: "
        + ", ".join(missing_sections)
        + ". Hãy tạo lại TOÀN BỘ metadata, không chỉ bổ sung phần thiếu. "
        "BẮT BUỘC xuất đủ các nhãn: TIÊU ĐỀ, URL SLUG, MÔ TẢ VIDEO, "
        "HASHTAG, BÌNH LUẬN GHIM, CÂU HỎI, bốn lựa chọn A/B/C/D, "
        "CÂU TRẢ LỜI ĐÚNG và GIẢI THÍCH. Trả lời trực tiếp, không chào hỏi "
        "và không thêm nội dung ngoài metadata."
    )


def request_complete_metadata(page, generation_prompt: str) -> str:
    response_text = send_prompt(page, generation_prompt).strip()
    missing_sections = find_missing_metadata_sections(response_text)
    if not missing_sections:
        return response_text

    retry_prompt = build_metadata_retry_prompt(missing_sections)
    retry_response = send_prompt(page, retry_prompt).strip()
    return validate_metadata_response(retry_response)


def clean_text(text: str) -> str:
    """Removes 'Edit' and common AI conversational fillers from the output."""
    lines = text.split('\n')
    cleaned = []
    skip_keywords = ["dưới đây là", "trân trọng gửi", "đây là", "chắc chắn rồi", "dạ vâng", "vâng,", "đã hoàn thành", "bạn chưa cung cấp", "nội dung hoàn chỉnh", "chào bạn"]
    for line in lines:
        lower_line = line.strip().lower()
        if lower_line == "edit":
            continue
        if any(lower_line.startswith(kw) for kw in skip_keywords):
            continue
        if lower_line.endswith(":") and len(line) < 100 and ("đây" in lower_line or "sau" in lower_line or "hoàn chỉnh" in lower_line):
            continue
        cleaned.append(line)
    
    result = '\n'.join(cleaned).strip()
    # Remove leading 'Edit' that might be left if it wasn't on its own line
    result = re.sub(r'^\s*Edit\s*\n*', '', result)
    return result

def send_prompt(page: Page, prompt_text: str) -> str:
    # Ensure textarea is ready and enabled
    prompt_textarea = page.locator('#prompt-textarea').first
    try:
        prompt_textarea.wait_for(state="visible", timeout=60000)
    except Exception:
        raise Exception("Could not find the prompt textarea after 60s. Are you logged in, or is a Cloudflare check/popup blocking it?")

    # Wait for any previous generation to finish (stop-button disappears)
    try:
        page.wait_for_function(
            """() => {
                return document.querySelector('[data-testid="stop-button"]') === null;
            }""",
            timeout=CHATGPT_RESPONSE_TIMEOUT_SECONDS * 1000
        )
    except Exception:
        raise Exception(
            "Previous ChatGPT generation did not finish after 20 minutes. "
            "No new prompt was sent."
        )

    # Mark existing messages so we can identify the new one
    page.evaluate("document.querySelectorAll('[data-message-author-role=\"assistant\"]').forEach(el => el.classList.add('my-old-msg'))")

    # Use real editor input events so ChatGPT updates its internal composer state.
    try:
        prompt_textarea.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(prompt_text)
    except Exception:
        # Fallback for unusually large prompts or transient keyboard failures.
        page.evaluate("""(text) => {
            const el = document.querySelector('#prompt-textarea');
            if (!el) return;
            el.focus();
            // For ProseMirror contenteditable divs
            const selection = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(el);
            selection.removeAllRanges();
            selection.addRange(range);
            document.execCommand('insertText', false, text);
            el.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                inputType: 'insertText',
                data: text
            }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""", prompt_text)
    time.sleep(0.5)

    # Now wait for the send button to appear and be enabled
    send_btn = page.locator('[data-testid="send-button"]').first
    send_button_ready_script = """() => {
        const btn = document.querySelector('[data-testid="send-button"]');
        return btn && !btn.disabled;
    }"""
    try:
        page.wait_for_function(send_button_ready_script, timeout=30000)
    except Exception as e:
        page.evaluate("""() => {
            const el = document.querySelector('#prompt-textarea');
            if (!el) return;
            el.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                inputType: 'insertText',
                data: null
            }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
        try:
            page.wait_for_function(send_button_ready_script, timeout=10000)
        except Exception:
            diagnostics = page.evaluate("""() => {
                const editor = document.querySelector('#prompt-textarea');
                const button = document.querySelector('[data-testid="send-button"]');
                return {
                    editorTextLength: editor?.innerText?.length ?? 0,
                    sendButtonFound: Boolean(button),
                    sendButtonDisabled: button?.disabled ?? null
                };
            }""")
            try:
                if diagnostics["editorTextLength"] <= 0:
                    raise Exception("The prompt draft was empty before recovery.")

                # ChatGPT occasionally leaves the composer unmounted after a long
                # insert. Reloading the same conversation restores its saved draft.
                page.reload(wait_until="domcontentloaded", timeout=60000)
                prompt_textarea = page.locator('#prompt-textarea').first
                prompt_textarea.wait_for(state="visible", timeout=60000)

                restored_draft = prompt_textarea.inner_text().strip()
                if not restored_draft:
                    prompt_textarea.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.insert_text(prompt_text)

                # Reload removes the marker classes, so mark the existing replies
                # again before sending to avoid returning an earlier response.
                page.evaluate("document.querySelectorAll('[data-message-author-role=\"assistant\"]').forEach(el => el.classList.add('my-old-msg'))")
                page.wait_for_function(send_button_ready_script, timeout=30000)
                send_btn = page.locator('[data-testid="send-button"]').first
            except Exception as recovery_error:
                recovery_diagnostics = page.evaluate("""() => {
                    const editor = document.querySelector('#prompt-textarea');
                    const button = document.querySelector('[data-testid="send-button"]');
                    return {
                        editorTextLength: editor?.innerText?.length ?? 0,
                        sendButtonFound: Boolean(button),
                        sendButtonDisabled: button?.disabled ?? null
                    };
                }""")
                raise Exception(
                    "Send button did not appear/enable after typing or one same-chat reload. "
                    f"Initial diagnostics: {diagnostics}. "
                    f"Recovery diagnostics: {recovery_diagnostics}. "
                    f"Initial error: {e}. Recovery error: {recovery_error}"
                ) from recovery_error

    send_btn.click()
    
    # 1. Wait for textarea to clear (confirms send was successful)
    try:
        page.wait_for_function(
            """() => {
                const ta = document.querySelector('#prompt-textarea');
                return ta && ta.textContent.trim() === '';
            }""",
            timeout=5000
        )
    except Exception:
        # Fallback 1: try pressing Enter
        prompt_textarea.press("Enter")
        time.sleep(1)
        try:
            page.wait_for_function(
                """() => {
                    const ta = document.querySelector('#prompt-textarea');
                    return ta && ta.textContent.trim() === '';
                }""",
                timeout=5000
            )
        except Exception:
            # Fallback 2: JS click
            try:
                page.evaluate('document.querySelector(\'[data-testid="send-button"]\').click()')
                time.sleep(1)
            except Exception as e:
                print(f"Warning: JS click failed: {e}", file=sys.stderr)

    # 2. Wait for generation to start (stop button appears)
    try:
        page.wait_for_function('() => { return document.querySelector(\'[data-testid="stop-button"]\') !== null; }', timeout=15000)
    except Exception:
        print("Warning: stop button did not appear. Generation might have finished instantly or failed.", file=sys.stderr)

    # 3. Wait for generation to finish (stop button disappears). Never move to
    # the next prompt while ChatGPT is still producing the current response.
    try:
        page.wait_for_function(
            """() => {
                return document.querySelector('[data-testid="stop-button"]') === null;
            }""",
            timeout=CHATGPT_RESPONSE_TIMEOUT_SECONDS * 1000,
        )
    except Exception as exc:
        raise Exception(
            "ChatGPT generation did not finish after 20 minutes. "
            "No next prompt was sent."
        ) from exc

    time.sleep(1) # Extra buffer for DOM to settle

    # Wait for the new message to appear (one without 'my-old-msg')
    try:
        page.wait_for_function("() => document.querySelector('[data-message-author-role=\"assistant\"]:not(.my-old-msg)') !== null", timeout=30000)
    except Exception:
        print("Error: No new assistant message was created. ChatGPT might have blocked the prompt or encountered an error.", file=sys.stderr)
        return ""

    # Extract the markdown content from the new assistant message
    assistant_messages = page.locator('[data-message-author-role="assistant"]:not(.my-old-msg)')
    last_msg = assistant_messages.last
    content_div = last_msg.locator('.markdown').first
    
    try:
        content_div.wait_for(state="visible", timeout=3000)
        res = content_div.inner_text()
    except Exception:
        res = last_msg.inner_text()
        
    return clean_text(res)


def run(transcript: str) -> str:
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception("Profile directory not found. Please run the auto-login tool first.")

    with sync_playwright() as p:
        context = launch_chatgpt_context(p.chromium, profile_dir)

        page = context.pages[0] if context.pages else context.new_page()
        project_url = get_chatgpt_project_url()
        page.goto(project_url, wait_until="domcontentloaded")
        ensure_expected_project_page(page.url, project_url)

        STRICT_NO_FILLER = "\n\nLƯU Ý QUAN TRỌNG: TRẢ LỜI TRỰC TIẾP VÀO NỘI DUNG. TUYỆT ĐỐI KHÔNG CHÀO HỎI, KHÔNG DẠ VÂNG, KHÔNG THÊM BẤT KỲ CÂU DẪN HAY GIẢI THÍCH NÀO (VD: 'Dưới đây là...', 'Trân trọng gửi bạn...'). CHỈ IN RA ĐÚNG NỘI DUNG CẦN VIẾT."

        prompts = get_active_prompts()

        # Step 2: Dàn ý
        print(">>> BƯỚC 2: TẠO DÀN Ý", file=sys.stderr)
        prompt2 = prompts.get("outline", "").replace("{transcript}", transcript) + STRICT_NO_FILLER
        
        outline = send_prompt(page, prompt2)
        
        # Capture chat URL right after first message (URL chứa session ID duy nhất)
        chat_url = page.url
        print(f"    -> Chat URL: {chat_url}", file=sys.stderr)
        
        # Lọc bỏ tất cả rác (vd chữ 'Edit' hoặc lời dạo đầu của AI) trước chữ [PHAN] đầu tiên
        if "[PHAN]" in outline:
            outline = outline[outline.index("[PHAN]"):]
            
        parts = [p.strip() for p in outline.split("[PHAN]") if p.strip()]
        if not parts:
            parts = [outline] # fallback
            
        print(f"    -> Đã chia thành {len(parts)} phần.", file=sys.stderr)

        # Step 3: Intro
        print(">>> BƯỚC 3: VIẾT INTRO", file=sys.stderr)
        prompt3 = prompts.get("intro", "") + STRICT_NO_FILLER
        intro = send_prompt(page, prompt3)

        body_parts_result = []
        for i, part in enumerate(parts):
            print(f">>> BƯỚC 4: VIẾT BODY PHẦN {i+1}/{len(parts)}", file=sys.stderr)
            prompt4 = prompts.get("body", "").replace("{part}", part) + STRICT_NO_FILLER
            res = send_prompt(page, prompt4)
            body_parts_result.append(res)
            
        body_combined = "\n\n".join(body_parts_result)

        # Step 5: Outro
        print(">>> BƯỚC 5: VIẾT OUTRO", file=sys.stderr)
        prompt5 = prompts.get("outro", "") + STRICT_NO_FILLER
        outro = send_prompt(page, prompt5)

        # Step 6: Metadata & Quiz
        print(">>> BƯỚC 6: TẠO METADATA & QUIZ", file=sys.stderr)
        prompt6 = prompts.get("metadata", "") + STRICT_NO_FILLER
        metadata = send_prompt(page, prompt6)

        # Step 7: Chapters
        print(">>> BƯỚC 7: TẠO CHAPTERS", file=sys.stderr)
        prompt7 = prompts.get("chapters", "") + STRICT_NO_FILLER
        chapters = send_prompt(page, prompt7)

        # Helper for image extraction
        def _download_image_local(chatgpt_url: str) -> str:
            """Download image via Playwright session and save locally."""
            try:
                THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
                filename = f"thumb_{uuid.uuid4().hex[:12]}.png"
                dest = THUMBNAILS_DIR / filename
                b64 = page.evaluate("""
                    async (url) => {
                        const res = await fetch(url, { credentials: 'include' });
                        const buf = await res.arrayBuffer();
                        const bytes = new Uint8Array(buf);
                        let binary = '';
                        for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
                        return btoa(binary);
                    }
                """, chatgpt_url)
                import base64
                dest.write_bytes(base64.b64decode(b64))
                local_path = f"/api/thumbnails/{filename}"
                print(f"    -> Đã download ảnh về local: {local_path}", file=sys.stderr)
                return local_path
            except Exception as e:
                print(f"    -> Lỗi download ảnh: {e}", file=sys.stderr)
                return chatgpt_url  # fallback to original URL

        # Step 8: Thumbnail Idea 1 (With Text)
        print(">>> BƯỚC 8: TẠO Ý TƯỞNG THUMBNAIL (CÓ CHỮ)", file=sys.stderr)
        prompt8 = build_thumbnail_generation_prompt(
            prompts.get("thumb_text", ""),
            "with_text",
        )
        thumb1, image1_url = send_thumbnail_prompt(
            page,
            prompt8,
            _download_image_local,
        )
        if not image1_url:
            print(">>> THUMBNAIL CÓ CHỮ LỖI, THỬ LẠI MỘT LẦN...", file=sys.stderr)
            thumb1, image1_url = send_thumbnail_prompt(
                page,
                THUMBNAIL_RETRY_PROMPT,
                _download_image_local,
            )

        if image1_url:
            thumb1 += f"\n\n[IMAGE_URL:{image1_url}]"

        # Step 9: Thumbnail Idea 2 (No Text)
        print(">>> BƯỚC 9: TẠO Ý TƯỞNG THUMBNAIL (KHÔNG CHỮ)", file=sys.stderr)
        prompt9 = build_thumbnail_generation_prompt(
            prompts.get("thumb_notext", ""),
            "without_text",
        )
        thumb2, image2_url = send_thumbnail_prompt(
            page,
            prompt9,
            _download_image_local,
        )
        if not image2_url:
            print(">>> THUMBNAIL KHÔNG CHỮ LỖI, THỬ LẠI MỘT LẦN...", file=sys.stderr)
            thumb2, image2_url = send_thumbnail_prompt(
                page,
                THUMBNAIL_RETRY_PROMPT,
                _download_image_local,
            )

        if image2_url:
            thumb2 += f"\n\n[IMAGE_URL:{image2_url}]"

        context.close()
        
        # Combine all
        final_script = f"### [INTRO]\n{intro}\n\n### [BODY]\n{body_combined}\n\n### [OUTRO]\n{outro}\n\n### [METADATA & QUIZ]\n{metadata}\n\n### [CHAPTERS]\n{chapters}\n\n### [THUMBNAIL CÓ CHỮ]\n{thumb1}\n\n### [THUMBNAIL KHÔNG CHỮ]\n{thumb2}"

        return {"script": final_script, "chat_url": chat_url}


def generate_chapters_only(
    script_text: str,
    chat_url: str = "",
    prompt_version: str = "",
) -> str:
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception("Profile directory not found. Please run the auto-login tool first.")

    with sync_playwright() as p:
        context = launch_chatgpt_context(p.chromium, profile_dir)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            is_original_chat = is_chatgpt_conversation_url(chat_url)
            target_url = chat_url if is_original_chat else get_chatgpt_project_url()
            page.goto(target_url, wait_until="domcontentloaded")
            if not is_original_chat:
                ensure_expected_project_page(page.url, target_url)
            time.sleep(2)

            import os
            original_prompt_version = os.environ.get("PROMPT_VERSION")
            if prompt_version:
                os.environ["PROMPT_VERSION"] = prompt_version
            try:
                prompts = get_active_prompts()
            finally:
                if original_prompt_version is not None:
                    os.environ["PROMPT_VERSION"] = original_prompt_version
                else:
                    os.environ.pop("PROMPT_VERSION", None)

            if not is_original_chat:
                send_prompt(
                    page,
                    "Đây là nội dung kịch bản video YouTube cần tạo chapter:\n\n"
                    f"{script_text[:12000]}",
                )

            chapter_prompt = prompts.get("chapters", "")
            chapter_prompt += (
                "\n\nLƯU Ý QUAN TRỌNG: Trả lời trực tiếp bằng danh sách chapter. "
                "Không chào hỏi, không giải thích, không thêm nội dung ngoài chapter."
            )
            chapters = send_prompt(page, chapter_prompt).strip()
            if not chapters:
                raise RuntimeError("ChatGPT did not return chapter content.")
            return chapters
        finally:
            context.close()


def generate_metadata_only(
    chat_url: str,
    prompt_version: str = "",
) -> str:
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception("Profile directory not found. Please run the auto-login tool first.")

    conversation_url = get_video_chat_url(chat_url)
    with sync_playwright() as p:
        context = launch_chatgpt_context(p.chromium, profile_dir)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(conversation_url, wait_until="domcontentloaded")
            ensure_expected_conversation_page(page.url, conversation_url)
            time.sleep(2)

            original_prompt_version = os.environ.get("PROMPT_VERSION")
            if prompt_version:
                os.environ["PROMPT_VERSION"] = prompt_version
            try:
                prompts = get_active_prompts()
            finally:
                if original_prompt_version is not None:
                    os.environ["PROMPT_VERSION"] = original_prompt_version
                else:
                    os.environ.pop("PROMPT_VERSION", None)

            generation_prompt = build_metadata_generation_prompt(
                prompts.get("metadata", "")
            )
            return request_complete_metadata(page, generation_prompt)
        finally:
            context.close()


def generate_thumbnails_only(
    script_text: str,
    chat_url: str = '',
    prompt_version: str = '',
    thumbnail_type: str | None = None,
) -> dict:
    """Run only thumbnail generation steps (8 & 9).
    If chat_url is provided, navigates to that session to keep context.
    Otherwise opens a new chat.
    Uses the specified prompt_version if provided.
    """
    if thumbnail_type is not None:
        return _generate_single_thumbnail(
            script_text,
            chat_url,
            prompt_version,
            thumbnail_type,
        )

    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception("Profile directory not found. Please run the auto-login tool first.")

    with sync_playwright() as p:
        context = launch_chatgpt_context(p.chromium, profile_dir)

        page = context.pages[0] if context.pages else context.new_page()
        
        # Navigate to the original chat session if URL provided, otherwise open new chat
        is_original_chat = is_chatgpt_conversation_url(chat_url)
        target_url = chat_url if is_original_chat else get_chatgpt_project_url()
        print(f"    -> Navigating to: {target_url}", file=sys.stderr)
        page.goto(target_url, wait_until="domcontentloaded")
        if not is_original_chat:
            ensure_expected_project_page(page.url, target_url)
        time.sleep(2)  # Let the page settle

        # Temporarily set PROMPT_VERSION env var so get_active_prompts() reads it
        import os
        original_env = os.environ.get("PROMPT_VERSION")
        if prompt_version:
            os.environ["PROMPT_VERSION"] = prompt_version
            
        try:
            prompts = get_active_prompts()
        finally:
            if original_env is not None:
                os.environ["PROMPT_VERSION"] = original_env
            elif "PROMPT_VERSION" in os.environ:
                del os.environ["PROMPT_VERSION"]

        def _download_image(page, chatgpt_url: str) -> str:
            """Download image via Playwright session (has ChatGPT cookies) and save locally."""
            try:
                THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
                filename = f"thumb_{uuid.uuid4().hex[:12]}.png"
                dest = THUMBNAILS_DIR / filename
                # Use page.evaluate to fetch the image as base64 using the authenticated session
                b64 = page.evaluate("""
                    async (url) => {
                        const res = await fetch(url, { credentials: 'include' });
                        const buf = await res.arrayBuffer();
                        const bytes = new Uint8Array(buf);
                        let binary = '';
                        for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
                        return btoa(binary);
                    }
                """, chatgpt_url)
                import base64
                dest.write_bytes(base64.b64decode(b64))
                local_path = f"/api/thumbnails/{filename}"
                print(f"    -> Đã download ảnh về local: {local_path}", file=sys.stderr)
                return local_path
            except Exception as e:
                print(f"    -> Lỗi download ảnh: {e}", file=sys.stderr)
                return ""

        print("    -> Dùng trực tiếp prompt thumbnail đã lưu.", file=sys.stderr)

        print(">>>> GEN THUMBNAIL (CÓ CHỮ)", file=sys.stderr)
        prompt8 = build_thumbnail_generation_prompt(
            prompts.get("thumb_text", ""),
            "with_text",
        )
        thumb1, image1_url = send_thumbnail_prompt(
            page,
            prompt8,
            lambda image_url: _download_image(page, image_url),
        )
        if not image1_url:
            thumb1, image1_url = send_thumbnail_prompt(
                page,
                THUMBNAIL_RETRY_PROMPT,
                lambda image_url: _download_image(page, image_url),
            )

        print(">>> GEN THUMBNAIL (KHÔNG CHỮ)", file=sys.stderr)
        prompt9 = build_thumbnail_generation_prompt(
            prompts.get("thumb_notext", ""),
            "without_text",
        )
        thumb2, image2_url = send_thumbnail_prompt(
            page,
            prompt9,
            lambda image_url: _download_image(page, image_url),
        )
        if not image2_url:
            thumb2, image2_url = send_thumbnail_prompt(
                page,
                THUMBNAIL_RETRY_PROMPT,
                lambda image_url: _download_image(page, image_url),
            )

        context.close()

        return {
            "thumb_text": thumb1,
            "thumb_notext": thumb2,
            "image1_url": image1_url,
            "image2_url": image2_url,
        }


def _generate_single_thumbnail(
    script_text: str,
    chat_url: str,
    prompt_version: str,
    thumbnail_type: str,
) -> dict:
    thumbnail_configs = {
        "with_text": {
            "prompt_key": "thumb_text",
            "text_result_key": "thumb_text",
            "image_result_key": "image1_url",
            "label": "CÓ CHỮ",
        },
        "without_text": {
            "prompt_key": "thumb_notext",
            "text_result_key": "thumb_notext",
            "image_result_key": "image2_url",
            "label": "KHÔNG CHỮ",
        },
    }
    config = thumbnail_configs.get(thumbnail_type)
    if config is None:
        raise ValueError(f"Unsupported thumbnail type: {thumbnail_type}")

    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception("Profile directory not found. Please run the auto-login tool first.")

    with sync_playwright() as p:
        context = launch_chatgpt_context(p.chromium, profile_dir)

        page = context.pages[0] if context.pages else context.new_page()
        target_url = get_video_thumbnail_chat_url(chat_url)
        print(f"    -> Navigating to: {target_url}", file=sys.stderr)
        page.goto(target_url, wait_until="domcontentloaded")
        ensure_expected_conversation_page(page.url, target_url)
        time.sleep(2)

        import os
        original_prompt_version = os.environ.get("PROMPT_VERSION")
        if prompt_version:
            os.environ["PROMPT_VERSION"] = prompt_version
        try:
            prompts = get_active_prompts()
        finally:
            if original_prompt_version is not None:
                os.environ["PROMPT_VERSION"] = original_prompt_version
            else:
                os.environ.pop("PROMPT_VERSION", None)

        def download_image(chatgpt_url: str) -> str:
            try:
                THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
                filename = f"thumb_{uuid.uuid4().hex[:12]}.png"
                destination = THUMBNAILS_DIR / filename
                image_base64 = page.evaluate(
                    """
                    async (url) => {
                        const response = await fetch(url, { credentials: 'include' });
                        if (!response.ok) throw new Error(`Image download failed: ${response.status}`);
                        const buffer = await response.arrayBuffer();
                        const bytes = new Uint8Array(buffer);
                        let binary = '';
                        for (let i = 0; i < bytes.byteLength; i++) {
                            binary += String.fromCharCode(bytes[i]);
                        }
                        return btoa(binary);
                    }
                    """,
                    chatgpt_url,
                )
                import base64
                destination.write_bytes(base64.b64decode(image_base64))
                return f"/api/thumbnails/{filename}"
            except Exception as exc:
                print(f"    -> Image download failed: {exc}", file=sys.stderr)
                return ""

        generation_prompt = build_thumbnail_generation_prompt(
            prompts.get(config["prompt_key"], ""),
            thumbnail_type,
        )

        print(f">>>> REGENERATE THUMBNAIL ({config['label']})", file=sys.stderr)
        response_text, image_url = send_thumbnail_prompt(
            page,
            generation_prompt,
            download_image,
        )
        retry_succeeded = False
        if not image_url:
            print(
                f">>>> THUMBNAIL {config['label']} LỖI, THỬ LẠI MỘT LẦN...",
                file=sys.stderr,
            )
            retry_response_text, image_url = send_thumbnail_prompt(
                page,
                THUMBNAIL_RETRY_PROMPT,
                download_image,
            )
            retry_succeeded = bool(image_url)

        context.close()

        result = {
            "thumb_text": None,
            "thumb_notext": None,
            "image1_url": "",
            "image2_url": "",
        }
        result[config["text_result_key"]] = (
            None if retry_succeeded else response_text
        )
        result[config["image_result_key"]] = image_url
        return result


if __name__ == "__main__":
    transcript = sys.stdin.buffer.read().decode("utf-8")
    try:
        result = run(transcript)
        script = result["script"] if isinstance(result, dict) else result
        chat_url = result.get("chat_url", "") if isinstance(result, dict) else ""
        # Print script then marker then chat_url for the service to parse
        sys.stdout.buffer.write(script.encode("utf-8"))
        if chat_url:
            sys.stdout.buffer.write(f"\n###CHAT_URL###{chat_url}".encode("utf-8"))
    except Exception as e:
        sys.stderr.write(str(e))
        sys.exit(1)
