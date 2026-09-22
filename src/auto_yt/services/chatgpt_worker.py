import sys
import time
import asyncio
import base64
import json
import os
import re
import uuid
import hashlib
from collections.abc import Callable
from contextlib import closing
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright, Page
from auto_yt.paths import gpt_profile_dir, PROMPTS_PATH, THUMBNAILS_DIR
from auto_yt.default_prompts import DEFAULT_PROMPTS_DATA
from auto_yt.services.chatgpt_projects import (
    CHATGPT_PROJECT_URL_ENV,
    DEFAULT_CHATGPT_BOOTSTRAP_URL,
    DEFAULT_CHATGPT_PROJECT_URL,
    PROMPT_PIPELINE_ENV,
    get_project_url,
    normalize_prompt_pipeline,
    validate_prompt_pipeline,
)
from auto_yt.services.generation_checkpoint import (
    load_checkpoint,
    save_checkpoint,
)
from auto_yt.services.audio_review import looks_like_editorial_artifact
from auto_yt.services.network_security import (
    MAX_IMAGE_DOWNLOAD_BYTES,
    validate_chatgpt_image_url,
    validate_image_bytes,
)
from auto_yt.services import chatgpt_browser_service
from auto_yt.services.chatgpt_runtime import (
    ChatGPTAttentionRequiredError,
    encode_attention_error,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding='utf-8')

_HERE = __import__('pathlib').Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent.parent))

