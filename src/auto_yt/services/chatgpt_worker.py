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
PROFILE_BUSY_ERROR_MARKERS = (
    "Opening in existing browser session",
    "profile is already in use",
    "ProcessSingleton",
)
THUMBNAIL_SOURCE_CONTEXT_LIMIT = 4500


def extract_thumbnail_source_context(script_text: str) -> str:
    body_match = re.search(
        r'### \[BODY\]\s*(.*?)(?=\n### \[|\Z)',
        script_text,
        re.DOTALL | re.IGNORECASE,
    )
    source_context = body_match.group(1) if body_match else script_text
    transition_match = re.search(
        r'\n\s*(?:Tiếp theo|Câu chuyện tiếp theo|Sau đó là câu chuyện)\b',
        source_context,
        re.IGNORECASE,
    )
    if transition_match:
        source_context = source_context[:transition_match.start()]
    return source_context[:THUMBNAIL_SOURCE_CONTEXT_LIMIT].strip()


def build_thumbnail_generation_prompt(
    base_prompt: str,
    script_text: str,
    thumbnail_type: str,
) -> str:
    source_context = extract_thumbnail_source_context(script_text)
    generation_prompt = (
        f"{base_prompt}\n\n"
        "NGUỒN NỘI DUNG BẮT BUỘC CHO LƯỢT TẠO ẢNH NÀY:\n"
        f"{source_context}\n\n"
        "Chỉ dùng nguồn nội dung vừa được đính kèm ở trên. Nếu video có nhiều "
        "câu chuyện, chỉ tạo thumbnail cho câu chuyện đầu tiên. Không lấy chi "
        "tiết từ câu chuyện sau, ảnh cũ, cuộc trò chuyện khác hoặc bộ nhớ."
    )
    if thumbnail_type == "without_text":
        generation_prompt += thumbnail_without_text_constraint()
    return generation_prompt


