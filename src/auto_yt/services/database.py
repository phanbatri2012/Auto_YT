import sqlite3
import datetime
from pathlib import Path

# Fix the path to point correctly from where the app runs
# Since main.py is run from the project root usually, we can resolve relative to this file
_HERE = Path(__file__).resolve()
PROJECT_ROOT = _HERE.parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "database.db"

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
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_audio_tasks_request_hash '
        'ON audio_tasks(request_hash)'
    )
    conn.commit()
    conn.close()

def save_video(url: str, title: str, transcript: str, generated_script: str, chat_url: str = '', prompt_version: str = '') -> int:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute('''
        INSERT INTO videos (url, title, transcript, generated_script, created_at, chat_url, prompt_version)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (url, title, transcript, generated_script, datetime.datetime.now().isoformat(), chat_url, prompt_version))
    video_id = c.lastrowid
    conn.commit()
    conn.close()
    return video_id

def get_all_videos(
    limit: int = 10,
    offset: int = 0,
    is_published: int = None,
    prompt_version: str = None,
) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    version_clause = ''
    version_params = []
    if prompt_version is not None:
        version_clause = ' AND prompt_version = ?'
        version_params.append(prompt_version)

    # Counts follow the selected prompt version while remaining independent of
    # the publication-status filter.
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE is_published = 1{version_clause}',
        version_params,
    )
    count_published = c.fetchone()[0]
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE is_published = 0{version_clause}',
        version_params,
    )
    count_unpublished = c.fetchone()[0]

    filters = []
    params = []
    if is_published is not None:
        filters.append('is_published = ?')
        params.append(is_published)
    if prompt_version is not None:
        filters.append('prompt_version = ?')
        params.append(prompt_version)

    where_clause = f" WHERE {' AND '.join(filters)}" if filters else ''
    c.execute(f'SELECT COUNT(*) FROM videos{where_clause}', params)
    total = c.fetchone()[0]
    c.execute(
        'SELECT id, url, title, created_at, is_published, chat_url, '
        'prompt_version, audio_duration_seconds, '
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
    c.execute('UPDATE videos SET generated_script = ? WHERE id = ?', (new_script, video_id))
    success = c.rowcount > 0
    conn.commit()
    conn.close()
    return success

def update_audio_duration(video_id: int, duration_seconds: float) -> bool:
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
) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO audio_tasks (
            video_id, request_hash, task_id, status, audio_url, error,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            request_hash = excluded.request_hash,
            task_id = excluded.task_id,
            status = excluded.status,
            audio_url = excluded.audio_url,
            error = excluded.error,
            updated_at = excluded.updated_at
        ''',
        (
            video_id,
            request_hash,
            task_id,
            status,
            audio_url,
            error,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_audio_task(video_id)

# Initialize tables when module is imported
init_db()