DEFAULT_GPT_PROFILE = "PROFILE_GPT_1"
CHATGPT_BROWSER_CONNECT_TIMEOUT_SECONDS = 15
CHATGPT_BROWSER_CONNECT_RETRY_SECONDS = 1
CHATGPT_RESPONSE_TIMEOUT_SECONDS = 20 * 60
CHAPTER_LATE_RESPONSE_GRACE_SECONDS = 5 * 60
ASSISTANT_RESPONSE_WAIT_SECONDS = 30
ASSISTANT_RESPONSE_POLL_SECONDS = 0.5
ASSISTANT_RESPONSE_STABLE_SECONDS = 5
ASSISTANT_RESPONSE_SETTLE_AFTER_BUSY_SECONDS = 1
ASSISTANT_RESPONSE_RELOAD_WAIT_SECONDS = 60
PENDING_RESPONSE_WAIT_SECONDS = 90
CHATGPT_COMPOSER_RECOVERY_ATTEMPTS = 3
CHATGPT_COMPOSER_WAIT_PER_ATTEMPT_MS = 20_000
CHATGPT_PAGE_RECOVERY_SETTLE_MS = 1_500
CHATGPT_NAVIGATION_TIMEOUT_MS = 60_000
CHATGPT_PROJECT_NAVIGATION_ATTEMPTS = 2
CHATGPT_PROMPT_SUBMISSION_TIMEOUT_MS = 30_000
EXTERNAL_APP_PERMISSION_CLICK_TIMEOUT_MS = 5_000
EXTERNAL_APP_PERMISSION_DIALOG_SELECTOR = '[role="dialog"], [role="alertdialog"]'
EXTERNAL_APP_PERMISSION_DIALOG_MARKER_GROUPS = (
    (
        "search outside this project",
        "chatgpt will use",
        "help answer your request",
    ),
    (
        "tìm kiếm bên ngoài dự án này",
        "chatgpt sẽ sử dụng",
        "giúp trả lời yêu cầu của bạn",
    ),
)
EXTERNAL_APP_PERMISSION_DENY_LABELS = (
    "deny",
    "don't allow",
    "do not allow",
    "từ chối",
    "không cho phép",
)
OUTLINE_PART_MAX_CHARS = 3000
OUTLINE_INTRO_OUTRO_KEYWORDS = (
    "m\u1edf b\xe0i",     # mở bài
    "m\u1edf \u0111\u1ea7u",    # mở đầu
    "intro",
    "k\u1ebft b\xe0i",    # kết bài
    "k\u1ebft lu\u1eadn",  # kết luận
    "outro",
    "mo bai",
    "mo dau",
    "ket bai",
    "ket luan",
)
NARRATIVE_ARTIFACT_MAX_WORDS = 14
THUMBNAIL_IMAGE_WAIT_TIMEOUT_SECONDS = 5 * 60
THUMBNAIL_TURN_WAIT_TIMEOUT_SECONDS = 30
MAX_THUMBNAIL_IMAGES_PER_RESPONSE = 2
NARRATIVE_ONLY_INSTRUCTION = (
    "\n\nYÊU CẦU ĐẦU RA CHO PHẦN NỘI DUNG: Chỉ viết văn xuôi liền mạch. "
    "Không chèn tiêu đề, nhãn chuyển đoạn, dàn ý, ghi chú biên tập hoặc "
    "chỉ dẫn về cách viết."
)
THUMBNAIL_IMAGE_SELECTOR = (
    'img[src*="backend-api/estuary"], '
    'img[alt*="Generated image"], '
    'img[alt*="DALL"], '
    'img[src*="files/"]'
)
THUMBNAIL_REPAIR_PROMPT = (
    "hãy chỉ ra điểm vi phạm prompt của tôi. sau đó sửa prompt  sao cho không vi phạm nữa."
)
THUMBNAIL_REGENERATE_PROMPT = (
    "Tạo ảnh theo prompt vừa được sửa ở ngay trên. Lưu ý: chỉ cần xuất ảnh của prompt mới sửa"
)
URL_LIKE_TOKEN_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s<>{}\[\]\"']+"
    r"|\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"[a-z]{2,}(?:/[^\s<>{}\[\]\"']*)?",
    flags=re.IGNORECASE,
)
JSON_STRING_LITERAL_PATTERN = re.compile(r'"(?:\\.|[^"\\])*"', flags=re.DOTALL)
CORRUPTED_UNICODE_PATTERN = re.compile(
    r"(?:(?<=\w)\?(?=\w)|\?{2,}(?=\w))",
    flags=re.UNICODE,
)
MIN_CORRUPTED_UNICODE_MARKERS = 3
THINKING_INDICATOR_PATTERN = re.compile(
    r"^(?:stopped\s+thinking|thought\s+for\s+\d+.*|thinking\.{0,3}|đã\s+dừng\s+suy\s+nghĩ|đang\s+suy\s+nghĩ\.{0,3})$",
    re.IGNORECASE,
)


def _is_pure_thinking_indicator(text: str) -> bool:
    cleaned = text.strip()
    return bool(not cleaned or THINKING_INDICATOR_PATTERN.match(cleaned))


def download_chatgpt_image_via_page(page: Page, chatgpt_url: str) -> str:
    """Persist one authenticated ChatGPT PNG after URL, redirect and size checks."""
    validate_chatgpt_image_url(chatgpt_url)
    payload = page.evaluate(
        """
        async ({ url, maxBytes }) => {
            const response = await fetch(url, { credentials: 'include' });
            if (!response.ok) throw new Error(`Image download failed: ${response.status}`);
            const declaredSize = Number(response.headers.get('content-length') || 0);
            if (declaredSize > maxBytes) throw new Error('Image exceeds size limit');
            const buffer = await response.arrayBuffer();
            if (buffer.byteLength > maxBytes) throw new Error('Image exceeds size limit');
            const bytes = new Uint8Array(buffer);
            let binary = '';
            const chunkSize = 0x8000;
            for (let offset = 0; offset < bytes.length; offset += chunkSize) {
                binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
            }
            return {
                base64: btoa(binary),
                finalUrl: response.url,
                contentType: response.headers.get('content-type') || ''
            };
        }
        """,
        {"url": chatgpt_url, "maxBytes": MAX_IMAGE_DOWNLOAD_BYTES},
    )
    validate_chatgpt_image_url(str(payload.get("finalUrl") or ""))
    content_type = str(payload.get("contentType") or "").split(";", 1)[0].casefold()
    if content_type != "image/png":
        raise ValueError("Generated thumbnail did not return PNG content.")
    image_bytes = base64.b64decode(payload.get("base64") or "", validate=True)
    validate_image_bytes(image_bytes)
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"thumb_{uuid.uuid4().hex[:12]}.png"
    destination = THUMBNAILS_DIR / filename
    destination.write_bytes(image_bytes)
    return f"/api/thumbnails/{filename}"
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


class ChatGPTGenerationTimeoutError(RuntimeError):
    def __init__(
        self,
        message: str,
        response_text: str = "",
        previous_assistant_turn: int = -1,
        previous_assistant_count: int = 0,
    ):
        super().__init__(message)
        self.response_text = response_text
        self.previous_assistant_turn = previous_assistant_turn
        self.previous_assistant_count = previous_assistant_count


def is_valid_chapter_response(response_text: str) -> bool:
    timestamp_lines = re.findall(
        r"(?m)^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s*(?:-|–|—)\s*\S+",
        response_text,
    )
    if len(timestamp_lines) < 3:
        return False
    return all(int(part) == 0 for part in timestamp_lines[0].split(":"))


def sanitize_chapter_response(response_text: str) -> str:
    chapter_lines = [
        line.strip()
        for line in clean_text(response_text).splitlines()
        if re.match(
            r"^\s*(?:(?:\d{1,2}:)?\d{1,2}:\d{2})\s*(?:-|–|—)\s*\S+",
            line,
        )
    ]
    if not chapter_lines:
        return clean_text(response_text)
    return "Nội dung chính trong video:\n\n" + "\n".join(chapter_lines)


def select_reusable_chapter_response(
    conversation_turns: list[tuple[str, str]],
) -> str:
    for user_index in range(len(conversation_turns) - 1, -1, -1):
        role, user_text = conversation_turns[user_index]
        if role != "user" or "chapter" not in user_text.lower():
            continue

        next_user_index = next(
            (
                index
                for index in range(user_index + 1, len(conversation_turns))
                if conversation_turns[index][0] == "user"
            ),
            len(conversation_turns),
        )
        for response_role, response_text in reversed(
            conversation_turns[user_index + 1:next_user_index]
        ):
            cleaned_response = clean_text(response_text)
            if (
                response_role == "assistant"
                and is_valid_chapter_response(cleaned_response)
            ):
                return sanitize_chapter_response(cleaned_response)
    return ""


def select_reusable_outline_response(
    conversation_turns: list[tuple[str, str]],
) -> str:
    for role, response_text in reversed(conversation_turns):
        cleaned_response = clean_text(response_text)
        if (
            role == "assistant"
            and "[PHAN]" in cleaned_response
            and split_outline_parts(cleaned_response)
        ):
            return cleaned_response
    return ""


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
        if not turn.is_visible():
            continue
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


def get_assistant_message_count(page: Page) -> int:
    try:
        return page.locator(
            '[data-message-author-role="assistant"]'
        ).count()
    except Exception:
        return 0


def get_user_message_count(page: Page) -> int:
    try:
        return page.locator(
            '[data-message-author-role="user"]'
        ).count()
    except Exception:
        return 0


def accept_external_app_permission_dialog(page: Page) -> bool:
    """Accept action/plugin consent dialogs (like vidIQ)."""
    # Look for both modal dialogs and inline action cards
    # Inline cards don't have role="dialog", so we search globally for consent-like buttons
    
    # We look for visible buttons that match our target labels
    target_labels = ["always allow", "luôn cho phép", "allow", "cho phép", "đồng ý", "confirm", "xác nhận"]
    
    buttons = page.locator("button")
    try:
        count = buttons.count()
    except Exception:
        return False
        
    for button_index in range(count):
        button = buttons.nth(button_index)
        if not button.is_visible():
            continue
            
        button_text = button.inner_text().strip()
        if not button_text:
            button_text = button.get_attribute("aria-label") or ""
            
        normalized_label = " ".join(button_text.casefold().split())
        
        # If it's a split button, the text might be something like "Allow Dropdown" or similar, 
        # but inner_text() usually just gives "Allow" if the SVG is ignored.
        # Let's do a loose match: if the normalized label EXACTLY matches one of our targets.
        if normalized_label in target_labels:
            # To be safe and avoid clicking random "Confirm" buttons elsewhere, 
            # we check if there's context of an action consent.
            # But in the generation wait loop, any "Allow" or "Confirm" button is highly likely to be the popup!
            try:
                button.click(timeout=EXTERNAL_APP_PERMISSION_CLICK_TIMEOUT_MS)
                print(
                    f">>> Accepted ChatGPT action dialog with button '{normalized_label}'; continuing to wait for the current response.",
                    file=sys.stderr,
                )
                return True
            except Exception as e:
                print(f"Failed to click button '{normalized_label}': {e}", file=sys.stderr)
                
    return False


def get_response_turn_baseline(
    page_url_before_send: str,
    page_url_after_send: str,
    previous_assistant_turn: int,
) -> int:
    before = urlparse(page_url_before_send)
    after = urlparse(page_url_after_send)
    if (
        before.scheme != after.scheme
        or before.netloc != after.netloc
        or before.path.rstrip("/") != after.path.rstrip("/")
    ):
        return -1
    return previous_assistant_turn


def split_outline_parts(
    outline: str,
    max_chars: int = OUTLINE_PART_MAX_CHARS,
) -> list[str]:
    raw_parts = [
        part.strip()
        for part in outline.split("[PHAN]")
        if part.strip()
    ]
    if not raw_parts and outline.strip():
        raw_parts = [outline.strip()]

    chunks = []
    for raw_part in raw_parts:
        units = [line.strip() for line in raw_part.splitlines() if line.strip()]
        if not units:
            continue

        current_units = []
        current_length = 0
        for unit in units:
            if len(unit) > max_chars:
                words = unit.split()
                word_chunk = []
                word_chunk_length = 0
                expanded_units = []
                for word in words:
                    added_length = len(word) + (1 if word_chunk else 0)
                    if word_chunk and word_chunk_length + added_length > max_chars:
                        expanded_units.append(" ".join(word_chunk))
                        word_chunk = []
                        word_chunk_length = 0
                    word_chunk.append(word)
                    word_chunk_length += len(word) + (1 if word_chunk_length else 0)
                if word_chunk:
                    expanded_units.append(" ".join(word_chunk))
            else:
                expanded_units = [unit]

            for expanded_unit in expanded_units:
                added_length = len(expanded_unit) + (1 if current_units else 0)
                if (
                    current_units
                    and current_length + added_length > max_chars
                ):
                    chunks.append("\n".join(current_units))
                    current_units = []
                    current_length = 0
                current_units.append(expanded_unit)
                current_length += len(expanded_unit) + (
                    1 if len(current_units) > 1 else 0
                )

        if current_units:
            chunks.append("\n".join(current_units))

    return chunks


def is_core_script_complete(transcript: str, state: dict) -> bool:
    body_parts = state.get("body_parts", [])
    expected_body_parts = state.get("expected_body_parts", 0)
    return bool(
        state.get("intro", "").strip()
        and state.get("outro", "").strip()
        and expected_body_parts > 0
        and len(body_parts) == expected_body_parts
        and all(part.strip() for part in body_parts)
    )


def wait_for_new_user_turn(page: Page, previous_user_turn: int) -> int:
    deadline = time.time() + THUMBNAIL_TURN_WAIT_TIMEOUT_SECONDS
    while time.time() < deadline:
        request_turn = get_latest_conversation_turn(page, "user")
        if request_turn > previous_user_turn:
            return request_turn
        time.sleep(0.25)
    raise RuntimeError("ChatGPT did not create a new thumbnail request turn.")


def get_thumbnail_image_identity(image_url: str) -> str:
    parsed_url = urlparse(image_url)
    if parsed_url.scheme and parsed_url.netloc:
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        query_params = parse_qs(parsed_url.query)
        for asset_key in ("id", "file_id", "asset_id"):
            asset_values = query_params.get(asset_key)
            if asset_values:
                return f"{base_url}?{asset_key}={asset_values[0]}"
        return base_url
    return image_url


def wait_for_thumbnail_images(
    page: Page,
    request_turn: int,
    download_image,
) -> list[str]:
    deadline = time.time() + THUMBNAIL_IMAGE_WAIT_TIMEOUT_SECONDS
    response_turn = None
    snapshot_error_logged = False
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
            try:
                image_snapshots = images.evaluate_all(
                    """
                    elements => elements.map(image => ({
                        src: image.getAttribute('src') || image.currentSrc || '',
                        ready: image.complete && image.naturalWidth > 0,
                    }))
                    """
                )
                generation_active = (
                    page.locator('[data-testid="stop-button"]').count() > 0
                )
                snapshot_error_logged = False
            except PlaywrightError as exc:
                if not snapshot_error_logged:
                    print(
                        "Thumbnail DOM changed while reading images; retrying: "
                        f"{exc}",
                        file=sys.stderr,
                    )
                    snapshot_error_logged = True
                time.sleep(1)
                continue

            image_urls = []
            image_identities = set()
            for image_snapshot in image_snapshots:
                image_url = image_snapshot.get("src", "")
                image_identity = get_thumbnail_image_identity(image_url)
                if (
                    image_url
                    and image_snapshot.get("ready")
                    and image_identity not in image_identities
                ):
                    image_urls.append(image_url)
                    image_identities.add(image_identity)

            if image_urls and not generation_active:
                downloaded_urls = []
                for image_url in image_urls[:MAX_THUMBNAIL_IMAGES_PER_RESPONSE]:
                    downloaded_url = download_image(image_url) or image_url
                    if downloaded_url and downloaded_url not in downloaded_urls:
                        downloaded_urls.append(downloaded_url)
                return downloaded_urls
        time.sleep(1)
    return []


def wait_for_thumbnail_image(page: Page, request_turn: int, download_image) -> str:
    image_urls = wait_for_thumbnail_images(page, request_turn, download_image)
    return image_urls[0] if image_urls else ""


def send_thumbnail_prompt(
    page: Page,
    prompt_text: str,
    download_image,
    reference_image_base64: str | None = None
) -> tuple[str, list[str]]:
    previous_user_turn = get_latest_conversation_turn(page, "user")
    response_text = send_prompt(
        page,
        prompt_text,
        allow_empty_response=True,
        reference_image_base64=reference_image_base64
    )
    request_turn = wait_for_new_user_turn(page, previous_user_turn)
    if is_thumbnail_generation_error_response(response_text):
        return response_text, []
    image_urls = wait_for_thumbnail_images(page, request_turn, download_image)
    return response_text, image_urls


def retry_thumbnail_generation(page: Page, download_image) -> tuple[str, list[str]]:
    send_prompt(page, THUMBNAIL_REPAIR_PROMPT)
    return send_thumbnail_prompt(
        page,
        THUMBNAIL_REGENERATE_PROMPT,
        download_image,
    )


def build_thumbnail_generation_prompt(
    base_prompt: str,
    thumbnail_type: str,
) -> str:
    return base_prompt


def append_thumbnail_image_markers(
    response_text: str,
    image_urls: list[str],
) -> str:
    markers = "\n\n".join(f"[IMAGE_URL:{url}]" for url in image_urls)
    return f"{response_text}\n\n{markers}".strip() if markers else response_text


def get_chatgpt_project_url(prompt_version: str = "") -> str:
    return get_project_url(prompt_version)


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


def is_expected_project_conversation_url(
    actual_url: str,
    project_url: str,
) -> bool:
    actual = urlparse(str(actual_url or "").strip())
    project = urlparse(str(project_url or "").strip())
    actual_parts = actual.path.strip("/").split("/")
    project_parts = project.path.strip("/").split("/")
    return (
        actual.scheme == project.scheme == "https"
        and actual.netloc == project.netloc == "chatgpt.com"
        and len(project_parts) == 3
        and project_parts[0] == "g"
        and project_parts[2] == "project"
        and len(actual_parts) == 4
        and actual_parts[0] == "g"
        and actual_parts[1] == project_parts[1]
        and actual_parts[2] == "c"
        and bool(actual_parts[3])
    )


def ensure_expected_project_conversation_page(
    actual_url: str,
    project_url: str,
) -> None:
    if not is_expected_project_conversation_url(actual_url, project_url):
        raise RuntimeError(
            "ChatGPT created or opened a conversation outside the configured "
            "Project. No automatic resend was attempted."
        )


def get_project_sidebar_slug(project_url: str) -> str:
    project_identifier = urlparse(project_url).path.strip("/").split("/")[1]
    match = re.match(r"^g-p-[0-9a-f]{32}-(.+)$", project_identifier, re.I)
    return match.group(1) if match else project_identifier.removeprefix("g-p-")


def open_configured_project_from_sidebar(page: Page, project_url: str) -> bool:
    """Open a Project through ChatGPT's healthy client-side sidebar.

    Project rows are interactive ``div`` elements rather than links. Directly
    loading ``/project`` can leave ChatGPT on its full-page "Try again" state,
    while the same route works when reached through the hydrated home shell.
    This helper only expands sidebar rows and clicks their non-destructive
    "Open project home" control; it never submits a prompt.
    """
    project_identifier = urlparse(project_url).path.strip("/").split("/")[1]
    project_slug = get_project_sidebar_slug(project_url)
    result = page.evaluate(
        """async ({ projectIdentifier, projectSlug }) => {
            const delay = (milliseconds) => new Promise(
                (resolve) => setTimeout(resolve, milliseconds)
            );
            const normalize = (value) => (value || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, ' ')
                .trim();
            const normalizedSlug = normalize(projectSlug.replace(/-/g, ' '));

            const getRows = () => [...document.querySelectorAll(
                'button[aria-label^="Open project options for "]'
            )].map((optionsButton) => {
                const controls = optionsButton.parentElement;
                const group = controls?.parentElement;
                const row = group?.querySelector(
                    '[data-sidebar-item][role="button"]'
                );
                const homeButton = controls?.querySelector(
                    'button[aria-label="Open project home"]'
                );
                const label = row?.innerText?.trim()
                    || optionsButton.getAttribute('aria-label')
                        ?.replace(/^Open project options for\s+/, '')
                    || '';
                return { group, row, homeButton, label };
            }).filter((item) => item.row && item.homeButton);

            const expandMoreSidebarItems = async () => {
                const buttons = [...document.querySelectorAll(
                    'button, [role="button"]'
                )].filter((element) => {
                    const text = normalize(
                        element.innerText || element.textContent
                    );
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0
                        && rect.height > 0
                        && (text === 'show more' || text === 'hien them');
                });
                for (const button of buttons.slice(0, 4)) {
                    button.click();
                    await delay(150);
                }
            };

            let rows = getRows();
            let selected = rows.find(
                (item) => normalize(item.label) === normalizedSlug
            );
            if (!selected) {
                await expandMoreSidebarItems();
                rows = getRows();
                selected = rows.find(
                    (item) => normalize(item.label) === normalizedSlug
                );
            }

            // When the URL slug and display name differ, identify the row by
            // expanding it and checking its project-scoped conversation links.
            if (!selected) {
                for (const item of rows) {
                    if (item.row.getAttribute('aria-expanded') !== 'true') {
                        item.row.click();
                        await delay(200);
                    }
                    const expectedConversationPrefix = `/g/${projectIdentifier}/c/`;
                    const ownsConfiguredProject = [...document.querySelectorAll(
                        'a[href]'
                    )].some((anchor) => {
                        try {
                            const url = new URL(anchor.href, location.origin);
                            return url.pathname.startsWith(
                                expectedConversationPrefix
                            );
                        } catch (_) {
                            return false;
                        }
                    });
                    if (ownsConfiguredProject) {
                        selected = item;
                        break;
                    }
                }
            }

            if (!selected) return false;
            if (selected.row.getAttribute('aria-expanded') !== 'true') {
                selected.row.click();
                await delay(200);
            }
            selected.homeButton.click();
            return true;
        }""",
        {
            "projectIdentifier": project_identifier,
            "projectSlug": project_slug,
        },
    )
    return bool(result)


def navigate_to_chatgpt_project(
    page: Page,
    project_url: str,
    bootstrap_url: str = DEFAULT_CHATGPT_BOOTSTRAP_URL,
) -> None:
    """Open a configured Project from the healthy global ChatGPT shell.

    The exact Project route is validated before the caller can send a prompt.
    There is deliberately no direct ``/project`` fallback: direct loads are the
    failure mode this bootstrap flow is designed to avoid.
    """
    expected_path = urlparse(project_url).path.rstrip("/")
    last_error: Exception | None = None

    for attempt in range(CHATGPT_PROJECT_NAVIGATION_ATTEMPTS):
        try:
            page.goto(
                bootstrap_url,
                wait_until="domcontentloaded",
                timeout=CHATGPT_NAVIGATION_TIMEOUT_MS,
            )
            wait_for_chatgpt_composer(page)

            if not open_configured_project_from_sidebar(page, project_url):
                raise RuntimeError(
                    "Configured ChatGPT Project was not found in the sidebar. "
                    "No prompt was sent."
                )
            page.wait_for_function(
                """(path) => location.pathname.replace(/\/+$/, '') === path""",
                arg=expected_path,
                timeout=CHATGPT_NAVIGATION_TIMEOUT_MS,
            )

            ensure_expected_project_page(page.url, project_url)
            wait_for_chatgpt_composer(page)
            return
        except ChatGPTAttentionRequiredError:
            # Authentication and anti-bot challenges require an explicit user
            # action. Do not turn them into a transient navigation failure or
            # retry the Project route, because that could open/focus another
            # window and enqueue the same prompt again.
            raise
        except Exception as exc:
            last_error = exc
            print(
                ">>> ChatGPT Project bootstrap attempt "
                f"{attempt + 1}/{CHATGPT_PROJECT_NAVIGATION_ATTEMPTS} failed: {exc}",
                file=sys.stderr,
            )

    raise RuntimeError(
        "ChatGPT Project did not become ready after bounded bootstrap "
        "recovery. No prompt was sent."
    ) from last_error


def get_chatgpt_load_state(page: Page) -> dict:
    """Return enough DOM state to distinguish a transient full-page failure."""
    try:
        return page.evaluate(
            """() => {
                const isVisible = (element) => {
                    if (!element) return false;
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                };
                const normalize = (value) => (value || '')
                    .replace(/\s+/g, ' ')
                    .trim()
                    .toLowerCase();
                const retryLabels = ['try again', 'retry', 'thử lại'];
                const loginLabels = ['log in', 'sign in', 'đăng nhập'];
                const challengeMarkers = [
                    'verify you are human', 'checking your browser',
                    'security check', 'cloudflare', 'captcha', 'just a moment',
                    'xác minh bạn là con người', 'đang kiểm tra trình duyệt'
                ];
                const editor = document.querySelector('#prompt-textarea');
                const turns = document.querySelectorAll(
                    '[data-testid^="conversation-turn-"], '
                    + '[data-message-author-role]'
                );
                const retryButton = [...document.querySelectorAll(
                    'button, [role="button"]'
                )].find((element) => {
                    const label = normalize(
                        element.innerText
                        || element.textContent
                        || element.getAttribute('aria-label')
                    );
                    return isVisible(element)
                        && retryLabels.some((candidate) => label === candidate);
                });
                const editorPresent = Boolean(editor && isVisible(editor));
                const visibleControls = [...document.querySelectorAll(
                    'button, a, [role="button"]'
                )].filter(isVisible);
                const loginRequired = !editorPresent && turns.length === 0
                    && visibleControls.some((element) => {
                        const label = normalize(
                            element.innerText
                            || element.textContent
                            || element.getAttribute('aria-label')
                        );
                        return loginLabels.some((candidate) => label === candidate);
                    });
                const pageText = normalize(
                    `${document.title || ''} ${document.body?.innerText || ''}`
                );
                return {
                    editor_present: editorPresent,
                    conversation_turn_count: turns.length,
                    login_required: loginRequired,
                    challenge_present: !editorPresent && turns.length === 0
                        && challengeMarkers.some((marker) => pageText.includes(marker)),
                    full_page_retry: Boolean(
                        !editorPresent && turns.length === 0 && retryButton
                    ),
                    body_preview: normalize(document.body?.innerText).slice(0, 240),
                };
            }"""
        )
    except Exception as exc:
        return {
            "editor_present": False,
            "conversation_turn_count": 0,
            "full_page_retry": False,
            "login_required": False,
            "challenge_present": False,
            "body_preview": "",
            "inspection_error": str(exc),
        }


def raise_if_chatgpt_attention_required(state: dict) -> None:
    """Stop safely when continuing requires an interactive browser."""
    if state.get("challenge_present"):
        raise ChatGPTAttentionRequiredError(
            "ChatGPT đang yêu cầu CAPTCHA/Cloudflare. Mở Auto Login hoặc "
            "Open Profile để xác minh, sau đó tiếp tục job trong Trung tâm Job."
        )
    if state.get("login_required"):
        raise ChatGPTAttentionRequiredError(
            "Phiên đăng nhập ChatGPT đã hết hạn. Mở Auto Login hoặc Open "
            "Profile để đăng nhập, sau đó tiếp tục job trong Trung tâm Job."
        )


def check_chatgpt_page_attention(page: Page) -> None:
    """Inspect redirects before strict Project/conversation URL validation."""
    raise_if_chatgpt_attention_required(get_chatgpt_load_state(page))


def click_chatgpt_full_page_retry(page: Page) -> bool:
    """Click only the retry control from a full-page load failure."""
    try:
        return bool(
            page.evaluate(
                """() => {
                    const isVisible = (element) => {
                        if (!element) return false;
                        const style = window.getComputedStyle(element);
                        const rect = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const normalize = (value) => (value || '')
                        .replace(/\s+/g, ' ')
                        .trim()
                        .toLowerCase();
                    const retryLabels = ['try again', 'retry', 'thử lại'];
                    const editor = document.querySelector('#prompt-textarea');
                    const turns = document.querySelectorAll(
                        '[data-testid^="conversation-turn-"], '
                        + '[data-message-author-role]'
                    );
                    if (editor || turns.length > 0) return false;
                    const retryButton = [...document.querySelectorAll(
                        'button, [role="button"]'
                    )].find((element) => {
                        const label = normalize(
                            element.innerText
                            || element.textContent
                            || element.getAttribute('aria-label')
                        );
                        return isVisible(element)
                            && retryLabels.some((candidate) => label === candidate);
                    });
                    if (!retryButton) return false;
                    retryButton.click();
                    return true;
                }"""
            )
        )
    except Exception:
        return False


def wait_for_chatgpt_composer(
    page: Page,
    attempts: int = CHATGPT_COMPOSER_RECOVERY_ATTEMPTS,
) -> object:
    """Wait for the composer and recover transient page-load failures safely.

    Recovery occurs before any prompt text is inserted. It therefore cannot
    duplicate a prompt, and reload always stays in the current project or
    conversation instead of opening a new chat.
    """
    prompt_textarea = page.locator('#prompt-textarea').first
    retry_clicked = False
    last_state: dict = {}
    last_error: Exception | None = None

    for attempt in range(max(1, attempts)):
        try:
            prompt_textarea.wait_for(
                state="visible",
                timeout=CHATGPT_COMPOSER_WAIT_PER_ATTEMPT_MS,
            )
            return prompt_textarea
        except Exception as exc:
            last_error = exc
            last_state = get_chatgpt_load_state(page)
            raise_if_chatgpt_attention_required(last_state)
            if attempt >= max(1, attempts) - 1:
                break

            recovered = False
            if last_state.get("full_page_retry") and not retry_clicked:
                retry_clicked = True
                recovered = click_chatgpt_full_page_retry(page)
                if recovered:
                    print(
                        ">>> ChatGPT page failed to load; clicked the full-page "
                        "Try again control before sending any prompt.",
                        file=sys.stderr,
                    )

            if not recovered:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                    print(
                        ">>> ChatGPT composer was unavailable; reloaded the same "
                        "page before sending any prompt.",
                        file=sys.stderr,
                    )
                except Exception as reload_error:
                    last_state["reload_error"] = str(reload_error)

            try:
                page.wait_for_timeout(CHATGPT_PAGE_RECOVERY_SETTLE_MS)
            except Exception:
                time.sleep(CHATGPT_PAGE_RECOVERY_SETTLE_MS / 1000)

    preview = str(last_state.get("body_preview", "")).strip()
    detail = f" Page preview: {preview!r}." if preview else ""
    raise RuntimeError(
        "ChatGPT page did not become ready after bounded same-page recovery. "
        "No prompt was sent. Check the ChatGPT login or service status."
        f"{detail} Last error: {last_error}"
    ) from last_error


class SharedBrowserContextLease:
    """Proxy a shared persistent context without letting a worker close it."""

    def __init__(self, context, browser):
        self._context = context
        # Keep the CDP Browser wrapper alive until the Playwright client exits.
        self._browser = browser

    def __getattr__(self, name):
        return getattr(self._context, name)

    def close(self) -> None:
        # The Browser Service is the only owner allowed to close this context.
        # Exiting sync_playwright() disconnects this client automatically.
        return None


def launch_chatgpt_context(
    browser_type,
    profile_dir,
    wait_timeout: float = CHATGPT_BROWSER_CONNECT_TIMEOUT_SECONDS,
    retry_interval: float = CHATGPT_BROWSER_CONNECT_RETRY_SECONDS,
):
    """Attach to the long-lived Browser Service without launching a window."""
    deadline = time.monotonic() + wait_timeout
    last_error: Exception | None = None

    while True:
        endpoint = chatgpt_browser_service.get_browser_service_endpoint()
        if endpoint:
            try:
                browser = browser_type.connect_over_cdp(
                    endpoint,
                    timeout=min(3_000, max(250, int(wait_timeout * 1000))),
                )
                if not browser.contexts:
                    raise RuntimeError(
                        "ChatGPT Browser Service has no persistent context."
                    )
                return SharedBrowserContextLease(browser.contexts[0], browser)
            except Exception as exc:
                last_error = exc

        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            detail = f" Chi tiết: {last_error}" if last_error else ""
            raise ChatGPTAttentionRequiredError(
                "Trình duyệt ChatGPT nền chưa kết nối. Mở Settings và bấm "
                "'Khởi động trình duyệt nền', sau đó tiếp tục job trong "
                f"Trung tâm Job.{detail}"
            ) from last_error
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


def get_active_pipeline() -> dict[str, bool]:
    pipeline_json = os.environ.get(PROMPT_PIPELINE_ENV, "").strip()
    if pipeline_json:
        try:
            return validate_prompt_pipeline(json.loads(pipeline_json))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Invalid prompt pipeline snapshot.") from exc

    try:
        if PROMPTS_PATH.exists():
            data = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
            version_override = os.environ.get("PROMPT_VERSION", "").strip()
            active_version = version_override or data.get("active_version", "default")
            version = data.get("versions", {}).get(active_version)
            if not isinstance(version, dict):
                version = data.get("versions", {}).get(
                    data.get("active_version", "default"),
                    {},
                )
            return normalize_prompt_pipeline(version.get("pipeline"))
    except (OSError, json.JSONDecodeError):
        pass

    return normalize_prompt_pipeline(None)


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
    skip_keywords = ["dưới đây là", "trân trọng gửi", "chắc chắn rồi", "dạ vâng", "vâng,", "đã hoàn thành", "bạn chưa cung cấp", "nội dung hoàn chỉnh", "chào bạn"]
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


def _is_short_narrative_artifact(block: str) -> tuple[bool, bool]:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines or len(lines) > 3:
        return False, False

    explicit_editorial_note = False
    for line in lines:
        has_markdown_heading = bool(re.match(r"^#{1,6}\s+", line))
        normalized_line = re.sub(
            r"^(?:#{1,6}\s*|[-*•]\s+|\d+[.)]\s+)",
            "",
            line,
        ).strip()
        word_count = len(re.findall(r"\w+", normalized_line, re.UNICODE))
        if (
            not normalized_line
            or word_count > NARRATIVE_ARTIFACT_MAX_WORDS
            or re.search(r'[.!?…;]["”’\])]*$', normalized_line)
        ):
            return False, False

        lowered_line = normalized_line.lower()
        explicit_editorial_note = explicit_editorial_note or (
            has_markdown_heading
            or looks_like_editorial_artifact(lowered_line)
            or bool(re.match(
                r"^(?:ghi chú|ý chính|trọng tâm)\b",
                lowered_line,
            ))
        )

    return True, explicit_editorial_note


def dedup_consecutive_paragraphs(text: str, min_chars: int = 60) -> str:
    """Remove consecutive duplicate paragraphs within a text block."""
    if not isinstance(text, str) or not text:
        return text
    blocks = [b.strip() for b in re.split(r"\n[ \t]*\n+", text) if b.strip()]
    deduped = []
    for block in blocks:
        normalized = re.sub(r"\s+", " ", block).strip()
        if deduped and len(normalized) >= min_chars:
            prev_normalized = re.sub(r"\s+", " ", deduped[-1]).strip()
            if normalized == prev_normalized:
                continue
        deduped.append(block)
    return "\n\n".join(deduped)


def sanitize_narrative_response(response_text: str) -> str:
    """Remove isolated editorial notes without rewriting narrative prose."""
    cleaned_text = clean_text(response_text)
    blocks = [
        block.strip()
        for block in re.split(r"\n[ \t]*\n+", cleaned_text)
        if block.strip()
    ]
    if not blocks:
        return ""

    block_checks = [_is_short_narrative_artifact(block) for block in blocks]
    has_substantive_narrative = any(
        not is_short_artifact
        for is_short_artifact, _ in block_checks
    )
    kept_blocks = []
    for block, (is_short_artifact, is_explicit_note) in zip(
        blocks,
        block_checks,
    ):
        if is_short_artifact and (
            is_explicit_note
            or (has_substantive_narrative and len(blocks) > 1)
        ):
            continue
        kept_blocks.append(block)

    # Never turn a non-empty response into an empty section. The prompt guard
    # remains the first line of defence, while this filter only removes notes
    # when genuine narrative text is left behind.
    if not kept_blocks:
        return dedup_consecutive_paragraphs(cleaned_text)
    return dedup_consecutive_paragraphs("\n\n".join(kept_blocks).strip())


def sanitize_generated_script(script_text: str) -> str:
    """Sanitize narrative sections while preserving all other sections."""
    if not isinstance(script_text, str) or not script_text:
        return script_text

    section_pattern = re.compile(
        r"(### \[(?:INTRO|BODY|OUTRO)\]\r?\n)(.*?)(?=\r?\n### \[|\Z)",
        flags=re.DOTALL,
    )
    
    seen_paragraphs = set()

    def replace_section(match: re.Match) -> str:
        section_header = match.group(1)
        cleaned_content = sanitize_narrative_response(match.group(2))
        
        deduped_blocks = []
        import re as local_re
        for block in local_re.split(r"\n[ \t]*\n+", cleaned_content):
            block_stripped = block.strip()
            if not block_stripped:
                continue
                
            normalized = local_re.sub(r"\s+", " ", block_stripped)
            if len(normalized) >= 100 and len(local_re.findall(r"[^\W_]+(?:['’][^\W_]+)?", normalized, local_re.UNICODE)) >= 10:
                if normalized in seen_paragraphs:
                    continue
                seen_paragraphs.add(normalized)
            
            deduped_blocks.append(block_stripped)
            
        final_content = "\n\n".join(deduped_blocks)
        separator = "\n" if final_content else ""
        return f"{section_header}{final_content}{separator}"

    return section_pattern.sub(replace_section, script_text)

def validate_prompt_text(prompt_text: str) -> None:
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise ValueError("The ChatGPT prompt must contain text.")
    # Query strings legitimately contain a question mark between alphanumeric
    # characters (for example ``youtube.com/channel/...?...``). Remove URL-like
    # tokens before looking for question marks that replaced damaged Unicode.
    text_without_urls = URL_LIKE_TOKEN_PATTERN.sub("", prompt_text)
    # JSON string values can contain arbitrary viewer text. A question mark
    # between words is valid punctuation there and is not evidence that the
    # application's own prompt text was damaged during decoding.
    trusted_prompt_text = JSON_STRING_LITERAL_PATTERN.sub('""', text_without_urls)
    corrupted_markers = CORRUPTED_UNICODE_PATTERN.findall(trusted_prompt_text)
    corrupted_marker_count = sum(marker.count("?") for marker in corrupted_markers)
    if (
        "\ufffd" in prompt_text
        or corrupted_marker_count >= MIN_CORRUPTED_UNICODE_MARKERS
    ):
        raise ValueError(
            "The ChatGPT prompt contains corrupted Unicode text. "
            "No prompt was sent."
        )


def prompt_text_matches(expected_text: str, editor_text: str) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()

    return normalize(expected_text) == normalize(editor_text)


def history_prompt_text_matches(expected_text: str, rendered_text: str) -> bool:
    def normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()

    expected = normalize(expected_text)
    actual = normalize(rendered_text)

    if expected == actual:
        return True

    if len(actual) >= 100 and expected.startswith(actual):
        return True

    if len(actual) >= 100 and len(expected) >= 100:
        if expected[:100] == actual[:100]:
            return True

    return False



def replace_prompt_text_with_javascript(page: Page, prompt_text: str) -> None:
    page.evaluate("""(text) => {
        const el = document.querySelector('#prompt-textarea');
        if (!el) return;
        el.focus();
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


def ensure_prompt_editor_integrity(
    page: Page,
    prompt_textarea,
    prompt_text: str,
) -> None:
    editor_text = prompt_textarea.inner_text()
    if not isinstance(editor_text, str) or prompt_text_matches(
        prompt_text,
        editor_text,
    ):
        return

    replace_prompt_text_with_javascript(page, prompt_text)
    time.sleep(0.2)
    editor_text = prompt_textarea.inner_text()
    if not isinstance(editor_text, str) or not prompt_text_matches(
        prompt_text,
        editor_text,
    ):
        raise RuntimeError(
            "The ChatGPT editor changed the prompt text during input. "
            "No prompt was sent."
        )


def _read_assistant_message(message) -> str:
    markdown_nodes = message.locator('.markdown')
    markdown_parts = []
    markdown_count = markdown_nodes.count()
    if not isinstance(markdown_count, int):
        markdown_count = 1 if markdown_nodes.first.count() > 0 else 0
    for index in range(markdown_count):
        try:
            markdown_node = (
                markdown_nodes.first
                if markdown_count == 1
                else markdown_nodes.nth(index)
            )
            markdown_text = clean_text(markdown_node.inner_text())
        except Exception:
            continue
        if (
            markdown_text
            and not _is_pure_thinking_indicator(markdown_text)
            and markdown_text not in markdown_parts
        ):
            markdown_parts.append(markdown_text)
    if markdown_parts:
        return "\n\n".join(markdown_parts)
    fallback = clean_text(message.inner_text())
    if _is_pure_thinking_indicator(fallback):
        return ""
    return fallback


def get_assistant_response_after_latest_user(
    page: Page,
    expected_user_text: str = "",
) -> str:
    """Read the reply following the latest user message without count baselines."""
    try:
        messages = page.locator('[data-message-author-role]')
        latest_user_index = -1
        for index in range(messages.count()):
            role = messages.nth(index).get_attribute("data-message-author-role")
            if role == "user":
                latest_user_index = index
        if latest_user_index < 0:
            return ""

        latest_user = messages.nth(latest_user_index)
        if expected_user_text and not history_prompt_text_matches(
            expected_user_text,
            clean_text(latest_user.inner_text()),
        ):
            return ""

        response_text = ""
        for index in range(latest_user_index + 1, messages.count()):
            message = messages.nth(index)
            if message.get_attribute("data-message-author-role") != "assistant":
                continue
            candidate = _read_assistant_message(message)
            if candidate:
                response_text = candidate
        return response_text
    except Exception:
        return ""


def get_new_assistant_response(
    page: Page,
    previous_assistant_turn: int = -1,
    previous_assistant_count: int | None = None,
) -> str:
    try:
        new_assistant_turns = [
            turn_number
            for turn_number, role in get_visible_conversation_turns(page)
            if role == "assistant" and turn_number > previous_assistant_turn
        ]
        if new_assistant_turns:
            turn = page.locator(
                f'[data-testid="conversation-turn-{max(new_assistant_turns)}"]'
            )
            assistant_nodes = turn.locator(
                '[data-message-author-role="assistant"]'
            )
            if assistant_nodes.count() > 0:
                response_text = _read_assistant_message(assistant_nodes.last)
                if response_text:
                    return response_text

        # Compatibility fallback for ChatGPT DOM variants without numbered
        # turns. The count baseline survives React DOM re-renders, unlike a
        # temporary CSS marker attached to old message nodes.
        assistant_messages = page.locator(
            '[data-message-author-role="assistant"]'
        )
        assistant_count = assistant_messages.count()
        if (
            assistant_count == 0
            or (
                previous_assistant_count is not None
                and assistant_count <= previous_assistant_count
            )
        ):
            return ""
        return _read_assistant_message(assistant_messages.last)
    except Exception:
        return ""


def is_chatgpt_generation_active(page: Page) -> bool:
    try:
        return bool(page.evaluate(
            """() => {
                if (document.querySelector('[data-testid="stop-button"]')) {
                    return true;
                }
                return [...document.querySelectorAll('button')].some((button) => {
                    const label = [
                        button.getAttribute('aria-label') || '',
                        button.getAttribute('title') || ''
                    ].join(' ').toLowerCase();
                    return label.includes('stop streaming')
                        || label.includes('stop generating')
                        || label.includes('dừng tạo')
                        || label.includes('dừng phản hồi');
                });
            }"""
        ))
    except Exception:
        return False


def _check_for_chatgpt_errors(text: str) -> None:
    if not text:
        return
    errors = [
        "A network error occurred. Please check your connection",
        "There was an error generating a response",
        "Something went wrong. If this issue persists",
        "The server had an error while processing your request",
        "Conversation not found",
    ]
    for err in errors:
        if err.lower() in text.lower():
            raise Exception(f"ChatGPT ERROR detected: {err}")


def wait_for_assistant_response(
    page: Page,
    previous_assistant_turn: int,
    allow_empty_response: bool = False,
    timeout: int = CHATGPT_RESPONSE_TIMEOUT_SECONDS,
    previous_assistant_count: int | None = None,
    previous_user_turn: int | None = None,
    previous_user_count: int | None = None,
    submitted_prompt_text: str = "",
) -> str:
    """Wait for a new response without relying only on the stop button."""
    started_at = time.monotonic()
    deadline = started_at + timeout
    empty_response_deadline = started_at + ASSISTANT_RESPONSE_WAIT_SECONDS
    last_response = ""
    last_change_at = started_at
    saw_busy_state = False

    while True:
        accept_external_app_permission_dialog(page)
        busy = is_chatgpt_generation_active(page)
        saw_busy_state = saw_busy_state or busy
        response_text = get_new_assistant_response(
            page,
            previous_assistant_turn,
            previous_assistant_count,
        )
        new_user_observed = saw_busy_state
        if not new_user_observed and previous_user_turn is not None:
            new_user_observed = (
                get_latest_conversation_turn(page, "user") > previous_user_turn
            )
        if not new_user_observed and previous_user_count is not None:
            new_user_observed = get_user_message_count(page) > previous_user_count
        if not response_text and new_user_observed:
            response_text = get_assistant_response_after_latest_user(
                page,
                expected_user_text=submitted_prompt_text,
            )
        now = time.monotonic()

        if response_text != last_response:
            last_response = response_text
            last_change_at = now

        required_stability = (
            ASSISTANT_RESPONSE_SETTLE_AFTER_BUSY_SECONDS
            if saw_busy_state
            else ASSISTANT_RESPONSE_STABLE_SECONDS
        )
        if (
            last_response
            and not busy
            and now - last_change_at >= required_stability
        ):
            _check_for_chatgpt_errors(last_response)
            return last_response

        if (
            allow_empty_response
            and not busy
            and (
                saw_busy_state
                or now >= empty_response_deadline
            )
        ):
            _check_for_chatgpt_errors(last_response)
            return last_response

        if now >= deadline:
            if busy:
                raise ChatGPTGenerationTimeoutError(
                    "ChatGPT generation did not finish after 20 minutes. "
                    "No next prompt was sent.",
                    last_response,
                    previous_assistant_turn,
                    previous_assistant_count or 0,
                )
            if submitted_prompt_text:
                recovered_response = recover_assistant_response_after_reload(
                    page,
                    submitted_prompt_text,
                )
                if recovered_response:
                    _check_for_chatgpt_errors(recovered_response)
                    return recovered_response
            raise RuntimeError(
                "ChatGPT returned no readable text for this step. "
                "The same conversation was reloaded once and no next prompt "
                "was sent."
            )

        time.sleep(ASSISTANT_RESPONSE_POLL_SECONDS)


def wait_for_existing_assistant_response(
    page: Page,
    expected_user_text: str,
    timeout: int,
) -> str:
    started_at = time.monotonic()
    deadline = started_at + timeout
    last_response = ""
    last_change_at = started_at
    saw_busy_state = False

    while True:
        accept_external_app_permission_dialog(page)
        busy = is_chatgpt_generation_active(page)
        saw_busy_state = saw_busy_state or busy
        response_text = get_assistant_response_after_latest_user(
            page,
            expected_user_text=expected_user_text,
        )
        now = time.monotonic()
        if response_text != last_response:
            last_response = response_text
            last_change_at = now
        required_stability = (
            ASSISTANT_RESPONSE_SETTLE_AFTER_BUSY_SECONDS
            if saw_busy_state
            else ASSISTANT_RESPONSE_STABLE_SECONDS
        )
        if (
            last_response
            and not busy
            and now - last_change_at >= required_stability
        ):
            _check_for_chatgpt_errors(last_response)
            return last_response
        if now >= deadline:
            return ""
        time.sleep(ASSISTANT_RESPONSE_POLL_SECONDS)


def recover_assistant_response_after_reload(
    page: Page,
    expected_user_text: str,
) -> str:
    """Reload the same conversation once and read only the existing reply."""
    expected_url = page.url
    if not is_chatgpt_conversation_url(expected_url):
        return ""
    try:
        page.reload(
            wait_until="domcontentloaded",
            timeout=CHATGPT_NAVIGATION_TIMEOUT_MS,
        )
        check_chatgpt_page_attention(page)
        ensure_expected_conversation_page(page.url, expected_url)
        page.locator('#prompt-textarea').first.wait_for(
            state="visible",
            timeout=CHATGPT_COMPOSER_WAIT_PER_ATTEMPT_MS,
        )
        wait_for_conversation_history(page, composer_ready=True)
        return wait_for_existing_assistant_response(
            page,
            expected_user_text,
            ASSISTANT_RESPONSE_RELOAD_WAIT_SECONDS,
        )
    except ChatGPTAttentionRequiredError:
        raise
    except Exception as exc:
        print(
            ">>> Could not recover the submitted ChatGPT response after one "
            f"same-conversation reload: {exc}",
            file=sys.stderr,
        )
        return ""


def wait_for_valid_chapter_response(
    page: Page,
    previous_assistant_turn: int,
    initial_response: str = "",
    timeout: int = CHAPTER_LATE_RESPONSE_GRACE_SECONDS,
    previous_assistant_count: int | None = None,
    expected_user_text: str = "",
) -> str:
    deadline = time.time() + timeout
    response_text = initial_response
    while True:
        if is_valid_chapter_response(response_text):
            return sanitize_chapter_response(response_text)
        if time.time() >= deadline:
            return ""
        time.sleep(1)
        response_text = get_new_assistant_response(
            page,
            previous_assistant_turn,
            previous_assistant_count,
        )
        if not response_text and expected_user_text:
            response_text = get_assistant_response_after_latest_user(
                page,
                expected_user_text=expected_user_text,
            )


def get_reusable_chapter_response(page: Page) -> str:
    conversation_turns = page.evaluate(
        """() => [...document.querySelectorAll('[data-message-author-role]')]
            .map((element) => [
                element.getAttribute('data-message-author-role') || '',
                element.innerText || ''
            ])"""
    )
    return select_reusable_chapter_response(
        [(role, text) for role, text in conversation_turns]
    )


def get_reusable_outline_response(page: Page) -> str:
    conversation_turns = page.evaluate(
        """() => [...document.querySelectorAll('[data-message-author-role]')]
            .map((element) => [
                element.getAttribute('data-message-author-role') || '',
                element.innerText || ''
            ])"""
    )
    return select_reusable_outline_response(
        [(role, text) for role, text in conversation_turns]
    )


def wait_for_conversation_history(
    page: Page,
    composer_ready: bool = False,
) -> None:
    if not composer_ready:
        wait_for_chatgpt_composer(page)
    page.wait_for_function(
        """() => {
            const turns = [...document.querySelectorAll(
                '[data-testid^="conversation-turn-"]'
            )];
            const roles = turns.map((turn) =>
                turn.getAttribute('data-turn')
                || turn.querySelector('[data-message-author-role]')
                    ?.getAttribute('data-message-author-role')
                || ''
            );
            if (roles.includes('user') && roles.includes('assistant')) {
                return true;
            }
            const fallbackRoles = [...document.querySelectorAll(
                '[data-message-author-role]'
            )].map((message) =>
                message.getAttribute('data-message-author-role') || ''
            );
            return fallbackRoles.includes('user')
                && fallbackRoles.includes('assistant');
        }""",
        timeout=60000,
    )

def send_prompt(
    page: Page,
    prompt_text: str,
    allow_empty_response: bool = False,
    reference_image_base64: str | None = None,
    on_prompt_submitted: Callable[[], None] | None = None,
) -> str:
    validate_prompt_text(prompt_text)

    # Recover a transient ChatGPT full-page load failure before touching the
    # composer. This is shared by every prompt-driven workflow.
    prompt_textarea = wait_for_chatgpt_composer(page)

    if is_chatgpt_conversation_url(page.url):
        wait_for_conversation_history(page)

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

    page_url_before_send = page.url
    previous_assistant_turn = get_latest_conversation_turn(page, "assistant")
    previous_assistant_count = get_assistant_message_count(page)
    previous_user_turn = get_latest_conversation_turn(page, "user")
    previous_user_count = get_user_message_count(page)

    # Mark existing messages so we can identify the new one
    page.evaluate("document.querySelectorAll('[data-message-author-role=\"assistant\"]').forEach(el => el.classList.add('my-old-msg'))")

    # Use real editor input events so ChatGPT updates its internal composer state.
    try:
        prompt_textarea.click()
        page.keyboard.press("Control+A")
        page.keyboard.insert_text(prompt_text)
    except Exception:
        # Fallback for unusually large prompts or transient keyboard failures.
        replace_prompt_text_with_javascript(page, prompt_text)
    time.sleep(0.5)
    ensure_prompt_editor_integrity(page, prompt_textarea, prompt_text)


    # Attach images if provided
    if reference_image_base64:
        js = """
        (dataStr) => {
            let base64Array = [];
            if (dataStr.trim().startsWith('[')) {
                try {
                    base64Array = JSON.parse(dataStr);
                } catch(e) {
                    base64Array = [dataStr];
                }
            } else {
                base64Array = [dataStr];
            }

            const dt = new DataTransfer();

            base64Array.forEach((base64Data, idx) => {
                const byteString = atob(base64Data);
                const ab = new ArrayBuffer(byteString.length);
                const ia = new Uint8Array(ab);
                for (let i = 0; i < byteString.length; i++) {
                    ia[i] = byteString.charCodeAt(i);
                }
                const file = new File([ab], `reference_${idx}.png`, { type: 'image/png' });
                dt.items.add(file);
            });

            const textarea = document.querySelector('#prompt-textarea');
            textarea.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
        }
        """
        page.evaluate(js, reference_image_base64)
        page.wait_for_timeout(2000)

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

    # Confirm the single send attempt through multiple independent UI signals.
    # Never press Enter or click again after an ambiguous delivery because the
    # first request may already have reached ChatGPT.
    try:
        page.wait_for_function(
            """(baseline) => {
                const editor = document.querySelector('#prompt-textarea');
                const userTurns = document.querySelectorAll(
                    '[data-message-author-role="user"]'
                ).length;
                const generationStarted = Boolean(
                    document.querySelector('[data-testid="stop-button"]')
                );
                const editorCleared = Boolean(
                    editor && (editor.innerText || editor.textContent || '').trim() === ''
                );
                return editorCleared
                    || generationStarted
                    || userTurns > baseline.previousUserCount
                    || location.href !== baseline.previousUrl;
            }""",
            arg={
                "previousUrl": page_url_before_send,
                "previousUserCount": previous_user_count,
            },
            timeout=CHATGPT_PROMPT_SUBMISSION_TIMEOUT_MS,
        )
    except Exception as exc:
        raise RuntimeError(
            "Prompt submission could not be confirmed after one send attempt. "
            "No automatic resend was attempted to avoid duplicate ChatGPT "
            "messages."
        ) from exc

    if on_prompt_submitted is not None:
        on_prompt_submitted()

    response_turn_baseline = get_response_turn_baseline(
        page_url_before_send,
        page.url,
        previous_assistant_turn,
    )
    return wait_for_assistant_response(
        page,
        response_turn_baseline,
        previous_assistant_count=previous_assistant_count,
        previous_user_turn=previous_user_turn,
        previous_user_count=previous_user_count,
        submitted_prompt_text=prompt_text,
        allow_empty_response=allow_empty_response,
    )


def get_prompt_fingerprint(prompt_text: str) -> str:
    normalized_prompt = re.sub(r"\s+", " ", prompt_text).strip()
    return hashlib.sha256(normalized_prompt.encode("utf-8")).hexdigest()


def _pending_prompt_matches(state: dict, step: str, prompt_text: str) -> bool:
    pending_prompt = state.get("pending_prompt")
    return bool(
        isinstance(pending_prompt, dict)
        and pending_prompt.get("step") == step
        and pending_prompt.get("fingerprint") == get_prompt_fingerprint(prompt_text)
    )


def is_prompt_in_conversation(page: Page, expected_user_text: str) -> bool:
    try:
        messages = page.locator('[data-message-author-role]')
        latest_user_index = -1
        for index in range(messages.count()):
            role = messages.nth(index).get_attribute("data-message-author-role")
            if role == "user":
                latest_user_index = index
        if latest_user_index < 0:
            return False

        latest_user = messages.nth(latest_user_index)
        return history_prompt_text_matches(
            expected_user_text,
            clean_text(latest_user.inner_text()),
        )
    except Exception:
        return False



def recover_pending_prompt_response(
    page: Page, 
    prompt_text: str,
    on_prompt_submitted = None,
) -> str:
    response_text = wait_for_existing_assistant_response(
        page,
        prompt_text,
        PENDING_RESPONSE_WAIT_SECONDS,
    )
    if not response_text:
        response_text = recover_assistant_response_after_reload(page, prompt_text)
    if response_text:
        return response_text
        
    if not is_prompt_in_conversation(page, prompt_text):
        return send_prompt(page, prompt_text, on_prompt_submitted=on_prompt_submitted)
        
    raise RuntimeError(
        "A previously submitted ChatGPT prompt still has no readable response. "
        "The existing conversation was inspected without resending the prompt."
    )


def send_or_recover_generation_prompt(
    page: Page,
    state: dict,
    step: str,
    prompt_text: str,
) -> str:
    pending_prompt = state.get("pending_prompt")
    def mark_submitted() -> None:
        state["pending_prompt"]["status"] = "submitted"
        if is_chatgpt_conversation_url(page.url):
            state["chat_url"] = page.url
        persist_generation_state(state)

    if pending_prompt:
        if not _pending_prompt_matches(state, step, prompt_text):
            raise RuntimeError(
                "The checkpoint contains a different unresolved ChatGPT prompt. "
                "No new prompt was sent."
            )
        return recover_pending_prompt_response(page, prompt_text, on_prompt_submitted=mark_submitted)

    def mark_prompt_submitted() -> None:
        state["pending_prompt"] = {
            "step": step,
            "fingerprint": get_prompt_fingerprint(prompt_text),
        }
        if is_chatgpt_conversation_url(page.url):
            state["chat_url"] = page.url
        persist_generation_state(state)

    return send_prompt(
        page,
        prompt_text,
        on_prompt_submitted=mark_prompt_submitted,
    )


def clear_pending_generation_prompt(
    state: dict,
    step: str,
    prompt_text: str,
) -> None:
    if _pending_prompt_matches(state, step, prompt_text):
        state.pop("pending_prompt", None)


def build_video_script(state: dict) -> str:
    intro = dedup_consecutive_paragraphs(sanitize_narrative_response(state.get("intro", "")))
    body = "\n\n".join(
        sanitized_part
        for part in state.get("body_parts", [])
        if (sanitized_part := dedup_consecutive_paragraphs(sanitize_narrative_response(part)))
    )
    outro = dedup_consecutive_paragraphs(sanitize_narrative_response(state.get("outro", "")))
    raw_script = (
        f"### [INTRO]\n{intro}\n\n"
        f"### [BODY]\n{body}\n\n"
        f"### [OUTRO]\n{outro}\n\n"
        f"### [METADATA & QUIZ]\n{state.get('metadata', '')}\n\n"
        f"### [CHAPTERS]\n{state.get('chapters', '')}\n\n"
        f"### [THUMBNAIL CÓ CHỮ]\n{state.get('thumb_text', '')}\n\n"
        f"### [THUMBNAIL KHÔNG CHỮ]\n{state.get('thumb_notext', '')}"
    )
    return sanitize_generated_script(raw_script)


def _checkpoint_video_id() -> int | None:
    raw_video_id = os.environ.get("VIDEO_ID", "").strip()
    if not raw_video_id:
        return None
    try:
        return int(raw_video_id)
    except ValueError:
        return None


def persist_generation_state(state: dict) -> None:
    """Persist both resumable worker state and the latest UI-visible draft."""
    video_id = _checkpoint_video_id()
    if video_id is None:
        return

    save_checkpoint(video_id, state)
    try:
        from auto_yt.services import database as db

        db.update_video_generation(
            video_id,
            build_video_script(state),
            state.get("chat_url", ""),
        )
    except Exception as exc:
        # The JSON checkpoint remains the recovery source even when SQLite is
        # temporarily unavailable (for example during an abrupt shutdown).
        print(
            f"Warning: could not update draft checkpoint in database: {exc}",
            file=sys.stderr,
        )


def _run_complete(transcript: str, state: dict) -> dict:
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception("Profile directory not found. Please run the auto-login tool first.")

    with sync_playwright() as p, closing(
        launch_chatgpt_context(p.chromium, profile_dir)
    ) as context:
        page = context.pages[0] if context.pages else context.new_page()
        project_url = get_chatgpt_project_url()
        resume_url = state.get("chat_url", "")
        is_resuming = is_chatgpt_conversation_url(resume_url)
        if is_resuming:
            page.goto(
                resume_url,
                wait_until="domcontentloaded",
                timeout=CHATGPT_NAVIGATION_TIMEOUT_MS,
            )
            check_chatgpt_page_attention(page)
            ensure_expected_conversation_page(page.url, resume_url)
            wait_for_conversation_history(page)
            print(
                f">>> TIẾP TỤC PHIÊN CHAT CŨ: {resume_url}",
                file=sys.stderr,
            )
        else:
            navigate_to_chatgpt_project(page, project_url)

        STRICT_NO_FILLER = "\n\nLƯU Ý QUAN TRỌNG: TRẢ LỜI TRỰC TIẾP VÀO NỘI DUNG. TUYỆT ĐỐI KHÔNG CHÀO HỎI, KHÔNG DẠ VÂNG, KHÔNG THÊM BẤT KỲ CÂU DẪN HAY GIẢI THÍCH NÀO (VD: 'Dưới đây là...', 'Trân trọng gửi bạn...'). CHỈ IN RA ĐÚNG NỘI DUNG CẦN VIẾT."

        prompts = get_active_prompts()
        pipeline = normalize_prompt_pipeline(state.get("pipeline"))
        state["pipeline"] = pipeline

        parts = state.get("outline_parts", [])
        if not parts:
            # Step 2: Dàn ý
            print(">>> BƯỚC 2: TẠO DÀN Ý", file=sys.stderr)
            prompt2 = prompts.get("outline", "").replace("{transcript}", transcript) + STRICT_NO_FILLER
            state["current_step"] = "outline"
            outline = get_reusable_outline_response(page) if is_resuming else ""
            if outline:
                print(">>> TÁI SỬ DỤNG DÀN Ý ĐÃ HOÀN THÀNH TRONG CHAT CŨ", file=sys.stderr)
            else:
                try:
                    outline = send_or_recover_generation_prompt(
                        page,
                        state,
                        "outline",
                        prompt2,
                    )
                finally:
                    if is_chatgpt_conversation_url(page.url):
                        ensure_expected_project_conversation_page(
                            page.url,
                            project_url,
                        )
                        state["chat_url"] = page.url
                        persist_generation_state(state)

            if is_chatgpt_conversation_url(page.url):
                ensure_expected_project_conversation_page(
                    page.url,
                    project_url,
                )
                state["chat_url"] = page.url
            if not state.get("chat_url"):
                raise RuntimeError(
                    "ChatGPT did not create a conversation inside the configured "
                    "Project after the first prompt. No automatic resend was "
                    "attempted."
                )
            print(f"    -> Chat URL: {state['chat_url']}", file=sys.stderr)

            if "[PHAN]" in outline:
                outline = outline[outline.index("[PHAN]"):]

            parts = split_outline_parts(outline)
            if not parts:
                clear_pending_generation_prompt(state, "outline", prompt2)
                persist_generation_state(state)
                raise RuntimeError("ChatGPT returned an empty outline.")
                
            filtered_parts = []
            removed_count = 0
            for part in parts:
                header = part.lower()[:100]
                if any(x in header for x in OUTLINE_INTRO_OUTRO_KEYWORDS):
                    removed_count += 1
                    continue
                filtered_parts.append(part)
                
            if removed_count > 0:
                print(f"    -> Đã loại bỏ {removed_count} phần Intro/Outro bị lẫn vào dàn ý.", file=sys.stderr)
                parts = filtered_parts
                if not parts:
                    raise RuntimeError("Tất cả dàn ý đều bị lọc bỏ vì chứa từ khóa Intro/Outro.")

            state["outline_parts"] = parts
            state["expected_body_parts"] = len(parts)
            clear_pending_generation_prompt(state, "outline", prompt2)
            persist_generation_state(state)
            print(f"    -> Đã chia thành {len(parts)} phần.", file=sys.stderr)
        else:
            state["expected_body_parts"] = len(parts)

        chat_url = state.get("chat_url", page.url)

        # Step 3: Intro
        if not state.get("intro"):
            print(">>> BƯỚC 3: VIẾT INTRO", file=sys.stderr)
            prompt3 = (
                prompts.get("intro", "")
                + STRICT_NO_FILLER
                + NARRATIVE_ONLY_INSTRUCTION
            )
            state["current_step"] = "intro"
            intro = dedup_consecutive_paragraphs(sanitize_narrative_response(
                send_or_recover_generation_prompt(
                    page,
                    state,
                    "intro",
                    prompt3,
                )
            ))
            if not intro:
                clear_pending_generation_prompt(state, "intro", prompt3)
                persist_generation_state(state)
                raise RuntimeError(
                    "ChatGPT returned no narrative INTRO content."
                )
            state["intro"] = intro
            clear_pending_generation_prompt(state, "intro", prompt3)
            persist_generation_state(state)
        else:
            intro = state["intro"]

        body_parts_result = state["body_parts"]
        if len(body_parts_result) > len(parts):
            raise RuntimeError("Checkpoint contains more BODY parts than the outline.")
        for i, part in enumerate(parts[len(body_parts_result):], start=len(body_parts_result)):
            print(f">>> BƯỚC 4: VIẾT BODY PHẦN {i+1}/{len(parts)}", file=sys.stderr)
            prompt4 = (
                prompts.get("body", "").replace("{part}", part)
                + STRICT_NO_FILLER
                + NARRATIVE_ONLY_INSTRUCTION
            )
            body_step = f"body {i + 1}/{len(parts)}"
            state["current_step"] = body_step
            res = dedup_consecutive_paragraphs(sanitize_narrative_response(
                send_or_recover_generation_prompt(
                    page,
                    state,
                    body_step,
                    prompt4,
                )
            ))
            if not res:
                clear_pending_generation_prompt(state, body_step, prompt4)
                persist_generation_state(state)
                raise RuntimeError(
                    "ChatGPT returned no narrative BODY content."
                )
            body_parts_result.append(res)
            clear_pending_generation_prompt(state, body_step, prompt4)
            persist_generation_state(state)
            
        # Step 5: Outro
        if not state.get("outro"):
            print(">>> BƯỚC 5: VIẾT OUTRO", file=sys.stderr)
            prompt5 = (
                prompts.get("outro", "")
                + STRICT_NO_FILLER
                + NARRATIVE_ONLY_INSTRUCTION
            )
            state["current_step"] = "outro"
            outro = dedup_consecutive_paragraphs(sanitize_narrative_response(
                send_or_recover_generation_prompt(
                    page,
                    state,
                    "outro",
                    prompt5,
                )
            ))
            if not outro:
                clear_pending_generation_prompt(state, "outro", prompt5)
                persist_generation_state(state)
                raise RuntimeError(
                    "ChatGPT returned no narrative OUTRO content."
                )
            state["outro"] = outro
            clear_pending_generation_prompt(state, "outro", prompt5)
            persist_generation_state(state)
        else:
            outro = state["outro"]

        if not is_core_script_complete(transcript, state):
            state["current_step"] = "core completeness"
            raise RuntimeError(
                "The rewritten core script is missing one or more required sections. "
                "No metadata, thumbnail, or audio request was sent."
            )

        # Step 6: Metadata & Quiz
        if pipeline["metadata"] and not state.get("metadata"):
            print(">>> BƯỚC 6: TẠO METADATA & QUIZ", file=sys.stderr)
            prompt6 = prompts.get("metadata", "") + STRICT_NO_FILLER
            state["current_step"] = "metadata"
            metadata = send_or_recover_generation_prompt(
                page,
                state,
                "metadata",
                prompt6,
            )
            state["metadata"] = metadata
            clear_pending_generation_prompt(state, "metadata", prompt6)
            persist_generation_state(state)

        # Step 7: Chapters
        if pipeline["chapters"] and not state.get("chapters"):
            print(">>> BƯỚC 7: TẠO CHAPTERS", file=sys.stderr)
            prompt7 = prompts.get("chapters", "") + STRICT_NO_FILLER
            state["current_step"] = "chapters"
            try:
                chapters = send_or_recover_generation_prompt(
                    page,
                    state,
                    "chapters",
                    prompt7,
                )
            except ChatGPTGenerationTimeoutError as exc:
                recovered_chapters = wait_for_valid_chapter_response(
                    page,
                    exc.previous_assistant_turn,
                    initial_response=exc.response_text.strip(),
                    previous_assistant_count=exc.previous_assistant_count,
                    expected_user_text=prompt7,
                )
                if recovered_chapters:
                    state["chapters"] = recovered_chapters
                    state["current_step"] = "thumbnail with text"
                    clear_pending_generation_prompt(state, "chapters", prompt7)
                    persist_generation_state(state)
                    raise RuntimeError(
                        "Chapter content was preserved, but ChatGPT remained busy. "
                        "No thumbnail prompt was sent."
                    ) from exc
                raise
            state["chapters"] = chapters
            clear_pending_generation_prompt(state, "chapters", prompt7)
            persist_generation_state(state)

        # Helper for image extraction
        def _download_image_local(chatgpt_url: str) -> str:
            """Download image via Playwright session and save locally."""
            try:
                local_path = download_chatgpt_image_via_page(page, chatgpt_url)
                print(f"    -> Đã download ảnh về local: {local_path}", file=sys.stderr)
                return local_path
            except Exception as e:
                print(f"    -> Lỗi download ảnh: {e}", file=sys.stderr)
                return ""

        # Step 8: Thumbnail Idea 1 (With Text)
        if pipeline["thumbnail_with_text"] and not state.get("thumb_text"):
            print(">>> BƯỚC 8: TẠO Ý TƯỞNG THUMBNAIL (CÓ CHỮ)", file=sys.stderr)
            prompt8 = build_thumbnail_generation_prompt(
                prompts.get("thumb_text", ""),
                "with_text",
            )
            state["current_step"] = "thumbnail with text"
            thumb1, image1_urls = send_thumbnail_prompt(
                page,
                prompt8,
                _download_image_local,
                prompts.get("thumb_text_image_base64")
            )
            if not image1_urls:
                print(">>> THUMBNAIL CÓ CHỮ LỖI, THỬ LẠI MỘT LẦN...", file=sys.stderr)
                thumb1, image1_urls = retry_thumbnail_generation(
                    page,
                    _download_image_local,
                )

            thumb1 = append_thumbnail_image_markers(thumb1, image1_urls)
            state["thumb_text"] = thumb1
            persist_generation_state(state)

        # Step 9: Thumbnail Idea 2 (No Text)
        if pipeline["thumbnail_without_text"] and not state.get("thumb_notext"):
            print(">>> BƯỚC 9: TẠO Ý TƯỞNG THUMBNAIL (KHÔNG CHỮ)", file=sys.stderr)
            prompt9 = build_thumbnail_generation_prompt(
                prompts.get("thumb_notext", ""),
                "without_text",
            )
            state["current_step"] = "thumbnail without text"
            thumb2, image2_urls = send_thumbnail_prompt(
                page,
                prompt9,
                _download_image_local,
                prompts.get("thumb_notext_image_base64")
            )
            if not image2_urls:
                print(">>> THUMBNAIL KHÔNG CHỮ LỖI, THỬ LẠI MỘT LẦN...", file=sys.stderr)
                thumb2, image2_urls = retry_thumbnail_generation(
                    page,
                    _download_image_local,
                )

            thumb2 = append_thumbnail_image_markers(thumb2, image2_urls)
            state["thumb_notext"] = thumb2
            persist_generation_state(state)

        state["current_step"] = "complete"
        persist_generation_state(state)
        complete_for_audio = is_core_script_complete(transcript, state)
        warning = "" if complete_for_audio else "The core video script is incomplete."
        return {
            "script": build_video_script(state),
            "chat_url": chat_url,
            "warning": warning,
            "failed_step": "" if complete_for_audio else state["current_step"],
            "complete_for_audio": complete_for_audio,
            "pipeline": pipeline,
        }


def run(transcript: str) -> dict:
    transcript_fingerprint = hashlib.sha256(
        transcript.encode("utf-8")
    ).hexdigest()
    state = {
        "chat_url": "",
        "current_step": "startup",
        "expected_body_parts": 0,
        "outline_parts": [],
        "intro": "",
        "body_parts": [],
        "outro": "",
        "metadata": "",
        "chapters": "",
        "thumb_text": "",
        "thumb_notext": "",
        "pending_prompt": None,
        "pipeline": get_active_pipeline(),
        "transcript_fingerprint": transcript_fingerprint,
    }
    video_id = _checkpoint_video_id()
    if video_id is not None:
        saved_state = load_checkpoint(video_id)
        if saved_state and saved_state.get("transcript_fingerprint") == transcript_fingerprint:
            state.update(saved_state)
            if os.environ.get(PROMPT_PIPELINE_ENV, "").strip():
                # The persistent queue snapshot is authoritative for automatic
                # recovery, even if Settings changed while the job was waiting.
                state["pipeline"] = get_active_pipeline()
            else:
                state["pipeline"] = normalize_prompt_pipeline(
                    state.get("pipeline")
                )
            print(
                f">>> KHÔI PHỤC CHECKPOINT VIDEO #{video_id}: "
                f"{state.get('current_step', 'unknown')}",
                file=sys.stderr,
            )
        elif saved_state:
            print(
                f"Warning: ignored mismatched checkpoint for video #{video_id}.",
                file=sys.stderr,
            )
    try:
        return _run_complete(transcript, state)
    except ChatGPTAttentionRequiredError:
        persist_generation_state(state)
        raise
    except Exception as exc:
        has_recoverable_content = bool(
            state["chat_url"]
            or state["intro"]
            or state["body_parts"]
            or state["outro"]
        )
        if not has_recoverable_content:
            raise

        failed_step = state["current_step"]
        warning = f"{failed_step}: {exc}"
        print(
            f">>> RECOVERED PARTIAL VIDEO AFTER {failed_step.upper()}: {exc}",
            file=sys.stderr,
        )
        complete_for_audio = is_core_script_complete(transcript, state)
        persist_generation_state(state)
        return {
            "script": build_video_script(state),
            "chat_url": state["chat_url"],
            "warning": warning,
            "failed_step": failed_step,
            "complete_for_audio": complete_for_audio,
            "pipeline": normalize_prompt_pipeline(state.get("pipeline")),
        }


def generate_chapters_only(
    script_text: str,
    chat_url: str = "",
    prompt_version: str = "",
    reuse_existing_response: bool = False,
) -> str:
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception("Profile directory not found. Please run the auto-login tool first.")

    with sync_playwright() as p:
        context = launch_chatgpt_context(p.chromium, profile_dir)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            is_original_chat = is_chatgpt_conversation_url(chat_url)
            target_url = (
                chat_url
                if is_original_chat
                else get_chatgpt_project_url(prompt_version)
            )
            if is_original_chat:
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=CHATGPT_NAVIGATION_TIMEOUT_MS,
                )
                check_chatgpt_page_attention(page)
                ensure_expected_conversation_page(page.url, target_url)
                wait_for_conversation_history(page)
            else:
                navigate_to_chatgpt_project(page, target_url)

            if is_original_chat and reuse_existing_response:
                try:
                    page.wait_for_function(
                        """() => document.querySelector(
                            '[data-testid="stop-button"]'
                        ) === null""",
                        timeout=CHATGPT_RESPONSE_TIMEOUT_SECONDS * 1000,
                    )
                except Exception as exc:
                    raise ChatGPTGenerationTimeoutError(
                        "Previous ChatGPT generation did not finish after "
                        "20 minutes. No new prompt was sent."
                    ) from exc

                reusable_chapters = get_reusable_chapter_response(page)
                if reusable_chapters:
                    print(
                        ">>> REUSED COMPLETED CHAPTER RESPONSE FROM THE SAME CHAT",
                        file=sys.stderr,
                    )
                    return reusable_chapters

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
            return sanitize_chapter_response(chapters)
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
            check_chatgpt_page_attention(page)
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


def generate_comment_replies(
    chat_url: str,
    comments: list[dict],
    reply_instruction: str = "",
    recent_replies: list[str] | None = None,
) -> dict[str, str]:
    """Draft replies inside the video's existing ChatGPT conversation only."""
    from auto_yt.services.youtube_comments import (
        build_comment_reply_prompt,
        parse_comment_reply_response,
    )

    conversation_url = get_video_chat_url(chat_url)
    expected_ids = {str(comment["comment_id"]) for comment in comments}
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception(
            "Profile directory not found. Please run the auto-login tool first."
        )

    with sync_playwright() as playwright, closing(
        launch_chatgpt_context(playwright.chromium, profile_dir)
    ) as context:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(
            conversation_url,
            wait_until="domcontentloaded",
            timeout=CHATGPT_NAVIGATION_TIMEOUT_MS,
        )
        check_chatgpt_page_attention(page)
        ensure_expected_conversation_page(page.url, conversation_url)
        wait_for_conversation_history(page)
        response_text = send_prompt(
            page,
            build_comment_reply_prompt(comments, reply_instruction, recent_replies),
        )
        return parse_comment_reply_response(response_text, expected_ids)


def initialize_comment_video_chat(
    *,
    title: str,
    description: str,
    transcript: str,
    prompt_version: str,
) -> str:
    """Create one dedicated Project conversation for a legacy YouTube video.

    A single prompt carries the complete available source context. If delivery
    becomes ambiguous after ChatGPT has already created the conversation, the
    new URL is returned instead of sending the prompt a second time.
    """
    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception(
            "Profile directory not found. Please run the auto-login tool first."
        )
    project_url = get_chatgpt_project_url(prompt_version)
    normalized_title = str(title or "").strip()
    normalized_description = str(description or "").strip()
    normalized_transcript = str(transcript or "").strip()
    if not normalized_title:
        raise ValueError("Legacy video title is required.")
    if not normalized_description and not normalized_transcript:
        raise ValueError(
            "Legacy video must have a description or transcript before Chat creation."
        )
    context_prompt = (
        "Đây là phiên Chat riêng dành duy nhất cho một video YouTube đã đăng. "
        "Hãy dùng tiêu đề, mô tả và transcript bên dưới làm nền tảng nội dung "
        "để trả lời các bình luận của người xem ở những lượt sau. Không trộn "
        "nội dung với video khác. Ở lượt này chỉ xác nhận ngắn gọn rằng bạn đã "
        "nhận nội dung, không phân tích và không viết lại kịch bản.\n\n"
        f"TIÊU ĐỀ VIDEO:\n{normalized_title}\n\n"
        f"MÔ TẢ VIDEO:\n{normalized_description or '(Không có mô tả)'}\n\n"
        f"TRANSCRIPT VIDEO:\n{normalized_transcript or '(Không có transcript)'}"
    )

    with sync_playwright() as playwright, closing(
        launch_chatgpt_context(playwright.chromium, profile_dir)
    ) as context:
        page = context.pages[0] if context.pages else context.new_page()
        navigate_to_chatgpt_project(page, project_url)
        try:
            send_prompt(page, context_prompt)
        except Exception:
            # One send may have succeeded even if the response watcher failed.
            # Saving the resulting conversation is safer than creating a second
            # chat or duplicating the context prompt.
            if is_expected_project_conversation_url(page.url, project_url):
                return page.url.strip().rstrip("/")
            raise
        ensure_expected_project_conversation_page(page.url, project_url)
        return page.url.strip().rstrip("/")


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

    with sync_playwright() as p, closing(
        launch_chatgpt_context(p.chromium, profile_dir)
    ) as context:
        page = context.pages[0] if context.pages else context.new_page()
        
        # Navigate to the original chat session if URL provided, otherwise open new chat
        is_original_chat = is_chatgpt_conversation_url(chat_url)
        target_url = (
            chat_url
            if is_original_chat
            else get_chatgpt_project_url(prompt_version)
        )
        print(f"    -> Navigating to: {target_url}", file=sys.stderr)
        if is_original_chat:
            page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=CHATGPT_NAVIGATION_TIMEOUT_MS,
            )
            check_chatgpt_page_attention(page)
            ensure_expected_conversation_page(page.url, target_url)
        else:
            navigate_to_chatgpt_project(page, target_url)
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
                local_path = download_chatgpt_image_via_page(page, chatgpt_url)
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
        thumb1, image1_urls = send_thumbnail_prompt(
            page,
            prompt8,
            lambda image_url: _download_image(page, image_url),
            prompts.get("thumb_text_image_base64")
        )
        if not image1_urls:
            thumb1, image1_urls = retry_thumbnail_generation(
                page,
                lambda image_url: _download_image(page, image_url),
            )

        print(">>> GEN THUMBNAIL (KHÔNG CHỮ)", file=sys.stderr)
        prompt9 = build_thumbnail_generation_prompt(
            prompts.get("thumb_notext", ""),
            "without_text",
        )
        thumb2, image2_urls = send_thumbnail_prompt(
            page,
            prompt9,
            lambda image_url: _download_image(page, image_url),
            prompts.get("thumb_notext_image_base64")
        )
        if not image2_urls:
            thumb2, image2_urls = retry_thumbnail_generation(
                page,
                lambda image_url: _download_image(page, image_url),
            )

        return {
            "thumb_text": thumb1,
            "thumb_notext": thumb2,
            "image1_urls": image1_urls,
            "image2_urls": image2_urls,
            "image1_url": image1_urls[0] if image1_urls else "",
            "image2_url": image2_urls[0] if image2_urls else "",
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
            "images_result_key": "image1_urls",
            "label": "CÓ CHỮ",
        },
        "without_text": {
            "prompt_key": "thumb_notext",
            "text_result_key": "thumb_notext",
            "image_result_key": "image2_url",
            "images_result_key": "image2_urls",
            "label": "KHÔNG CHỮ",
        },
    }
    config = thumbnail_configs.get(thumbnail_type)
    if config is None:
        raise ValueError(f"Unsupported thumbnail type: {thumbnail_type}")

    profile_dir = gpt_profile_dir(DEFAULT_GPT_PROFILE)
    if not profile_dir.exists():
        raise Exception("Profile directory not found. Please run the auto-login tool first.")

    with sync_playwright() as p, closing(
        launch_chatgpt_context(p.chromium, profile_dir)
    ) as context:
        page = context.pages[0] if context.pages else context.new_page()
        target_url = get_video_thumbnail_chat_url(chat_url)
        print(f"    -> Navigating to: {target_url}", file=sys.stderr)
        page.goto(target_url, wait_until="domcontentloaded")
        check_chatgpt_page_attention(page)
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
                return download_chatgpt_image_via_page(page, chatgpt_url)
            except Exception as exc:
                print(f"    -> Image download failed: {exc}", file=sys.stderr)
                return ""

        generation_prompt = build_thumbnail_generation_prompt(
            prompts.get(config["prompt_key"], ""),
            thumbnail_type,
        )

        print(f">>>> REGENERATE THUMBNAIL ({config['label']})", file=sys.stderr)
        image_base64 = prompts.get(f"{config['prompt_key']}_image_base64")
        response_text, image_urls = send_thumbnail_prompt(
            page,
            generation_prompt,
            download_image,
            image_base64
        )
        retry_succeeded = False
        if not image_urls:
            print(
                f">>>> THUMBNAIL {config['label']} LỖI, THỬ LẠI MỘT LẦN...",
                file=sys.stderr,
            )
            retry_response_text, image_urls = retry_thumbnail_generation(
                page,
                download_image,
            )
            retry_succeeded = bool(image_urls)

        result = {
            "thumb_text": None,
            "thumb_notext": None,
            "image1_url": "",
            "image2_url": "",
            "image1_urls": [],
            "image2_urls": [],
        }
        result[config["text_result_key"]] = (
            None if retry_succeeded else response_text
        )
        result[config["images_result_key"]] = image_urls
        result[config["image_result_key"]] = image_urls[0] if image_urls else ""
        return result