def thumbnail_without_text_constraint() -> str:
    return (
        "\n\nRÀNG BUỘC TUYỆT ĐỐI: Ảnh cuối cùng KHÔNG ĐƯỢC CÓ BẤT KỲ "
        "headline, caption, chữ lớn, chữ trang trí, ký tự hoặc typography "
        "nào. Hãy kể chuyện hoàn toàn bằng nhân vật, biểu cảm, hành động, "
        "vật chứng và bối cảnh. ZERO TEXT, NO WORDS, NO LETTERS."
    )


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
            timeout=180000
        )
    except Exception:
        raise Exception("Previous generation is taking too long (stop button still present).")

    # Mark existing messages so we can identify the new one
    page.evaluate("document.querySelectorAll('[data-message-author-role=\"assistant\"]').forEach(el => el.classList.add('my-old-msg'))")

    # Fill text via evaluate to avoid Playwright fill() timeout on very long prompts
    try:
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
        }""", prompt_text)
    except Exception:
        # Fallback to fill() if evaluate fails
        prompt_textarea.fill(prompt_text)
    time.sleep(0.5)

    # Now wait for the send button to appear and be enabled
    send_btn = page.locator('[data-testid="send-button"]').first
    try:
        page.wait_for_function(
            """() => {
                const btn = document.querySelector('[data-testid="send-button"]');
                return btn && !btn.disabled;
            }""",
            timeout=10000
        )
    except Exception as e:
        raise Exception(f"Send button did not appear/enable after typing. Error: {e}")

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

    # 3. Wait for generation to finish (stop button disappears)
    try:
        start_time = time.time()
        while time.time() - start_time < 300:
            is_stopped = page.evaluate('() => { return document.querySelector(\'[data-testid="stop-button"]\') === null; }')
            if is_stopped:
                break
            time.sleep(1)
        else:
            print("Warning: Generation did not finish after 5 minutes. Might be stalled.", file=sys.stderr)
    except Exception as e:
        print(f"Warning checking generation finish: {e}", file=sys.stderr)

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

        def extract_latest_image():
            try:
                time.sleep(3)
                images = page.locator('img[src*="backend-api/estuary"]')
                if images.count() == 0:
                    images = page.locator('img[alt*="Generated image"]')
                if images.count() == 0:
                    images = page.locator('img[alt*="DALL"]')
                if images.count() == 0:
                    images = page.locator('img[src*="files/"]')
                if images.count() > 0:
                    chatgpt_url = images.nth(images.count() - 1).get_attribute("src")
                    print(f"    -> Found image: {chatgpt_url[:80]}...", file=sys.stderr)
                    return _download_image_local(chatgpt_url)
            except Exception as e:
                print(f"    -> Lỗi lấy ảnh DALL-E: {e}", file=sys.stderr)
            return ""

        # Step 8: Thumbnail Idea 1 (With Text)
        print(">>> BƯỚC 8: TẠO Ý TƯỞNG THUMBNAIL (CÓ CHỮ)", file=sys.stderr)
        prompt8 = prompts.get("thumb_text", "") + STRICT_NO_FILLER
        thumb1 = send_prompt(page, prompt8)
        image1_url = extract_latest_image()
        
        # Đồng bộ logic: Nếu ChatGPT chỉ trả text mà chưa vẽ ảnh, ta trích xuất nội dung để ép vẽ
        if not image1_url:
            import re
            draw_str = None
            prompt_match1 = re.search(r'Prompt hình ảnh:\s*(.*?)(?=\n\n|\Z)', thumb1, re.IGNORECASE | re.DOTALL)
            if prompt_match1:
                draw_str = prompt_match1.group(1).strip()
            else:
                json_match = re.search(r'```(?:json)?\n(.*?)```', thumb1, re.IGNORECASE | re.DOTALL)
                if json_match:
                    draw_str = json_match.group(1).strip()
                else:
                    json_brace_match = re.search(r'(\{.*\})', thumb1, re.DOTALL)
                    if json_brace_match:
                        draw_str = json_brace_match.group(1).strip()
                    elif len(thumb1) > 20 and '👍' not in thumb1:
                        draw_str = thumb1.strip()

            if draw_str:
                print(">>> ĐANG VẼ ẢNH THUMBNAIL 1...", file=sys.stderr)
                draw_prompt1 = f"Vui lòng vẽ chính xác hình ảnh (Tỷ lệ 16:9) bám sát tuyệt đối mô tả sau đây:\n\n{draw_str}\n\nLƯU Ý: CHỈ VẼ ẢNH, KHÔNG BÌNH LUẬN."
                send_prompt(page, draw_prompt1)
                image1_url = extract_latest_image()

        if image1_url:
            thumb1 += f"\n\n[IMAGE_URL:{image1_url}]"

        # Step 9: Thumbnail Idea 2 (No Text)
        print(">>> BƯỚC 9: TẠO Ý TƯỞNG THUMBNAIL (KHÔNG CHỮ)", file=sys.stderr)
        prompt9 = prompts.get("thumb_notext", "") + STRICT_NO_FILLER
        thumb2 = send_prompt(page, prompt9)
        image2_url = extract_latest_image()
        
        # Đồng bộ logic tương tự cho thumbnail 2
        if not image2_url:
            import re
            draw_str2 = None
            prompt_match2 = re.search(r'Prompt hình ảnh:\s*(.*?)(?=\n\n|\Z)', thumb2, re.IGNORECASE | re.DOTALL)
            if prompt_match2:
                draw_str2 = prompt_match2.group(1).strip()
            else:
                json_match2 = re.search(r'```(?:json)?\n(.*?)```', thumb2, re.IGNORECASE | re.DOTALL)
                if json_match2:
                    draw_str2 = json_match2.group(1).strip()
                else:
                    json_brace_match2 = re.search(r'(\{.*\})', thumb2, re.DOTALL)
                    if json_brace_match2:
                        draw_str2 = json_brace_match2.group(1).strip()
                    elif len(thumb2) > 20 and '👍' not in thumb2:
                        draw_str2 = thumb2.strip()

            if draw_str2:
                print(">>> ĐANG VẼ ẢNH THUMBNAIL 2...", file=sys.stderr)
                draw_prompt2 = f"Vui lòng vẽ chính xác hình ảnh (Tỷ lệ 16:9) bám sát tuyệt đối mô tả sau đây:\n\n{draw_str2}\n\nLƯU Ý: CHỈ VẼ ẢNH, KHÔNG BÌNH LUẬN."
                send_prompt(page, draw_prompt2)
                image2_url = extract_latest_image()

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

        STRICT_NO_FILLER = "\n\nLƯU Ý QUAN TRỌNG: TRẢ LỜI TRỰC TIẾP VÀO NỘI DUNG. TUYỆT ĐỐI KHÔNG CHÀO HỎI, KHÔNG DẠ VÂNG, KHÔNG THÊM BẤT KỲ CÂU DẪN HAY GIẢI THÍCH NÀO (VD: 'Dưới đây là...', 'Trân trọng gửi bạn...'). CHỈ IN RA ĐÚNG NỘI DUNG CẦN VIẾT."

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

        def extract_latest_image():
            """Find the latest DALL-E image in the chat, download it locally, return the local URL."""
            try:
                time.sleep(3)
                images = page.locator('img[src*="backend-api/estuary"]')
                if images.count() == 0:
                    images = page.locator('img[alt*="Generated image"]')
                if images.count() == 0:
                    images = page.locator('img[alt*="DALL"]')
                if images.count() == 0:
                    images = page.locator('img[src*="files/"]')
                if images.count() > 0:
                    chatgpt_url = images.nth(images.count() - 1).get_attribute("src")
                    print(f"    -> Found image URL: {chatgpt_url[:80]}...", file=sys.stderr)
                    # Download locally via Playwright (uses auth cookies)
                    local_url = _download_image(page, chatgpt_url)
                    return local_url if local_url else chatgpt_url
                else:
                    print(f"    -> Không tìm thấy ảnh nào với các selector đã thử.", file=sys.stderr)
            except Exception as e:
                print(f"    -> Lỗi lấy ảnh DALL-E: {e}", file=sys.stderr)
            return ""

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

        import re
        def extract_image_prompt(script: str, section: str) -> str:
            pattern = re.escape(section) + r'(.*?)(?=### \[|\Z)'
            match = re.search(pattern, script, re.DOTALL)
            if match:
                content = match.group(1)
                prompt_match = re.search(r'Prompt hình ảnh:\s*(.*?)(?=\n\n|\Z)', content, re.IGNORECASE | re.DOTALL)
                if prompt_match:
                    return prompt_match.group(1).strip()
            return ""

        extracted_thumb1 = extract_image_prompt(script_text, "### [THUMBNAIL CÓ CHỮ]")
        extracted_thumb2 = extract_image_prompt(script_text, "### [THUMBNAIL KHÔNG CHỮ]")
        print(f"    -> extracted_thumb1 length: {len(extracted_thumb1)}", file=sys.stderr)
        print(f"    -> extracted_thumb2 length: {len(extracted_thumb2)}", file=sys.stderr)
        msg_count = page.locator('[data-message-author-role="assistant"]').count()
        print(f"    -> Total assistant messages on page: {msg_count}", file=sys.stderr)

        # Nếu là chat mới (không có chat_url), và không trích xuất được prompt hình ảnh trực tiếp -> cần gửi context trước
        if not is_original_chat:
            if not extracted_thumb1 and not extracted_thumb2:
                print("    -> Chat mới, gửi context script trước...", file=sys.stderr)
                context_prompt = f"Đây là nội dung kịch bản video YouTube tôi cần tạo thumbnail:\n\n{script_text[:3000]}"
                send_prompt(page, context_prompt)
            else:
                print("    -> Chat mới, nhưng đã có sẵn Prompt hình ảnh -> Bỏ qua bước gửi context.", file=sys.stderr)
        else:
            print("    -> Dùng lại phiên chat gốc, bỏ qua bước gửi context.", file=sys.stderr)

        # Step 8: Thumbnail Có Chữ
        print(">>>> GEN THUMBNAIL (CÓ CHỮ)", file=sys.stderr)
        text1_regenerated = False
        thumb1 = None

        # Kiểm tra ảnh đã có sẵn trong chat trước khi gửi prompt mới
        images_before = page.locator('div[data-message-author-role="assistant"] img[src*="backend-api/estuary"]')
        existing_count = images_before.count()
        print(f"    -> Số ảnh đã có trong chat: {existing_count}", file=sys.stderr)

        if existing_count >= 2:
            # Đã có đủ 2 ảnh — lấy trực tiếp không cần vẽ lại
            print("    -> Đã có 2 ảnh sẵn trong chat, lấy trực tiếp...", file=sys.stderr)
            image1_url = images_before.nth(existing_count - 2).get_attribute("src")
            image2_url = images_before.nth(existing_count - 1).get_attribute("src")
            print(f"    -> image1_url: {image1_url}", file=sys.stderr)
            print(f"    -> image2_url: {image2_url}", file=sys.stderr)
        else:
            image2_url = ""
            if extracted_thumb1:
                print(f"    -> Using extracted prompt: {extracted_thumb1}", file=sys.stderr)
                prompt8 = f"Vui lòng vẽ chính xác hình ảnh (Tỷ lệ 16:9) bám sát tuyệt đối mô tả sau đây:\n\n{extracted_thumb1}\n\nLƯU Ý: CHỈ VẼ ẢNH, KHÔNG BÌNH LUẬN."
            else:
                prompt8 = prompts.get("thumb_text", "") + STRICT_NO_FILLER
                text1_regenerated = True

            count_before = page.locator('[data-message-author-role="assistant"]').count()
            print(f"    -> Assistant count before sending prompt8: {count_before}", file=sys.stderr)
            print(f"    -> Sending prompt8 (first 200 chars): {prompt8[:200]}", file=sys.stderr)
            thumb1 = send_prompt(page, prompt8)
            print(f"    -> thumb1 response (first 200 chars): {thumb1[:200]}", file=sys.stderr)
            image1_url = extract_latest_image()

            if not image1_url and not extracted_thumb1:
                import re
                draw_str = None
                prompt_match1 = re.search(r'Prompt hình ảnh:\s*(.*?)(?=\n\n|\Z)', thumb1, re.IGNORECASE | re.DOTALL)
                if prompt_match1:
                    draw_str = prompt_match1.group(1).strip()
                else:
                    json_match = re.search(r'```(?:json)?\n(.*?)```', thumb1, re.IGNORECASE | re.DOTALL)
                    if json_match:
                        draw_str = json_match.group(1).strip()
                    else:
                        json_brace_match = re.search(r'(\{.*\})', thumb1, re.DOTALL)
                        if json_brace_match:
                            draw_str = json_brace_match.group(1).strip()
                        elif len(thumb1) > 20 and '👍' not in thumb1:
                            draw_str = thumb1.strip()

                if draw_str:
                    print(">>> ĐANG VẼ ẢNH THUMBNAIL 1 (Fallback)...", file=sys.stderr)
                    draw_prompt1 = f"Vui lòng vẽ chính xác hình ảnh (Tỷ lệ 16:9) bám sát tuyệt đối mô tả sau đây:\n\n{draw_str}\n\nLƯU Ý: CHỈ VẼ ẢNH, KHÔNG BÌNH LUẬN."
                    send_prompt(page, draw_prompt1)
                    image1_url = extract_latest_image()


        # Step 9: Thumbnail Không Chữ (bỏ qua nếu đã lấy được ảnh từ chat cũ)
        if existing_count < 2:
            print(">>> GEN THUMBNAIL (KHÔNG CHỮ)", file=sys.stderr)
            text2_regenerated = False
            if extracted_thumb2:
                prompt9 = f"Vui lòng vẽ chính xác hình ảnh (Tỷ lệ 16:9) bám sát tuyệt đối mô tả sau đây:\n\n{extracted_thumb2}\n\nLƯU Ý: CHỈ VẼ ẢNH, KHÔNG BÌNH LUẬN."
            else:
                prompt9 = prompts.get("thumb_notext", "") + STRICT_NO_FILLER
                text2_regenerated = True

            thumb2 = send_prompt(page, prompt9)
            image2_url = extract_latest_image()

            if not image2_url and not extracted_thumb2:
                import re
                draw_str2 = None
                prompt_match2 = re.search(r'Prompt hình ảnh:\s*(.*?)(?=\n\n|\Z)', thumb2, re.IGNORECASE | re.DOTALL)
                if prompt_match2:
                    draw_str2 = prompt_match2.group(1).strip()
                else:
                    json_match2 = re.search(r'```(?:json)?\n(.*?)```', thumb2, re.IGNORECASE | re.DOTALL)
                    if json_match2:
                        draw_str2 = json_match2.group(1).strip()
                    else:
                        json_brace_match2 = re.search(r'(\{.*\})', thumb2, re.DOTALL)
                        if json_brace_match2:
                            draw_str2 = json_brace_match2.group(1).strip()
                        elif len(thumb2) > 20 and '👍' not in thumb2:
                            draw_str2 = thumb2.strip()

                if draw_str2:
                    print(">>> ĐANG VẼ ẢNH THUMBNAIL 2 (Fallback)...", file=sys.stderr)
                    draw_prompt2 = f"Vui lòng vẽ chính xác hình ảnh (Tỷ lệ 16:9) bám sát tuyệt đối mô tả sau đây:\n\n{draw_str2}\n\nLƯU Ý: CHỈ VẼ ẢNH, KHÔNG BÌNH LUẬN."
                    send_prompt(page, draw_prompt2)
                    image2_url = extract_latest_image()
        else:
            print("    -> Bỏ qua Step 9, đã lấy đủ ảnh từ chat.", file=sys.stderr)
            thumb2 = None
            text2_regenerated = False

        context.close()

        return {
            "thumb_text": thumb1 if text1_regenerated else None,
            "thumb_notext": thumb2 if text2_regenerated else None,
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
            "section": "### [THUMBNAIL CÓ CHỮ]",
            "prompt_key": "thumb_text",
            "text_result_key": "thumb_text",
            "image_result_key": "image1_url",
            "label": "CÓ CHỮ",
        },
        "without_text": {
            "section": "### [THUMBNAIL KHÔNG CHỮ]",
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
        target_url = get_chatgpt_project_url()
        print(f"    -> Navigating to: {target_url}", file=sys.stderr)
        page.goto(target_url, wait_until="domcontentloaded")
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

        def get_latest_conversation_turn() -> int:
            if page.url.rstrip("/") == target_url.rstrip("/"):
                return -1
            turn_ids = page.locator(
                '[data-testid^="conversation-turn-"]'
            ).evaluate_all(
                """
                elements => elements
                    .map(element => element.getAttribute('data-testid') || '')
                    .map(value => Number(value.replace('conversation-turn-', '')))
                    .filter(Number.isFinite)
                """
            )
            return max(turn_ids, default=-1)

        def extract_image_from_new_turn(previous_turn: int) -> str:
            deadline = time.time() + 30
            while time.time() < deadline:
                turns = page.locator(
                    '[data-testid^="conversation-turn-"]'
                )
                for index in range(turns.count() - 1, -1, -1):
                    turn = turns.nth(index)
                    test_id = turn.get_attribute("data-testid") or ""
                    try:
                        turn_number = int(test_id.rsplit("-", 1)[-1])
                    except ValueError:
                        continue
                    if turn_number <= previous_turn:
                        continue
                    images = turn.locator('img[src*="backend-api/estuary"]')
                    if images.count() > 0:
                        chatgpt_url = images.last.get_attribute("src")
                        if chatgpt_url:
                            return download_image(chatgpt_url) or chatgpt_url
                time.sleep(1)
            return ""

        def extract_section_prompt() -> str:
            pattern = re.escape(config["section"]) + r'(.*?)(?=### \[|\Z)'
            section_match = re.search(pattern, script_text, re.DOTALL)
            if not section_match:
                return ""
            prompt_match = re.search(
                r'Prompt hình ảnh:\s*(.*?)(?=\n\n|\Z)',
                section_match.group(1),
                re.IGNORECASE | re.DOTALL,
            )
            return prompt_match.group(1).strip() if prompt_match else ""

        def extract_draw_prompt(response_text: str) -> str:
            normalized_response = response_text.strip().lower()
            error_markers = (
                "something went wrong",
                "please try again",
                "đã xảy ra lỗi",
                "hãy thử lại",
            )
            if not normalized_response or any(
                marker in normalized_response for marker in error_markers
            ):
                return ""

            prompt_match = re.search(
                r'Prompt hình ảnh:\s*(.*?)(?=\n\n|\Z)',
                response_text,
                re.IGNORECASE | re.DOTALL,
            )
            if prompt_match:
                return prompt_match.group(1).strip()

            json_match = re.search(r'```(?:json)?\n(.*?)```', response_text, re.IGNORECASE | re.DOTALL)
            if json_match:
                return json_match.group(1).strip()

            json_object_match = re.search(r'(\{.*\})', response_text, re.DOTALL)
            if json_object_match:
                return json_object_match.group(1).strip()

            return response_text.strip() if len(response_text.strip()) > 20 and '👍' not in response_text else ""

        extracted_prompt = extract_section_prompt()

        if extracted_prompt:
            generation_prompt = (
                "Vui lòng vẽ chính xác hình ảnh (Tỷ lệ 16:9) bám sát tuyệt đối "
                f"mô tả sau đây:\n\n{extracted_prompt}\n\n"
                "LƯU Ý: CHỈ VẼ ẢNH, KHÔNG BÌNH LUẬN."
            )
            if thumbnail_type == "without_text":
                generation_prompt += thumbnail_without_text_constraint()
            text_regenerated = False
        else:
            generation_prompt = build_thumbnail_generation_prompt(
                prompts.get(config["prompt_key"], ""),
                script_text,
                thumbnail_type,
            )
            generation_prompt += (
                "\n\nLƯU Ý QUAN TRỌNG: TRẢ LỜI TRỰC TIẾP VÀO NỘI DUNG. "
                "TUYỆT ĐỐI KHÔNG CHÀO HỎI, KHÔNG DẠ VÂNG, KHÔNG THÊM CÂU DẪN. "
                "CHỈ IN RA ĐÚNG NỘI DUNG CẦN VIẾT."
            )
            text_regenerated = True

        print(f">>>> REGENERATE THUMBNAIL ({config['label']})", file=sys.stderr)
        previous_turn = get_latest_conversation_turn()
        response_text = send_prompt(page, generation_prompt)
        image_url = extract_image_from_new_turn(previous_turn)

        if not image_url and not extracted_prompt:
            draw_prompt = extract_draw_prompt(response_text)
            if draw_prompt:
                previous_turn = get_latest_conversation_turn()
                output_constraint = (
                    thumbnail_without_text_constraint()
                    if thumbnail_type == "without_text"
                    else ""
                )
                send_prompt(
                    page,
                    "Vui lòng vẽ chính xác hình ảnh (Tỷ lệ 16:9) bám sát tuyệt đối "
                    f"mô tả sau đây:\n\n{draw_prompt}\n\n"
                    "LƯU Ý: CHỈ VẼ ẢNH, KHÔNG BÌNH LUẬN."
                    f"{output_constraint}",
                )
                image_url = extract_image_from_new_turn(previous_turn)

        context.close()

        result = {
            "thumb_text": None,
            "thumb_notext": None,
            "image1_url": "",
            "image2_url": "",
        }
        result[config["text_result_key"]] = response_text if text_regenerated else None
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
