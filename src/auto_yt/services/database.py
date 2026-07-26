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
            prompt_version TEXT DEFAULT ''
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

def get_all_videos(limit: int = 10, offset: int = 0, is_published: int = None) -> dict:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get counts for all statuses
    c.execute('SELECT COUNT(*) FROM videos WHERE is_published = 1')
    count_published = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM videos WHERE is_published = 0')
    count_unpublished = c.fetchone()[0]
    
    # Apply filter
    if is_published is not None:
        c.execute('SELECT COUNT(*) FROM videos WHERE is_published = ?', (is_published,))
        total = c.fetchone()[0]
        c.execute('SELECT id, url, title, created_at, is_published, chat_url, prompt_version, SUBSTR(generated_script, 1, 300) as snippet FROM videos WHERE is_published = ? ORDER BY id DESC LIMIT ? OFFSET ?', (is_published, limit, offset))
    else:
        c.execute('SELECT COUNT(*) FROM videos')
        total = c.fetchone()[0]
        c.execute('SELECT id, url, title, created_at, is_published, chat_url, prompt_version, SUBSTR(generated_script, 1, 300) as snippet FROM videos ORDER BY id DESC LIMIT ? OFFSET ?', (limit, offset))
    
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

# Initialize tables when module is imported
init_db()
