import sqlite3
import datetime
import re
import unicodedata
from pathlib import Path

# Fix the path to point correctly from where the app runs
# Since main.py is run from the project root usually, we can resolve relative to this file
_HERE = Path(__file__).resolve()
PROJECT_ROOT = _HERE.parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "database.db"

GENERATED_TITLE_LABELS = {"TIEU DE", "TIEU DE VIDEO"}
GENERATED_DESCRIPTION_LABELS = {"MO TA", "MO TA VIDEO", "MO TA VIDEO CHAPTERS"}
METADATA_FIELD_LABELS = GENERATED_TITLE_LABELS | GENERATED_DESCRIPTION_LABELS | {
    "URL SLUG",
    "SLUG",
    "HASHTAG",
    "BINH LUAN GHIM",
    "CAU HOI",
    "CAU HOI KHAN GIA",
    "DAP AN DUNG",
    "CAU TRA LOI DUNG",
    "GIAI THICH",
    "QUIZ",
    "CHAPTER",
    "CHAPTERS",
}
METADATA_SECTION_PATTERN = re.compile(
    r"### \[METADATA & QUIZ\]\n(.*?)(?=\n### \[|\Z)",
    flags=re.DOTALL,
)


def _normalize_metadata_label(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFD",
        value.replace("Đ", "D").replace("đ", "d"),
    )
    without_accents = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    return re.sub(r"[^A-Z0-9]+", " ", without_accents.upper()).strip()


def _clean_generated_title(value: str) -> str:
    cleaned = value.strip().strip("#*` ").strip('"“”')
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_generated_video_title(generated_script: str) -> str:
    if not generated_script:
        return ""

    metadata_match = METADATA_SECTION_PATTERN.search(generated_script)
    metadata = metadata_match.group(1) if metadata_match else generated_script
    lines = metadata.splitlines()

    for index, raw_line in enumerate(lines):
        line = raw_line.strip().strip("#*` ")
        label, separator, inline_value = line.partition(":")
        if _normalize_metadata_label(label) not in GENERATED_TITLE_LABELS:
            continue

        if separator:
            title = _clean_generated_title(inline_value)
            if title:
                return title

        for following_line in lines[index + 1:]:
            candidate = _clean_generated_title(following_line)
            if not candidate:
                continue
            candidate_label = candidate.partition(":")[0]
            if _normalize_metadata_label(candidate_label) in {
                "URL SLUG",
                "MO TA VIDEO",
                "HASHTAG",
            }:
                break
            return candidate
    return ""


def extract_generated_video_description(generated_script: str) -> str:
    if not generated_script:
        return ""

    metadata_match = METADATA_SECTION_PATTERN.search(generated_script)
    metadata = metadata_match.group(1) if metadata_match else generated_script
    lines = metadata.splitlines()

    for index, raw_line in enumerate(lines):
        line = raw_line.strip().strip("#*` ")
        label, separator, inline_value = line.partition(":")
        if _normalize_metadata_label(label) not in GENERATED_DESCRIPTION_LABELS:
            continue

        description_lines = []
        if separator and inline_value.strip():
            description_lines.append(inline_value.strip())

        for following_line in lines[index + 1:]:
            candidate = following_line.strip()
            if not candidate:
                continue
            if candidate.startswith("#"):
                break

            candidate_label, candidate_separator, _ = (
                candidate.strip("#*` ").partition(":")
            )
            normalized_label = _normalize_metadata_label(candidate_label)
            if candidate_separator and normalized_label in METADATA_FIELD_LABELS:
                break
            description_lines.append(candidate)

        return re.sub(r"\s+", " ", " ".join(description_lines)).strip()
    return ""


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFD",
        (value or "").replace("Đ", "D").replace("đ", "d"),
    )
    without_accents = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    searchable = re.sub(r"[^a-z0-9]+", " ", without_accents.casefold())
    return re.sub(r"\s+", " ", searchable).strip()


