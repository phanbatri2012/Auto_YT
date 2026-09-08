import sqlite3
import datetime
import json
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS audio_reviews (
            video_id INTEGER PRIMARY KEY,
            script_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            report_json TEXT DEFAULT '{}',
            reviewed_at TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS system_jobs (
            id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT DEFAULT '',
            progress TEXT DEFAULT '',
            payload_json TEXT DEFAULT '{}',
            result_json TEXT DEFAULT '{}',
            error TEXT DEFAULT '',
            video_id INTEGER,
            prompt_version TEXT DEFAULT '',
            voice_id TEXT DEFAULT '',
            attempt INTEGER DEFAULT 0,
            recovery_count INTEGER DEFAULT 0,
            resume_from_step TEXT DEFAULT '',
            next_retry_at TEXT DEFAULT '',
            cancel_requested INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT DEFAULT '',
            finished_at TEXT DEFAULT '',
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE SET NULL
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_system_jobs_queue '
        'ON system_jobs(job_type, status, created_at)'
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
    for column_definition in (
        "recovery_count INTEGER DEFAULT 0",
        "resume_from_step TEXT DEFAULT ''",
        "next_retry_at TEXT DEFAULT ''",
    ):
        try:
            c.execute(f"ALTER TABLE system_jobs ADD COLUMN {column_definition}")
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
        "COALESCE((SELECT status FROM audio_reviews WHERE video_id = videos.id), '') "
        'AS audio_review_status, '
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
    c.execute('DELETE FROM audio_reviews WHERE video_id = ?', (video_id,))
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


def get_audio_review(video_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM audio_reviews WHERE video_id = ?', (video_id,))
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    review = dict(row)
    try:
        review["report"] = json.loads(review.get("report_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        review["report"] = {}
    review.pop("report_json", None)
    return review


def upsert_audio_review(
    video_id: int,
    script_hash: str,
    status: str,
    report: dict,
    reviewed_at: str = "",
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO audio_reviews (
            video_id, script_hash, status, report_json, reviewed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            script_hash = excluded.script_hash,
            status = excluded.status,
            report_json = excluded.report_json,
            reviewed_at = excluded.reviewed_at,
            updated_at = excluded.updated_at
        ''',
        (
            video_id,
            script_hash,
            status,
            json.dumps(report, ensure_ascii=False),
            reviewed_at,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_audio_review(video_id)


def list_audio_reviews(limit: int = 100) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT audio_reviews.*,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS title,
               videos.title AS original_title,
               videos.generated_title AS generated_title,
               videos.url AS video_url
        FROM audio_reviews
        LEFT JOIN videos ON videos.id = audio_reviews.video_id
        ORDER BY audio_reviews.updated_at DESC
        LIMIT ?
        ''',
        (max(1, min(int(limit), 500)),),
    )
    rows = c.fetchall()
    conn.close()
    reviews = []
    for row in rows:
        review = dict(row)
        try:
            review["report"] = json.loads(review.get("report_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            review["report"] = {}
        review.pop("report_json", None)
        reviews.append(review)
    return reviews

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


def list_audio_tasks(limit: int = 100) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT audio_tasks.*,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS title,
               videos.title AS original_title,
               videos.generated_title AS generated_title,
               videos.url AS video_url
        FROM audio_tasks
        LEFT JOIN videos ON videos.id = audio_tasks.video_id
        ORDER BY audio_tasks.updated_at DESC
        LIMIT ?
        ''',
        (max(1, min(int(limit), 500)),),
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


SYSTEM_JOB_JSON_FIELDS = {"payload_json", "result_json"}
SYSTEM_JOB_MUTABLE_FIELDS = {
    "status",
    "title",
    "progress",
    "payload_json",
    "result_json",
    "error",
    "video_id",
    "prompt_version",
    "voice_id",
    "attempt",
    "recovery_count",
    "resume_from_step",
    "next_retry_at",
    "cancel_requested",
    "started_at",
    "finished_at",
}


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _decode_system_job(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    job = dict(row)
    for field in SYSTEM_JOB_JSON_FIELDS:
        try:
            job[field.removesuffix("_json")] = json.loads(job.get(field) or "{}")
        except (TypeError, json.JSONDecodeError):
            job[field.removesuffix("_json")] = {}
        job.pop(field, None)
    job["cancel_requested"] = bool(job.get("cancel_requested"))
    return job


def create_system_job(
    job_id: str,
    job_type: str,
    title: str,
    payload: dict,
    prompt_version: str = "",
    voice_id: str = "",
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO system_jobs (
            id, job_type, status, title, progress, payload_json,
            result_json, error, prompt_version, voice_id, created_at, updated_at
        ) VALUES (?, ?, 'queued', ?, ?, ?, '{}', '', ?, ?, ?, ?)
        ''',
        (
            job_id,
            job_type,
            title,
            "Đang chờ trong hàng đợi",
            json.dumps(payload, ensure_ascii=False),
            prompt_version,
            voice_id,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_system_job(job_id)


def get_system_job(job_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM system_jobs WHERE id = ?", (job_id,))
    row = c.fetchone()
    conn.close()
    return _decode_system_job(row)


def list_system_jobs(
    limit: int = 100,
    job_type: str | None = None,
) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    params: list = []
    where_clause = ""
    if job_type:
        where_clause = " WHERE job_type = ?"
        params.append(job_type)
    params.append(max(1, min(int(limit), 500)))
    c.execute(
        f'''
        SELECT system_jobs.*,
               videos.url AS video_url,
               videos.title AS original_title,
               videos.generated_title AS generated_title
        FROM system_jobs
        LEFT JOIN videos ON videos.id = system_jobs.video_id
        {where_clause}
        ORDER BY system_jobs.created_at DESC
        LIMIT ?
        ''',
        params,
    )
    rows = c.fetchall()
    conn.close()
    return [_decode_system_job(row) for row in rows]


def update_system_job(job_id: str, **changes) -> dict | None:
    invalid_fields = set(changes) - SYSTEM_JOB_MUTABLE_FIELDS
    if invalid_fields:
        raise ValueError(f"Unsupported system job fields: {sorted(invalid_fields)}")
    if not changes:
        return get_system_job(job_id)

    encoded_changes = dict(changes)
    for field in SYSTEM_JOB_JSON_FIELDS:
        if field in encoded_changes and not isinstance(encoded_changes[field], str):
            encoded_changes[field] = json.dumps(
                encoded_changes[field],
                ensure_ascii=False,
            )
    encoded_changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in encoded_changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    c = conn.cursor()
    c.execute(
        f"UPDATE system_jobs SET {assignments} WHERE id = ?",
        (*encoded_changes.values(), job_id),
    )
    conn.commit()
    conn.close()
    return get_system_job(job_id)


def claim_next_system_job(job_type: str) -> dict | None:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            '''
            SELECT * FROM system_jobs
            WHERE job_type = ? AND cancel_requested = 0
              AND status IN ('queued', 'retry_wait')
            ORDER BY created_at ASC
            LIMIT 1
            ''',
            (job_type,),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        if (
            row["status"] == "retry_wait"
            and row["next_retry_at"]
            and row["next_retry_at"] > now
        ):
            conn.execute("COMMIT")
            return None
        conn.execute(
            '''
            UPDATE system_jobs
            SET status = 'running', progress = ?, attempt = attempt + 1,
                started_at = ?, finished_at = '', next_retry_at = '', updated_at = ?
            WHERE id = ? AND status IN ('queued', 'retry_wait')
            ''',
            ("Đang khởi động", now, now, row["id"]),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return get_system_job(row["id"])


def recover_interrupted_system_jobs(job_type: str) -> int:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    c = conn.cursor()
    c.execute(
        '''
        UPDATE system_jobs
        SET status = CASE WHEN cancel_requested = 1 THEN 'canceled' ELSE 'queued' END,
            progress = CASE
                WHEN cancel_requested = 1 THEN 'Đã hủy khi ứng dụng khởi động lại'
                ELSE 'Đã khôi phục sau khi ứng dụng khởi động lại'
            END,
            finished_at = CASE WHEN cancel_requested = 1 THEN ? ELSE '' END,
            recovery_count = CASE
                WHEN cancel_requested = 1 THEN recovery_count
                ELSE recovery_count + 1
            END,
            next_retry_at = '',
            cancel_requested = 0,
            updated_at = ?
        WHERE job_type = ? AND status = 'running'
        ''',
        (now, now, job_type),
    )
    recovered = c.rowcount
    conn.commit()
    conn.close()
    return recovered


def schedule_system_job_recovery(
    job_id: str,
    resume_from_step: str,
    delay_seconds: float,
    error: str,
    result_json: dict | None = None,
) -> dict | None:
    retry_at = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=max(0.0, float(delay_seconds)))
    ).isoformat()
    changes = {
        "status": "retry_wait",
        "progress": (
            f"Chờ tự phục hồi từ bước {resume_from_step or 'gần nhất'}"
        ),
        "error": error,
        "resume_from_step": resume_from_step,
        "next_retry_at": retry_at,
        "cancel_requested": 0,
        "finished_at": "",
    }
    if result_json is not None:
        changes["result_json"] = result_json

    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT recovery_count FROM system_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        changes["recovery_count"] = int(row[0] or 0) + 1
        changes["updated_at"] = utc_now()
        assignments = ", ".join(f"{field} = ?" for field in changes)
        encoded_values = []
        for field, value in changes.items():
            if field in SYSTEM_JOB_JSON_FIELDS and not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            encoded_values.append(value)
        conn.execute(
            f"UPDATE system_jobs SET {assignments} WHERE id = ?",
            (*encoded_values, job_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return get_system_job(job_id)


def has_claimable_system_jobs(job_type: str) -> bool:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT status, next_retry_at FROM system_jobs
        WHERE job_type = ? AND cancel_requested = 0
          AND status IN ('queued', 'retry_wait')
        ORDER BY created_at ASC
        LIMIT 1
        ''',
        (job_type,),
    )
    row = c.fetchone()
    conn.close()
    if row is None:
        return False
    return (
        row["status"] == "queued"
        or not row["next_retry_at"]
        or row["next_retry_at"] <= now
    )


def get_next_system_job_retry_delay(job_type: str) -> float | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT status, next_retry_at FROM system_jobs
        WHERE job_type = ? AND status IN ('queued', 'retry_wait')
          AND cancel_requested = 0
        ORDER BY created_at ASC
        LIMIT 1
        ''',
        (job_type,),
    )
    row = c.fetchone()
    conn.close()
    if not row or row["status"] == "queued":
        return 0.0 if row else None
    if not row["next_retry_at"]:
        return None
    try:
        retry_at = datetime.datetime.fromisoformat(row["next_retry_at"])
    except (TypeError, ValueError):
        return 0.0
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return max(0.0, (retry_at - now).total_seconds())


def request_cancel_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["status"] in {"queued", "retry_wait", "paused"}:
        return update_system_job(
            job_id,
            status="canceled",
            progress="Đã hủy khỏi hàng đợi",
            cancel_requested=0,
            finished_at=utc_now(),
        )
    if job["status"] == "running":
        return update_system_job(
            job_id,
            progress="Đã nhận yêu cầu dừng; sẽ dừng tại điểm an toàn",
            cancel_requested=1,
        )
    return job


def pause_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["status"] not in {"queued", "retry_wait"}:
        raise ValueError("Chỉ có thể tạm dừng job đang chờ.")
    return update_system_job(
        job_id,
        status="paused",
        progress="Đã tạm dừng trong hàng đợi",
    )


def resume_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["status"] != "paused":
        raise ValueError("Chỉ có thể tiếp tục job đang tạm dừng.")
    return update_system_job(
        job_id,
        status="queued",
        progress="Đang chờ sau khi tiếp tục",
        next_retry_at="",
    )


def retry_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["status"] not in {"error", "canceled"}:
        raise ValueError("Chỉ có thể chạy lại job lỗi hoặc đã hủy.")
    return update_system_job(
        job_id,
        status="queued",
        progress="Đang chờ chạy lại",
        error="",
        result_json={},
        recovery_count=0,
        next_retry_at="",
        cancel_requested=0,
        finished_at="",
    )


def get_system_job_queue_position(job_id: str) -> int | None:
    job = get_system_job(job_id)
    if not job or job["status"] != "queued":
        return None
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        SELECT COUNT(*) FROM system_jobs
        WHERE job_type = ? AND status = 'queued' AND cancel_requested = 0
          AND created_at <= ?
        ''',
        (job["job_type"], job["created_at"]),
    )
    position = int(c.fetchone()[0])
    conn.close()
    return position

# Initialize tables when module is imported
init_db()