if __name__ == "__main__":
    transcript = sys.stdin.buffer.read().decode("utf-8")
    try:
        result = run(transcript)
        script = result["script"] if isinstance(result, dict) else result
        chat_url = result.get("chat_url", "") if isinstance(result, dict) else ""
        worker_meta = {
            "warning": result.get("warning", "") if isinstance(result, dict) else "",
            "failed_step": result.get("failed_step", "") if isinstance(result, dict) else "",
            "complete_for_audio": (
                result.get("complete_for_audio", True)
                if isinstance(result, dict)
                else True
            ),
            "pipeline": (
                result.get("pipeline", normalize_prompt_pipeline(None))
                if isinstance(result, dict)
                else normalize_prompt_pipeline(None)
            ),
        }
        # Print script then marker then chat_url for the service to parse
        sys.stdout.buffer.write(script.encode("utf-8"))
        if chat_url:
            sys.stdout.buffer.write(f"\n###CHAT_URL###{chat_url}".encode("utf-8"))
        sys.stdout.buffer.write(
            f"\n###WORKER_META###{json.dumps(worker_meta)}".encode("utf-8")
        )
    except Exception as e:
        if isinstance(e, ChatGPTAttentionRequiredError):
            sys.stderr.write(encode_attention_error(str(e)))
        else:
            sys.stderr.write(str(e))
        sys.exit(1)