def build_video_search_text(
    original_url: str,
    original_title: str,
    generated_script: str,
    generated_title: str = "",
) -> str:
    resolved_generated_title = (
        generated_title or extract_generated_video_title(generated_script)
    )
    generated_description = extract_generated_video_description(generated_script)
    return normalize_search_text(
        " ".join(
            part
            for part in (
                original_url,
                original_title,
                resolved_generated_title,
                generated_description,
            )
            if part
        )
    )

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            transcript TEXT NOT NULL,
            generated_script TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_published INTEGER DEFAULT 0,
            chat_url TEXT DEFAULT '',
            prompt_version TEXT DEFAULT '',
            generated_title TEXT DEFAULT '',
            search_text TEXT DEFAULT '',
            voice_id TEXT DEFAULT '',
            voice_name TEXT DEFAULT '',
            audio_duration_seconds REAL
        )
    ''')
    # Try adding the column if upgrading from older version
    try:
        c.execute("ALTER TABLE videos ADD COLUMN is_published INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN chat_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN prompt_version TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN generated_title TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN search_text TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN voice_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN voice_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN audio_duration_seconds REAL")
    except sqlite3.OperationalError:
        pass
    c.execute('''
        CREATE TABLE IF NOT EXISTS audio_tasks (
            video_id INTEGER PRIMARY KEY,
            request_hash TEXT NOT NULL,
            task_id TEXT NOT NULL,
            status TEXT NOT NULL,
            audio_url TEXT DEFAULT '',
            error TEXT DEFAULT '',
            segments_json TEXT DEFAULT '',
            voice_id TEXT DEFAULT '',
            voice_name TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_audio_tasks_request_hash '
        'ON audio_tasks(request_hash)'
    )
    try:
        c.execute("ALTER TABLE audio_tasks ADD COLUMN segments_json TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE audio_tasks ADD COLUMN voice_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE audio_tasks ADD COLUMN voice_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    c.execute(
        "SELECT id, generated_script FROM videos "
        "WHERE COALESCE(generated_title, '') = ''"
    )
    generated_title_updates = [
        (extract_generated_video_title(script), video_id)
        for video_id, script in c.fetchall()
    ]
    c.executemany(
        "UPDATE videos SET generated_title = ? WHERE id = ?",
        [update for update in generated_title_updates if update[0]],
    )
    c.execute(
        "SELECT id, url, title, generated_script, generated_title FROM videos"
    )
    search_text_updates = [
        (
            build_video_search_text(url, title, script, generated_title),
            video_id,
        )
        for video_id, url, title, script, generated_title in c.fetchall()
    ]
    c.executemany(
        "UPDATE videos SET search_text = ? WHERE id = ?",
        search_text_updates,
    )
    conn.commit()
    conn.close()

def save_video(
    url: str,
    title: str,
    transcript: str,
    generated_script: str,
    chat_url: str = '',
    prompt_version: str = '',
    voice_id: str = '',
    voice_name: str = '',
) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    generated_title = extract_generated_video_title(generated_script)
    c.execute('''
        INSERT INTO videos (
            url, title, transcript, generated_script, created_at, chat_url,
            prompt_version, generated_title, search_text, voice_id, voice_name
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        url,
        title,
        transcript,
        generated_script,
        datetime.datetime.now().isoformat(),
        chat_url,
        prompt_version,
        generated_title,
        build_video_search_text(url, title, generated_script, generated_title),
        voice_id,
        voice_name,
    ))
    video_id = c.lastrowid
    conn.commit()
    conn.close()
    return video_id

def get_all_videos(
    limit: int = 10,
    offset: int = 0,
    is_published: int = None,
    prompt_version: str = None,
    search_query: str = None,
) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    scope_filters = []
    scope_params = []
    if prompt_version is not None:
        scope_filters.append('prompt_version = ?')
        scope_params.append(prompt_version)
    normalized_query = normalize_search_text(search_query or '')
    if normalized_query:
        scope_filters.append('search_text LIKE ?')
        scope_params.append(f'%{normalized_query}%')
    scope_clause = (
        f" AND {' AND '.join(scope_filters)}" if scope_filters else ''
    )

    # Counts follow the selected prompt version while remaining independent of
    # the publication-status filter.
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE is_published = 1{scope_clause}',
        scope_params,
    )
    count_published = c.fetchone()[0]
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE is_published = 0{scope_clause}',
        scope_params,
    )
    count_unpublished = c.fetchone()[0]

    filters = []
    params = []
    if is_published is not None:
        filters.append('is_published = ?')
        params.append(is_published)
    filters.extend(scope_filters)
    params.extend(scope_params)

    where_clause = f" WHERE {' AND '.join(filters)}" if filters else ''
    c.execute(f'SELECT COUNT(*) FROM videos{where_clause}', params)
    total = c.fetchone()[0]
    c.execute(
        'SELECT id, url, '
        "COALESCE(NULLIF(generated_title, ''), title) AS title, "
        'created_at, is_published, chat_url, '
        'prompt_version, voice_id, voice_name, audio_duration_seconds, '
        'SUBSTR(generated_script, 1, 300) as snippet, '
        'COALESCE('
        'NULLIF((SELECT audio_url FROM audio_tasks WHERE video_id = videos.id), \'\'), '
        'CASE WHEN INSTR(generated_script, \'### [AUDIO]\') > 0 '
        'THEN TRIM(SUBSTR('
        'generated_script, '
        'INSTR(generated_script, \'### [AUDIO]\') + LENGTH(\'### [AUDIO]\')'
        ')) ELSE \'\' END'
        ') AS audio_url '
        f'FROM videos{where_clause} ORDER BY id DESC LIMIT ? OFFSET ?',
        (*params, limit, offset),
    )

    rows = c.fetchall()
    conn.close()
    
    return {
        "total": total,
        "count_published": count_published,
        "count_unpublished": count_unpublished,
        "items": [dict(row) for row in rows]
    }

def toggle_published(video_id: int, is_published: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('UPDATE videos SET is_published = ? WHERE id = ?', (is_published, video_id))
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success

def get_video(video_id: int) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM videos WHERE id = ?', (video_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_video(video_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('DELETE FROM audio_tasks WHERE video_id = ?', (video_id,))
    c.execute('DELETE FROM videos WHERE id = ?', (video_id,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def update_script(video_id: int, new_script: str) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('SELECT url, title FROM videos WHERE id = ?', (video_id,))
    row = c.fetchone()
    if row is None:
        conn.close()
        return False
    generated_title = extract_generated_video_title(new_script)
    c.execute(
        'UPDATE videos SET generated_script = ?, generated_title = ?, '
        'search_text = ? '
        'WHERE id = ?',
        (
            new_script,
            generated_title,
            build_video_search_text(row[0], row[1], new_script, generated_title),
            video_id,
        ),
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success


def update_video_generation(
    video_id: int,
    generated_script: str,
    chat_url: str,
) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('SELECT url, title FROM videos WHERE id = ?', (video_id,))
    row = c.fetchone()
    if row is None:
        conn.close()
        return False
    generated_title = extract_generated_video_title(generated_script)
    c.execute(
        '''
        UPDATE videos
        SET generated_script = ?, chat_url = ?, generated_title = ?,
            search_text = ?
        WHERE id = ?
        ''',
        (
            generated_script,
            chat_url,
            generated_title,
            build_video_search_text(
                row[0],
                row[1],
                generated_script,
                generated_title,
            ),
            video_id,
        ),
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success


def update_audio_duration(video_id: int, duration_seconds: float | None) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'UPDATE videos SET audio_duration_seconds = ? WHERE id = ?',
        (duration_seconds, video_id),
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success


def update_video_voice(video_id: int, voice_id: str, voice_name: str) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        'UPDATE videos SET voice_id = ?, voice_name = ? WHERE id = ?',
        (voice_id, voice_name, video_id),
    )
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success

def get_audio_task(video_id: int) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM audio_tasks WHERE video_id = ?', (video_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_audio_task_by_request_hash(request_hash: str) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT * FROM audio_tasks
        WHERE request_hash = ?
        ORDER BY
            CASE status
                WHEN 'completed' THEN 0
                WHEN 'processing' THEN 1
                WHEN 'pending' THEN 2
                WHEN 'failed' THEN 3
                ELSE 4
            END,
            created_at ASC
        LIMIT 1
        ''',
        (request_hash,),
    )
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_active_audio_tasks() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT * FROM audio_tasks WHERE status IN ('pending', 'processing')"
    )
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def upsert_audio_task(
    video_id: int,
    request_hash: str,
    task_id: str,
    status: str,
    audio_url: str = '',
    error: str = '',
    segments_json: str = '',
    voice_id: str = '',
    voice_name: str = '',
) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO audio_tasks (
            video_id, request_hash, task_id, status, audio_url, error,
            segments_json, voice_id, voice_name, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            request_hash = excluded.request_hash,
            task_id = excluded.task_id,
            status = excluded.status,
            audio_url = excluded.audio_url,
            error = excluded.error,
            segments_json = excluded.segments_json,
            voice_id = CASE
                WHEN excluded.voice_id != '' THEN excluded.voice_id
                ELSE audio_tasks.voice_id
            END,
            voice_name = CASE
                WHEN excluded.voice_name != '' THEN excluded.voice_name
                ELSE audio_tasks.voice_name
            END,
            updated_at = excluded.updated_at
        WHERE NOT (
            audio_tasks.status = 'completed'
            AND audio_tasks.request_hash = excluded.request_hash
            AND excluded.status != 'completed'
        )
        ''',
        (
            video_id,
            request_hash,
            task_id,
            status,
            audio_url,
            error,
            segments_json,
            voice_id,
            voice_name,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_audio_task(video_id)

# Initialize tables when module is imported
init_db()
