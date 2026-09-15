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
TTS_V2_BACKUP_SUFFIX = ".pre_tts_v2.bak"
NULLABLE_PUBLICATION_CHANNEL_BACKUP_SUFFIX = ".pre_nullable_publication_channel.bak"

VIDEO_STATUS_ACTIVE = "active"
VIDEO_STATUS_ERROR = "error"
VIDEO_STATUSES = {VIDEO_STATUS_ACTIVE, VIDEO_STATUS_ERROR}
VIDEO_SOURCE_GENERATED = "generated"
VIDEO_SOURCE_COMMENT_IMPORT = "comment_import"

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


def _remove_orphan_video_dependencies(connection: sqlite3.Connection) -> dict:
    removed = {}
    for table in (
        "system_jobs",
        "audio_tasks",
        "audio_reviews",
        "video_publications",
    ):
        cursor = connection.execute(
            f"DELETE FROM {table} "
            "WHERE video_id IS NOT NULL "
            "AND NOT EXISTS ("
            f"SELECT 1 FROM videos WHERE videos.id = {table}.video_id"
            ")"
        )
        removed[table] = cursor.rowcount
    return removed


def remove_orphan_video_dependencies() -> dict:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        removed = _remove_orphan_video_dependencies(conn)
        conn.execute("COMMIT")
        return removed
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

def _backup_database_before_tts_v2() -> None:
    """Create one consistent backup immediately before the TTS v2 migration."""
    if not DB_PATH.exists():
        return
    source = sqlite3.connect(str(DB_PATH))
    try:
        existing_tables = {
            row[0]
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        required_columns = {
            "videos": "tts_provider_id",
            "audio_tasks": "tts_provider_id",
            "system_jobs": "tts_provider_id",
        }
        needs_migration = any(
            table in existing_tables
            and column
            not in {
                row[1]
                for row in source.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for table, column in required_columns.items()
        )
        if not needs_migration:
            return
        backup_path = DB_PATH.with_name(f"{DB_PATH.name}{TTS_V2_BACKUP_SUFFIX}")
        if backup_path.exists():
            return
        backup = sqlite3.connect(str(backup_path))
        try:
            source.backup(backup)
        finally:
            backup.close()
    finally:
        source.close()


def _publication_channel_requires_migration(conn: sqlite3.Connection) -> bool:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        ("video_publications",),
    ).fetchone()
    if table_exists is None:
        return False
    channel_column = next(
        (
            row
            for row in conn.execute(
                "PRAGMA table_info(video_publications)"
            ).fetchall()
            if row[1] == "youtube_channel_id"
        ),
        None,
    )
    return channel_column is not None and bool(channel_column[3])


def _backup_database_before_nullable_publication_channel() -> None:
    """Create one consistent backup immediately before rebuilding publications."""
    if not DB_PATH.exists():
        return
    source = sqlite3.connect(str(DB_PATH))
    try:
        if not _publication_channel_requires_migration(source):
            return
        backup_path = DB_PATH.with_name(
            f"{DB_PATH.name}{NULLABLE_PUBLICATION_CHANNEL_BACKUP_SUFFIX}"
        )
        if backup_path.exists():
            return
        backup = sqlite3.connect(str(backup_path))
        try:
            source.backup(backup)
        finally:
            backup.close()
    finally:
        source.close()


def _migrate_nullable_publication_channel() -> None:
    """Allow unverified publications while preserving IDs and child records."""
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        if not _publication_channel_requires_migration(conn):
            return
        foreign_keys_enabled = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DROP TABLE IF EXISTS video_publications_nullable_migration")
        conn.execute(
            '''
            CREATE TABLE video_publications_nullable_migration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                youtube_channel_id INTEGER,
                youtube_video_id TEXT NOT NULL UNIQUE,
                published_url TEXT NOT NULL,
                published_title TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
                FOREIGN KEY(youtube_channel_id) REFERENCES youtube_channels(id) ON DELETE CASCADE
            )
            '''
        )
        conn.execute(
            '''
            INSERT INTO video_publications_nullable_migration (
                id, video_id, youtube_channel_id, youtube_video_id,
                published_url, published_title, published_at,
                created_at, updated_at
            )
            SELECT id, video_id, youtube_channel_id, youtube_video_id,
                   published_url, published_title, published_at,
                   created_at, updated_at
            FROM video_publications
            '''
        )
        conn.execute("DROP TABLE video_publications")
        conn.execute(
            "ALTER TABLE video_publications_nullable_migration "
            "RENAME TO video_publications"
        )
        conn.execute(
            "CREATE INDEX idx_video_publications_video "
            "ON video_publications(video_id)"
        )
        conn.execute(
            "CREATE INDEX idx_video_publications_channel "
            "ON video_publications(youtube_channel_id)"
        )
        conn.execute("COMMIT")
        if foreign_keys_enabled:
            conn.execute("PRAGMA foreign_keys = ON")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _backup_database_before_tts_v2()
    _backup_database_before_nullable_publication_channel()
    _migrate_nullable_publication_channel()
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
            tts_provider_id TEXT DEFAULT 'genmax',
            voice_revision INTEGER DEFAULT 1,
            voice_snapshot_json TEXT DEFAULT '{}',
            audio_duration_seconds REAL,
            video_status TEXT NOT NULL DEFAULT 'active',
            source_type TEXT NOT NULL DEFAULT 'generated',
            description TEXT DEFAULT ''
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
    try:
        c.execute(
            "ALTER TABLE videos ADD COLUMN video_status "
            "TEXT NOT NULL DEFAULT 'active'"
        )
    except sqlite3.OperationalError:
        pass
    try:
        c.execute(
            "ALTER TABLE videos ADD COLUMN source_type "
            "TEXT NOT NULL DEFAULT 'generated'"
        )
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE videos ADD COLUMN description TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    c.execute(
        "UPDATE videos SET video_status = ? "
        "WHERE video_status IS NULL OR video_status NOT IN (?, ?)",
        (VIDEO_STATUS_ACTIVE, VIDEO_STATUS_ACTIVE, VIDEO_STATUS_ERROR),
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(video_status)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_videos_source_type ON videos(source_type)"
    )
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
            tts_provider_id TEXT DEFAULT 'genmax',
            voice_revision INTEGER DEFAULT 1,
            voice_snapshot_json TEXT DEFAULT '{}',
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
        CREATE TABLE IF NOT EXISTS tts_previews (
            id TEXT PRIMARY KEY,
            provider_task_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            text TEXT NOT NULL,
            character_count INTEGER NOT NULL,
            voice_id TEXT NOT NULL,
            voice_name TEXT NOT NULL,
            tts_provider_id TEXT NOT NULL,
            voice_revision INTEGER NOT NULL DEFAULT 1,
            voice_snapshot_json TEXT NOT NULL DEFAULT '{}',
            audio_filename TEXT DEFAULT '',
            duration_seconds REAL,
            error TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_tts_previews_expiry '
        'ON tts_previews(expires_at)'
    )
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
            voice_name TEXT DEFAULT '',
            tts_provider_id TEXT DEFAULT 'genmax',
            voice_revision INTEGER DEFAULT 1,
            voice_snapshot_json TEXT DEFAULT '{}',
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS youtube_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            thumbnail_url TEXT DEFAULT '',
            access_token_encrypted TEXT DEFAULT '',
            refresh_token_encrypted TEXT DEFAULT '',
            token_expiry TEXT DEFAULT '',
            scope TEXT DEFAULT '',
            oauth_client_id TEXT DEFAULT '',
            status TEXT DEFAULT 'connected',
            reply_instruction TEXT DEFAULT '',
            auto_mode TEXT DEFAULT 'draft_only',
            daily_reply_limit INTEGER DEFAULT 50,
            reply_interval_minutes INTEGER DEFAULT 5,
            quarter_hour_reply_limit INTEGER DEFAULT 3,
            hourly_reply_limit INTEGER DEFAULT 10,
            video_half_hour_reply_limit INTEGER DEFAULT 3,
            backlog_daily_reply_limit INTEGER DEFAULT 20,
            reply_window_start TEXT DEFAULT '08:00',
            reply_window_end TEXT DEFAULT '22:00',
            reply_paused INTEGER DEFAULT 0,
            auto_sync INTEGER DEFAULT 1,
            sync_interval_minutes INTEGER DEFAULT 10,
            last_sync_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS video_publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            youtube_channel_id INTEGER,
            youtube_video_id TEXT NOT NULL UNIQUE,
            published_url TEXT NOT NULL,
            published_title TEXT DEFAULT '',
            published_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE,
            FOREIGN KEY(youtube_channel_id) REFERENCES youtube_channels(id) ON DELETE CASCADE
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_video_publications_video '
        'ON video_publications(video_id)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_video_publications_channel '
        'ON video_publications(youtube_channel_id)'
    )
    c.execute('''
        CREATE TABLE IF NOT EXISTS youtube_comments (
            comment_id TEXT PRIMARY KEY,
            thread_id TEXT DEFAULT '',
            publication_id INTEGER NOT NULL,
            parent_id TEXT DEFAULT '',
            author_name TEXT DEFAULT '',
            author_channel_id TEXT DEFAULT '',
            author_avatar_url TEXT DEFAULT '',
            text TEXT NOT NULL,
            like_count INTEGER DEFAULT 0,
            published_at TEXT DEFAULT '',
            source_updated_at TEXT DEFAULT '',
            can_reply INTEGER DEFAULT 1,
            total_reply_count INTEGER DEFAULT 0,
            risk_level TEXT DEFAULT 'low',
            risk_reason TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            draft_reply TEXT DEFAULT '',
            reply_youtube_id TEXT DEFAULT '',
            reply_text TEXT DEFAULT '',
            reply_published_at TEXT DEFAULT '',
            auto_reply_priority INTEGER DEFAULT 0,
            auto_reply_reason TEXT DEFAULT '',
            error TEXT DEFAULT '',
            synced_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(publication_id) REFERENCES video_publications(id) ON DELETE CASCADE
        )
    ''')
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_youtube_comments_status '
        'ON youtube_comments(status, published_at)'
    )
    c.execute(
        'CREATE INDEX IF NOT EXISTS idx_youtube_comments_publication '
        'ON youtube_comments(publication_id)'
    )
    for column_definition in (
        "auto_sync INTEGER DEFAULT 1",
        "sync_interval_minutes INTEGER DEFAULT 10",
        "reply_interval_minutes INTEGER DEFAULT 5",
        "quarter_hour_reply_limit INTEGER DEFAULT 3",
        "hourly_reply_limit INTEGER DEFAULT 10",
        "video_half_hour_reply_limit INTEGER DEFAULT 3",
        "backlog_daily_reply_limit INTEGER DEFAULT 20",
        "reply_window_start TEXT DEFAULT '08:00'",
        "reply_window_end TEXT DEFAULT '22:00'",
        "reply_paused INTEGER DEFAULT 0",
        "oauth_client_id TEXT DEFAULT ''",
    ):
        try:
            c.execute(f"ALTER TABLE youtube_channels ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass
    for column_definition in (
        "risk_level TEXT DEFAULT 'low'",
        "risk_reason TEXT DEFAULT ''",
        "reply_published_at TEXT DEFAULT ''",
        "auto_reply_priority INTEGER DEFAULT 0",
        "auto_reply_reason TEXT DEFAULT ''",
    ):
        try:
            c.execute(f"ALTER TABLE youtube_comments ADD COLUMN {column_definition}")
        except sqlite3.OperationalError:
            pass
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
    for table_name in ("videos", "audio_tasks", "system_jobs"):
        for column_definition in (
            "tts_provider_id TEXT DEFAULT 'genmax'",
            "voice_revision INTEGER DEFAULT 1",
            "voice_snapshot_json TEXT DEFAULT '{}'",
        ):
            try:
                c.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_definition}"
                )
            except sqlite3.OperationalError:
                pass
    try:
        c.execute("ALTER TABLE system_jobs ADD COLUMN voice_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    c.execute(
        "UPDATE videos SET tts_provider_id = 'genmax' "
        "WHERE COALESCE(tts_provider_id, '') = ''"
    )
    c.execute(
        "UPDATE audio_tasks SET tts_provider_id = 'genmax' "
        "WHERE COALESCE(tts_provider_id, '') = ''"
    )
    c.execute(
        "UPDATE system_jobs SET tts_provider_id = 'genmax' "
        "WHERE COALESCE(tts_provider_id, '') = ''"
    )
    for table_name in ("videos", "audio_tasks", "system_jobs"):
        rows = c.execute(
            f"SELECT rowid, voice_id, voice_name, tts_provider_id, voice_revision "
            f"FROM {table_name} WHERE COALESCE(voice_id, '') != '' "
            "AND COALESCE(voice_snapshot_json, '') IN ('', '{}')"
        ).fetchall()
        snapshots = [
            (
                json.dumps(
                    {
                        "voice_id": voice_id,
                        "voice_name": voice_name or "",
                        "provider_id": provider_id or "genmax",
                        "provider_voice_id": voice_id,
                        "voice_revision": int(revision or 1),
                        "config": {},
                    },
                    ensure_ascii=False,
                ),
                row_id,
            )
            for row_id, voice_id, voice_name, provider_id, revision in rows
        ]
        c.executemany(
            f"UPDATE {table_name} SET voice_snapshot_json = ? WHERE rowid = ?",
            snapshots,
        )
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
    _remove_orphan_video_dependencies(conn)
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
    tts_provider_id: str = 'genmax',
    voice_revision: int = 1,
    voice_snapshot_json: str = '{}',
) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    generated_title = extract_generated_video_title(generated_script)
    c.execute('''
        INSERT INTO videos (
            url, title, transcript, generated_script, created_at, chat_url,
            prompt_version, generated_title, search_text, voice_id, voice_name,
            tts_provider_id, voice_revision, voice_snapshot_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        tts_provider_id,
        max(1, int(voice_revision or 1)),
        voice_snapshot_json or '{}',
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
    video_status: str = None,
) -> dict:
    if video_status is not None and video_status not in VIDEO_STATUSES:
        raise ValueError("Trạng thái video không hợp lệ.")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Videos imported only to manage comments must not pollute the generated
    # video library. They remain available through publications/comments.
    count_scope_filters = ["COALESCE(source_type, ?) != ?"]
    count_scope_params = [VIDEO_SOURCE_GENERATED, VIDEO_SOURCE_COMMENT_IMPORT]
    if prompt_version is not None:
        count_scope_filters.append('prompt_version = ?')
        count_scope_params.append(prompt_version)
    normalized_query = normalize_search_text(search_query or '')
    if normalized_query:
        count_scope_filters.append(
            "(search_text LIKE ? OR EXISTS ("
            "SELECT 1 FROM video_publications "
            "WHERE video_publications.video_id = videos.id "
            "AND LOWER(video_publications.published_url) LIKE ?))"
        )
        count_scope_params.extend(
            (f'%{normalized_query}%', f'%{normalized_query}%')
        )
    scope_filters = list(count_scope_filters)
    scope_params = list(count_scope_params)
    if video_status is not None:
        scope_filters.append('video_status = ?')
        scope_params.append(video_status)
    scope_clause = (
        f" AND {' AND '.join(scope_filters)}" if scope_filters else ''
    )

    # Counts follow the selected prompt version while remaining independent of
    # the publication-status filter.
    active_scope_clause = (
        f" AND {' AND '.join(count_scope_filters)}"
        if count_scope_filters else ''
    )
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE is_published = 1 '
        f'AND video_status = ?{active_scope_clause}',
        (VIDEO_STATUS_ACTIVE, *count_scope_params),
    )
    count_published = c.fetchone()[0]
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE is_published = 0 '
        f'AND video_status = ?{active_scope_clause}',
        (VIDEO_STATUS_ACTIVE, *count_scope_params),
    )
    count_unpublished = c.fetchone()[0]
    c.execute(
        f'SELECT COUNT(*) FROM videos WHERE video_status = ?{active_scope_clause}',
        (VIDEO_STATUS_ERROR, *count_scope_params),
    )
    count_error = c.fetchone()[0]

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
        'created_at, is_published, video_status, chat_url, '
        'prompt_version, voice_id, voice_name, tts_provider_id, '
        'audio_duration_seconds, '
        "COALESCE((SELECT status FROM audio_reviews WHERE video_id = videos.id), '') "
        'AS audio_review_status, '
        'SUBSTR(generated_script, 1, 300) as snippet, '
        "COALESCE((SELECT published_url FROM video_publications "
        "WHERE video_id = videos.id ORDER BY id DESC LIMIT 1), '') "
        'AS published_url, '
        "COALESCE((SELECT youtube_video_id FROM video_publications "
        "WHERE video_id = videos.id ORDER BY id DESC LIMIT 1), '') "
        'AS published_youtube_video_id, '
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
        "count_error": count_error,
        "items": [dict(row) for row in rows]
    }


def set_video_status(video_id: int, video_status: str) -> dict | None:
    if video_status not in VIDEO_STATUSES:
        raise ValueError("Trạng thái video không hợp lệ.")

    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        conn.execute(
            "UPDATE videos SET video_status = ? WHERE id = ?",
            (video_status, video_id),
        )
        if video_status == VIDEO_STATUS_ERROR:
            conn.execute(
                '''
                UPDATE system_jobs
                SET status = 'canceled', progress = ?, cancel_requested = 0,
                    finished_at = ?, updated_at = ?
                WHERE video_id = ?
                  AND status IN ('queued', 'retry_wait', 'paused')
                ''',
                ("Đã bỏ qua vì video được đánh dấu Lỗi", now, now, video_id),
            )
            conn.execute(
                '''
                UPDATE system_jobs
                SET progress = ?, cancel_requested = 1, updated_at = ?
                WHERE video_id = ? AND status = 'running'
                ''',
                (
                    "Video đã được đánh dấu Lỗi; sẽ dừng tại điểm an toàn",
                    now,
                    video_id,
                ),
            )
        updated = conn.execute(
            "SELECT * FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return dict(updated)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

def toggle_published(video_id: int, is_published: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    status_row = c.execute(
        "SELECT video_status FROM videos WHERE id = ?",
        (video_id,),
    ).fetchone()
    if status_row and status_row[0] == VIDEO_STATUS_ERROR:
        conn.close()
        raise ValueError(
            "Video đang ở trạng thái Lỗi. Hãy khôi phục trạng thái trước."
        )
    if not is_published:
        publication_count = c.execute(
            "SELECT COUNT(*) FROM video_publications WHERE video_id = ?",
            (video_id,),
        ).fetchone()[0]
        if publication_count:
            conn.close()
            raise ValueError(
                "Video đang có link đã đăng. Hãy xóa liên kết trong menu "
                "Bình luận YouTube để đồng bộ toàn hệ thống."
            )
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


def list_video_identity_candidates() -> list[dict]:
    """Return compact video identities used for exact YouTube-ID matching."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''
        SELECT id, url, title, chat_url, prompt_version, video_status,
               COALESCE(source_type, ?) AS source_type
        FROM videos
        ORDER BY id DESC
        ''',
        (VIDEO_SOURCE_GENERATED,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def reserve_comment_import_video(
    *,
    youtube_channel_id: int,
    youtube_video_id: str,
    published_url: str,
    title: str,
    description: str,
    published_at: str,
    prompt_version: str,
    existing_video_id: int | None = None,
) -> dict:
    """Atomically link or create one legacy video without overwriting context.

    The YouTube video ID is the durable deduplication key. Existing videos and
    Chat URLs always win; repeated bulk imports are therefore idempotent.
    """
    now = utc_now()
    normalized_youtube_id = str(youtube_video_id or "").strip()
    if not normalized_youtube_id:
        raise ValueError("Thiếu YouTube Video ID.")
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        channel = conn.execute(
            "SELECT id FROM youtube_channels WHERE id = ?",
            (youtube_channel_id,),
        ).fetchone()
        if channel is None:
            raise ValueError("Không tìm thấy kênh YouTube.")

        publication = conn.execute(
            "SELECT * FROM video_publications WHERE youtube_video_id = ?",
            (normalized_youtube_id,),
        ).fetchone()
        created_video = False
        created_publication = False
        assigned_channel = False
        if publication is not None:
            publication_channel_id = publication["youtube_channel_id"]
            if (
                publication_channel_id is not None
                and int(publication_channel_id) != int(youtube_channel_id)
            ):
                raise ValueError("Video này đã được liên kết với một kênh khác.")
            if publication_channel_id is None:
                cursor = conn.execute(
                    '''
                    UPDATE video_publications
                    SET youtube_channel_id = ?, published_url = ?,
                        published_title = ?, published_at = ?, updated_at = ?
                    WHERE id = ? AND youtube_channel_id IS NULL
                    ''',
                    (
                        youtube_channel_id,
                        published_url,
                        title,
                        published_at,
                        now,
                        publication["id"],
                    ),
                )
                assigned_channel = cursor.rowcount > 0
            video_id = int(publication["video_id"])
        elif existing_video_id is not None:
            video = conn.execute(
                "SELECT id, video_status FROM videos WHERE id = ?",
                (existing_video_id,),
            ).fetchone()
            if video is None:
                raise ValueError("Không tìm thấy video nội bộ cần liên kết.")
            if video["video_status"] == VIDEO_STATUS_ERROR:
                raise ValueError("Video nội bộ đang ở trạng thái Lỗi.")
            video_id = int(video["id"])
        else:
            search_text = normalize_search_text(
                " ".join((published_url, title, description))
            )
            cursor = conn.execute(
                '''
                INSERT INTO videos (
                    url, title, transcript, generated_script, created_at,
                    is_published, chat_url, prompt_version, generated_title,
                    search_text, voice_id, voice_name, video_status,
                    source_type, description
                ) VALUES (?, ?, '', '', ?, 1, '', ?, ?, ?, '', '', ?, ?, ?)
                ''',
                (
                    published_url,
                    title,
                    now,
                    prompt_version,
                    title,
                    search_text,
                    VIDEO_STATUS_ACTIVE,
                    VIDEO_SOURCE_COMMENT_IMPORT,
                    description,
                ),
            )
            video_id = int(cursor.lastrowid)
            created_video = True

        if publication is None:
            conn.execute(
                '''
                INSERT INTO video_publications (
                    video_id, youtube_channel_id, youtube_video_id,
                    published_url, published_title, published_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    video_id,
                    youtube_channel_id,
                    normalized_youtube_id,
                    published_url,
                    title,
                    published_at,
                    now,
                    now,
                ),
            )
            created_publication = True
        conn.execute(
            '''
            UPDATE videos
            SET is_published = 1,
                prompt_version = CASE
                    WHEN TRIM(COALESCE(prompt_version, '')) = '' THEN ?
                    ELSE prompt_version
                END,
                description = CASE
                    WHEN TRIM(COALESCE(description, '')) = '' THEN ?
                    ELSE description
                END
            WHERE id = ?
            ''',
            (prompt_version, description, video_id),
        )
        video = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        publication = conn.execute(
            "SELECT * FROM video_publications WHERE youtube_video_id = ?",
            (normalized_youtube_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return {
            "video": dict(video),
            "publication": dict(publication),
            "created_video": created_video,
            "created_publication": created_publication,
            "assigned_channel": assigned_channel,
        }
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def update_comment_import_context(
    video_id: int,
    *,
    transcript: str | None = None,
    description: str | None = None,
) -> dict | None:
    """Fill imported context fields without replacing non-empty saved data."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    assignments = []
    params: list = []
    if transcript is not None:
        assignments.append(
            "transcript = CASE WHEN TRIM(COALESCE(transcript, '')) = '' "
            "THEN ? ELSE transcript END"
        )
        params.append(transcript)
    if description is not None:
        assignments.append(
            "description = CASE WHEN TRIM(COALESCE(description, '')) = '' "
            "THEN ? ELSE description END"
        )
        params.append(description)
    if assignments:
        conn.execute(
            f"UPDATE videos SET {', '.join(assignments)} WHERE id = ?",
            (*params, video_id),
        )
        conn.commit()
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_video_chat_url_if_empty(video_id: int, chat_url: str) -> dict | None:
    """Persist a newly created Chat exactly once and never overwrite one."""
    normalized_url = str(chat_url or "").strip()
    if not normalized_url:
        raise ValueError("Chat URL không được để trống.")
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            '''
            UPDATE videos
            SET chat_url = ?
            WHERE id = ? AND TRIM(COALESCE(chat_url, '')) = ''
            ''',
            (normalized_url, video_id),
        )
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        conn.execute("COMMIT")
        return dict(row) if row else None
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

def delete_video_with_dependencies(video_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        video = conn.execute(
            "SELECT * FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if video is None:
            conn.execute("ROLLBACK")
            return None

        active_job = conn.execute(
            '''
            SELECT id FROM system_jobs
            WHERE video_id = ?
              AND status IN ('queued', 'running', 'retry_wait', 'paused')
            LIMIT 1
            ''',
            (video_id,),
        ).fetchone()
        if active_job is not None:
            raise ValueError(
                "Job tạo video đang chạy hoặc chờ xử lý. "
                "Hãy dừng job trước khi xóa."
            )

        active_audio = conn.execute(
            '''
            SELECT task_id FROM audio_tasks
            WHERE video_id = ? AND status IN ('pending', 'processing')
            LIMIT 1
            ''',
            (video_id,),
        ).fetchone()
        if active_audio is not None:
            raise ValueError(
                "Video đang có job audio chạy hoặc chờ Genmax. "
                "Hãy đợi job kết thúc trước khi xóa."
            )

        system_jobs = conn.execute(
            "SELECT * FROM system_jobs WHERE video_id = ? ORDER BY created_at",
            (video_id,),
        ).fetchall()
        audio_task = conn.execute(
            "SELECT * FROM audio_tasks WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        audio_review = conn.execute(
            "SELECT * FROM audio_reviews WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        publications = conn.execute(
            "SELECT * FROM video_publications WHERE video_id = ? ORDER BY id",
            (video_id,),
        ).fetchall()

        conn.execute(
            "DELETE FROM youtube_comments WHERE publication_id IN "
            "(SELECT id FROM video_publications WHERE video_id = ?)",
            (video_id,),
        )
        conn.execute("DELETE FROM video_publications WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM system_jobs WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM audio_reviews WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM audio_tasks WHERE video_id = ?", (video_id,))
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    decoded_jobs = [_decode_system_job(row) for row in system_jobs]
    return {
        "video": dict(video),
        "system_jobs": decoded_jobs,
        "system_job_ids": [job["id"] for job in decoded_jobs],
        "audio_task": dict(audio_task) if audio_task else None,
        "audio_review": dict(audio_review) if audio_review else None,
        "video_publications": [dict(row) for row in publications],
    }


def delete_video(video_id: int) -> bool:
    return delete_video_with_dependencies(video_id) is not None


def save_youtube_channel(
    *,
    channel_id: str,
    title: str,
    thumbnail_url: str = "",
    access_token_encrypted: str = "",
    refresh_token_encrypted: str = "",
    token_expiry: str = "",
    scope: str = "",
    oauth_client_id: str = "",
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        '''
        INSERT INTO youtube_channels (
            channel_id, title, thumbnail_url, access_token_encrypted,
            refresh_token_encrypted, token_expiry, scope, oauth_client_id, status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'connected', ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            title = excluded.title,
            thumbnail_url = excluded.thumbnail_url,
            access_token_encrypted = excluded.access_token_encrypted,
            refresh_token_encrypted = CASE
                WHEN excluded.refresh_token_encrypted = ''
                THEN youtube_channels.refresh_token_encrypted
                ELSE excluded.refresh_token_encrypted
            END,
            token_expiry = excluded.token_expiry,
            scope = excluded.scope,
            oauth_client_id = CASE
                WHEN excluded.refresh_token_encrypted = ''
                    AND youtube_channels.refresh_token_encrypted != ''
                THEN youtube_channels.oauth_client_id
                ELSE excluded.oauth_client_id
            END,
            status = 'connected',
            updated_at = excluded.updated_at
        ''',
        (
            channel_id,
            title,
            thumbnail_url,
            access_token_encrypted,
            refresh_token_encrypted,
            token_expiry,
            scope,
            str(oauth_client_id or "").strip(),
            now,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM youtube_channels WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    conn.close()
    return _public_youtube_channel(row)


def _public_youtube_channel(row: sqlite3.Row | dict | None) -> dict | None:
    if row is None:
        return None
    channel = dict(row)
    channel["has_refresh_token"] = bool(channel.get("refresh_token_encrypted"))
    channel.pop("access_token_encrypted", None)
    channel.pop("refresh_token_encrypted", None)
    return channel


def get_youtube_channel(channel_db_id: int, *, include_tokens: bool = False) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM youtube_channels WHERE id = ?", (channel_db_id,)
    ).fetchone()
    conn.close()
    return dict(row) if include_tokens and row else _public_youtube_channel(row)


def get_youtube_channel_by_channel_id(
    channel_id: str,
    *,
    include_tokens: bool = False,
) -> dict | None:
    """Resolve a connected channel by its stable YouTube channel identifier."""
    normalized_channel_id = str(channel_id or "").strip()
    if not normalized_channel_id:
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM youtube_channels WHERE channel_id = ?",
        (normalized_channel_id,),
    ).fetchone()
    conn.close()
    return dict(row) if include_tokens and row else _public_youtube_channel(row)


def list_youtube_channels() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM youtube_channels ORDER BY title COLLATE NOCASE"
    ).fetchall()
    conn.close()
    return [_public_youtube_channel(row) for row in rows]


def update_youtube_channel(channel_db_id: int, **changes) -> dict | None:
    allowed = {
        "title",
        "thumbnail_url",
        "access_token_encrypted",
        "refresh_token_encrypted",
        "token_expiry",
        "scope",
        "oauth_client_id",
        "status",
        "reply_instruction",
        "auto_mode",
        "daily_reply_limit",
        "reply_interval_minutes",
        "quarter_hour_reply_limit",
        "hourly_reply_limit",
        "video_half_hour_reply_limit",
        "backlog_daily_reply_limit",
        "reply_window_start",
        "reply_window_end",
        "reply_paused",
        "auto_sync",
        "sync_interval_minutes",
        "last_sync_at",
    }
    invalid = set(changes) - allowed
    if invalid:
        raise ValueError(f"Unsupported YouTube channel fields: {sorted(invalid)}")
    if not changes:
        return get_youtube_channel(channel_db_id)
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute(
        f"UPDATE youtube_channels SET {assignments} WHERE id = ?",
        (*changes.values(), channel_db_id),
    )
    conn.commit()
    conn.close()
    return get_youtube_channel(channel_db_id)


def delete_youtube_channel(channel_db_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        publication_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM video_publications WHERE youtube_channel_id = ?",
                (channel_db_id,),
            ).fetchall()
        ]
        if publication_ids:
            placeholders = ",".join("?" for _ in publication_ids)
            conn.execute(
                f"DELETE FROM youtube_comments WHERE publication_id IN ({placeholders})",
                publication_ids,
            )
        video_ids = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT video_id FROM video_publications WHERE youtube_channel_id = ?",
                (channel_db_id,),
            ).fetchall()
        ]
        conn.execute(
            "DELETE FROM video_publications WHERE youtube_channel_id = ?",
            (channel_db_id,),
        )
        cursor = conn.execute("DELETE FROM youtube_channels WHERE id = ?", (channel_db_id,))
        for video_id in video_ids:
            remaining = conn.execute(
                "SELECT COUNT(*) FROM video_publications WHERE video_id = ?", (video_id,)
            ).fetchone()[0]
            if remaining == 0:
                conn.execute("UPDATE videos SET is_published = 0 WHERE id = ?", (video_id,))
        conn.execute("COMMIT")
        return cursor.rowcount > 0
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def save_video_publication(
    *,
    video_id: int,
    youtube_channel_id: int | None,
    youtube_video_id: str,
    published_url: str,
    published_title: str = "",
    published_at: str = "",
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        video = conn.execute(
            "SELECT video_status FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if video is None:
            raise ValueError("Không tìm thấy video nội bộ.")
        if video[0] == VIDEO_STATUS_ERROR:
            raise ValueError(
                "Video đang ở trạng thái Lỗi. Hãy khôi phục trạng thái trước."
            )
        if youtube_channel_id is not None:
            if conn.execute(
                "SELECT 1 FROM youtube_channels WHERE id = ?", (youtube_channel_id,)
            ).fetchone() is None:
                raise ValueError("Không tìm thấy kênh YouTube.")
        existing = conn.execute(
            "SELECT video_id, youtube_channel_id FROM video_publications "
            "WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        if existing:
            existing_channel_id = existing[1]
            changes_video = int(existing[0]) != int(video_id)
            changes_assigned_channel = existing_channel_id is not None and (
                youtube_channel_id is None
                or int(existing_channel_id) != int(youtube_channel_id)
            )
            if changes_video or changes_assigned_channel:
                raise ValueError(
                    "Link video đã đăng này đã được gắn với video hoặc kênh khác."
                )
        conn.execute(
            '''
            INSERT INTO video_publications (
                video_id, youtube_channel_id, youtube_video_id, published_url,
                published_title, published_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(youtube_video_id) DO UPDATE SET
                video_id = excluded.video_id,
                youtube_channel_id = excluded.youtube_channel_id,
                published_url = excluded.published_url,
                published_title = excluded.published_title,
                published_at = excluded.published_at,
                updated_at = excluded.updated_at
            ''',
            (
                video_id,
                youtube_channel_id,
                youtube_video_id,
                published_url,
                published_title,
                published_at,
                now,
                now,
            ),
        )
        conn.execute("UPDATE videos SET is_published = 1 WHERE id = ?", (video_id,))
        row = conn.execute(
            "SELECT * FROM video_publications WHERE youtube_video_id = ?",
            (youtube_video_id,),
        ).fetchone()
        conn.execute("COMMIT")
        return dict(row)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def list_video_publications(video_id: int | None = None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    filters = ["videos.video_status = ?"]
    params: list = [VIDEO_STATUS_ACTIVE]
    if video_id is not None:
        filters.append("publications.video_id = ?")
        params.append(video_id)
    where = f"WHERE {' AND '.join(filters)}"
    rows = conn.execute(
        f'''
        SELECT publications.*, channels.channel_id, channels.title AS channel_title,
               videos.chat_url, videos.prompt_version, videos.video_status,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS video_title
        FROM video_publications AS publications
        LEFT JOIN youtube_channels AS channels ON channels.id = publications.youtube_channel_id
        JOIN videos ON videos.id = publications.video_id
        {where}
        ORDER BY publications.created_at DESC
        ''',
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_video_publication_identities() -> list[dict]:
    """Return all publication IDs, including videos marked as error."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''
        SELECT publications.id, publications.video_id,
               publications.youtube_channel_id,
               publications.youtube_video_id,
               publications.published_url,
               videos.chat_url, videos.prompt_version, videos.video_status,
               COALESCE(videos.source_type, ?) AS source_type
        FROM video_publications AS publications
        JOIN videos ON videos.id = publications.video_id
        ORDER BY publications.id DESC
        ''',
        (VIDEO_SOURCE_GENERATED,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_video_publication_by_youtube_id(youtube_video_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        '''
        SELECT publications.*, videos.chat_url, videos.prompt_version,
               videos.video_status,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS video_title
        FROM video_publications AS publications
        JOIN videos ON videos.id = publications.video_id
        WHERE publications.youtube_video_id = ?
        ''',
        (youtube_video_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_video_publication(publication_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT video_id FROM video_publications WHERE id = ?", (publication_id,)
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return False
        conn.execute("DELETE FROM youtube_comments WHERE publication_id = ?", (publication_id,))
        conn.execute("DELETE FROM video_publications WHERE id = ?", (publication_id,))
        remaining = conn.execute(
            "SELECT COUNT(*) FROM video_publications WHERE video_id = ?", (row[0],)
        ).fetchone()[0]
        if remaining == 0:
            conn.execute("UPDATE videos SET is_published = 0 WHERE id = ?", (row[0],))
        conn.execute("COMMIT")
        return True
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def upsert_youtube_comment(publication_id: int, comment: dict) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        '''
        INSERT INTO youtube_comments (
            comment_id, thread_id, publication_id, parent_id, author_name,
            author_channel_id, author_avatar_url, text, like_count,
            published_at, source_updated_at, can_reply, total_reply_count,
            risk_level, risk_reason, auto_reply_priority, auto_reply_reason,
            status, reply_youtube_id, reply_text, reply_published_at,
            synced_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(comment_id) DO UPDATE SET
            thread_id = excluded.thread_id,
            publication_id = excluded.publication_id,
            author_name = excluded.author_name,
            author_channel_id = excluded.author_channel_id,
            author_avatar_url = excluded.author_avatar_url,
            text = excluded.text,
            like_count = excluded.like_count,
            published_at = excluded.published_at,
            source_updated_at = excluded.source_updated_at,
            can_reply = excluded.can_reply,
            total_reply_count = excluded.total_reply_count,
            risk_level = CASE
                WHEN youtube_comments.risk_level = 'reviewed' THEN 'reviewed'
                ELSE excluded.risk_level
            END,
            risk_reason = CASE
                WHEN youtube_comments.risk_level = 'reviewed' THEN youtube_comments.risk_reason
                ELSE excluded.risk_reason
            END,
            auto_reply_priority = excluded.auto_reply_priority,
            auto_reply_reason = excluded.auto_reply_reason,
            status = CASE
                WHEN ? != '' THEN 'replied'
                ELSE youtube_comments.status
            END,
            reply_youtube_id = CASE
                WHEN ? != '' THEN ?
                ELSE youtube_comments.reply_youtube_id
            END,
            reply_text = CASE
                WHEN ? != '' THEN ?
                ELSE youtube_comments.reply_text
            END,
            reply_published_at = CASE
                WHEN ? != '' THEN ?
                ELSE youtube_comments.reply_published_at
            END,
            error = CASE WHEN ? != '' THEN '' ELSE youtube_comments.error END,
            synced_at = excluded.synced_at,
            updated_at = excluded.updated_at
        ''',
        (
            comment["comment_id"],
            comment.get("thread_id", ""),
            publication_id,
            comment.get("parent_id", ""),
            comment.get("author_name", ""),
            comment.get("author_channel_id", ""),
            comment.get("author_avatar_url", ""),
            comment.get("text", ""),
            int(comment.get("like_count") or 0),
            comment.get("published_at", ""),
            comment.get("updated_at", ""),
            int(bool(comment.get("can_reply", True))),
            int(comment.get("total_reply_count") or 0),
            comment.get("risk_level", "low"),
            comment.get("risk_reason", ""),
            int(comment.get("auto_reply_priority") or 0),
            comment.get("auto_reply_reason", ""),
            "replied" if comment.get("existing_reply_id") else "new",
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_text", ""),
            comment.get("existing_reply_published_at", ""),
            now,
            now,
            now,
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_text", ""),
            comment.get("existing_reply_id", ""),
            comment.get("existing_reply_published_at", ""),
            comment.get("existing_reply_id", ""),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM youtube_comments WHERE comment_id = ?", (comment["comment_id"],)
    ).fetchone()
    conn.close()
    return dict(row)


def list_youtube_comments(
    *,
    channel_db_id: int | None = None,
    video_id: int | None = None,
    status: str | None = None,
    search_query: str = "",
    limit: int = 200,
) -> list[dict]:
    filters = ["videos.video_status = ?"]
    params: list = [VIDEO_STATUS_ACTIVE]
    if channel_db_id is not None:
        filters.append("publications.youtube_channel_id = ?")
        params.append(channel_db_id)
    if video_id is not None:
        filters.append("publications.video_id = ?")
        params.append(video_id)
    if status:
        filters.append("comments.status = ?")
        params.append(status)
    normalized_query = normalize_search_text(search_query)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    requested_limit = max(1, min(int(limit), 500))
    params.append(500 if normalized_query else requested_limit)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f'''
        SELECT comments.*, publications.video_id, publications.youtube_channel_id,
               publications.youtube_video_id, publications.published_url,
               channels.title AS channel_title, videos.chat_url,
               videos.prompt_version, videos.video_status,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS video_title
        FROM youtube_comments AS comments
        JOIN video_publications AS publications ON publications.id = comments.publication_id
        JOIN youtube_channels AS channels ON channels.id = publications.youtube_channel_id
        JOIN videos ON videos.id = publications.video_id
        {where}
        ORDER BY comments.published_at DESC, comments.created_at DESC
        LIMIT ?
        ''',
        params,
    ).fetchall()
    conn.close()
    items = [dict(row) for row in rows]
    if normalized_query:
        items = [
            item
            for item in items
            if normalized_query
            in normalize_search_text(
                " ".join(
                    str(item.get(field) or "")
                    for field in (
                        "text",
                        "author_name",
                        "video_title",
                        "published_url",
                    )
                )
            )
        ]
    return items[:requested_limit]


def get_youtube_comments(comment_ids: list[str]) -> list[dict]:
    if not comment_ids:
        return []
    placeholders = ",".join("?" for _ in comment_ids)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f'''
        SELECT comments.*, publications.video_id, publications.youtube_channel_id,
               publications.youtube_video_id, publications.published_url,
               channels.title AS channel_title, channels.reply_instruction,
               channels.auto_mode, channels.daily_reply_limit,
               channels.reply_interval_minutes,
               channels.quarter_hour_reply_limit, channels.hourly_reply_limit,
               channels.video_half_hour_reply_limit,
               channels.backlog_daily_reply_limit,
               channels.reply_window_start, channels.reply_window_end,
               channels.reply_paused,
               videos.chat_url, videos.prompt_version,
               videos.video_status,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS video_title
        FROM youtube_comments AS comments
        JOIN video_publications AS publications ON publications.id = comments.publication_id
        JOIN youtube_channels AS channels ON channels.id = publications.youtube_channel_id
        JOIN videos ON videos.id = publications.video_id
        WHERE comments.comment_id IN ({placeholders})
          AND videos.video_status = ?
        ''',
        (*comment_ids, VIDEO_STATUS_ACTIVE),
    ).fetchall()
    conn.close()
    rows_by_id = {row["comment_id"]: dict(row) for row in rows}
    return [rows_by_id[comment_id] for comment_id in comment_ids if comment_id in rows_by_id]


def update_youtube_comment(comment_id: str, **changes) -> dict | None:
    allowed = {
        "status",
        "draft_reply",
        "reply_youtube_id",
        "reply_text",
        "error",
        "risk_level",
        "risk_reason",
        "reply_published_at",
        "auto_reply_priority",
        "auto_reply_reason",
    }
    invalid = set(changes) - allowed
    if invalid:
        raise ValueError(f"Unsupported YouTube comment fields: {sorted(invalid)}")
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"UPDATE youtube_comments SET {assignments} WHERE comment_id = ?",
        (*changes.values(), comment_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM youtube_comments WHERE comment_id = ?", (comment_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def count_channel_replies_since(channel_db_id: int, since_iso: str) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        '''
        SELECT COUNT(*)
        FROM youtube_comments AS comments
        JOIN video_publications AS publications
          ON publications.id = comments.publication_id
        WHERE publications.youtube_channel_id = ?
          AND comments.status = 'replied'
          AND comments.updated_at >= ?
        ''',
        (channel_db_id, since_iso),
    ).fetchone()
    conn.close()
    return int(row[0] if row else 0)


def list_channel_reply_activity(channel_db_id: int, since_iso: str) -> list[dict]:
    """Return actual YouTube reply activity used by the publish scheduler."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        '''
        SELECT publications.video_id, comments.published_at AS comment_published_at,
               COALESCE(NULLIF(comments.reply_published_at, ''), comments.updated_at)
                   AS replied_at
        FROM youtube_comments AS comments
        JOIN video_publications AS publications
          ON publications.id = comments.publication_id
        WHERE publications.youtube_channel_id = ?
          AND comments.status = 'replied'
          AND COALESCE(NULLIF(comments.reply_published_at, ''), comments.updated_at) >= ?
        ORDER BY replied_at DESC
        ''',
        (channel_db_id, since_iso),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_recent_channel_reply_texts(channel_db_id: int, limit: int = 20) -> list[str]:
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        '''
        SELECT comments.reply_text
        FROM youtube_comments AS comments
        JOIN video_publications AS publications
          ON publications.id = comments.publication_id
        WHERE publications.youtube_channel_id = ?
          AND comments.status = 'replied'
          AND TRIM(comments.reply_text) != ''
        ORDER BY COALESCE(NULLIF(comments.reply_published_at, ''), comments.updated_at) DESC
        LIMIT ?
        ''',
        (channel_db_id, max(1, min(int(limit), 50))),
    ).fetchall()
    conn.close()
    return [str(row[0]) for row in rows]


def is_managed_media_filename_referenced(filename: str) -> bool:
    normalized_filename = Path(filename).name
    if not normalized_filename or normalized_filename != filename:
        return False
    pattern = f"%{normalized_filename}%"
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(
            '''
            SELECT EXISTS (
                SELECT 1 FROM videos WHERE generated_script LIKE ?
                UNION ALL
                SELECT 1 FROM audio_tasks
                WHERE audio_url LIKE ? OR segments_json LIKE ?
                UNION ALL
                SELECT 1 FROM system_jobs
                WHERE payload_json LIKE ? OR result_json LIKE ?
            )
            ''',
            (pattern, pattern, pattern, pattern, pattern),
        ).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()

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


def update_video_voice(
    video_id: int,
    voice_id: str,
    voice_name: str,
    tts_provider_id: str = "genmax",
    voice_revision: int = 1,
    voice_snapshot_json: str = "{}",
) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        UPDATE videos
        SET voice_id = ?, voice_name = ?, tts_provider_id = ?,
            voice_revision = ?, voice_snapshot_json = ?
        WHERE id = ?
        ''',
        (
            voice_id,
            voice_name,
            tts_provider_id,
            max(1, int(voice_revision or 1)),
            voice_snapshot_json or "{}",
            video_id,
        ),
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


def list_audio_reviews(limit: int | None = 100) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    limit_clause = " LIMIT ?" if limit is not None else ""
    params: list = [VIDEO_STATUS_ACTIVE]
    if limit is not None:
        params.append(max(1, min(int(limit), 500)))
    c.execute(
        f'''
        SELECT audio_reviews.*,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS title,
               videos.title AS original_title,
               videos.generated_title AS generated_title,
               videos.url AS video_url
        FROM audio_reviews
        JOIN videos ON videos.id = audio_reviews.video_id
        WHERE videos.video_status = ?
        ORDER BY audio_reviews.updated_at DESC
        {limit_clause}
        ''',
        params,
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
        SELECT audio_tasks.* FROM audio_tasks
        JOIN videos ON videos.id = audio_tasks.video_id
        WHERE audio_tasks.request_hash = ? AND videos.video_status = ?
        ORDER BY
            CASE audio_tasks.status
                WHEN 'completed' THEN 0
                WHEN 'processing' THEN 1
                WHEN 'pending' THEN 2
                WHEN 'failed' THEN 3
                ELSE 4
            END,
            created_at ASC
        LIMIT 1
        ''',
        (request_hash, VIDEO_STATUS_ACTIVE),
    )
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_active_audio_tasks() -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        '''
        SELECT audio_tasks.*
        FROM audio_tasks
        JOIN videos ON videos.id = audio_tasks.video_id
        WHERE audio_tasks.status IN ('pending', 'processing')
          AND videos.video_status = ?
        ''',
        (VIDEO_STATUS_ACTIVE,),
    )
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_audio_tasks(limit: int | None = 100) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    limit_clause = " LIMIT ?" if limit is not None else ""
    params: list = [VIDEO_STATUS_ACTIVE]
    if limit is not None:
        params.append(max(1, min(int(limit), 500)))
    c.execute(
        f'''
        SELECT audio_tasks.*,
               COALESCE(NULLIF(videos.generated_title, ''), videos.title) AS title,
               videos.title AS original_title,
               videos.generated_title AS generated_title,
               videos.url AS video_url
        FROM audio_tasks
        JOIN videos ON videos.id = audio_tasks.video_id
        WHERE videos.video_status = ?
        ORDER BY audio_tasks.updated_at DESC
        {limit_clause}
        ''',
        params,
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
    tts_provider_id: str = 'genmax',
    voice_revision: int = 1,
    voice_snapshot_json: str = '{}',
) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO audio_tasks (
            video_id, request_hash, task_id, status, audio_url, error,
            segments_json, voice_id, voice_name, tts_provider_id,
            voice_revision, voice_snapshot_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            tts_provider_id = CASE
                WHEN excluded.tts_provider_id != '' THEN excluded.tts_provider_id
                ELSE audio_tasks.tts_provider_id
            END,
            voice_revision = excluded.voice_revision,
            voice_snapshot_json = CASE
                WHEN excluded.voice_snapshot_json NOT IN ('', '{}')
                THEN excluded.voice_snapshot_json
                ELSE audio_tasks.voice_snapshot_json
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
            tts_provider_id,
            max(1, int(voice_revision or 1)),
            voice_snapshot_json or '{}',
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()
    return get_audio_task(video_id)


TTS_PREVIEW_MUTABLE_FIELDS = {
    "status",
    "audio_filename",
    "duration_seconds",
    "error",
}


def _decode_tts_preview(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    preview = dict(row)
    try:
        preview["voice_snapshot"] = json.loads(
            preview.get("voice_snapshot_json") or "{}"
        )
    except (TypeError, json.JSONDecodeError):
        preview["voice_snapshot"] = {}
    preview.pop("voice_snapshot_json", None)
    return preview


def create_tts_preview(
    *,
    preview_id: str,
    provider_task_id: str,
    request_hash: str,
    status: str,
    text: str,
    voice_id: str,
    voice_name: str,
    tts_provider_id: str,
    voice_revision: int,
    voice_snapshot_json: str,
    expires_at: str,
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute(
        '''
        INSERT INTO tts_previews (
            id, provider_task_id, request_hash, status, text,
            character_count, voice_id, voice_name, tts_provider_id,
            voice_revision, voice_snapshot_json, created_at, updated_at,
            expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            preview_id,
            provider_task_id,
            request_hash,
            status,
            text,
            len(text),
            voice_id,
            voice_name,
            tts_provider_id,
            max(1, int(voice_revision or 1)),
            voice_snapshot_json or "{}",
            now,
            now,
            expires_at,
        ),
    )
    conn.commit()
    conn.close()
    return get_tts_preview(preview_id)


def get_tts_preview(preview_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM tts_previews WHERE id = ?",
        (preview_id,),
    ).fetchone()
    conn.close()
    return _decode_tts_preview(row)


def list_active_tts_previews(provider_id: str = "") -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    filters = ["status IN ('queued', 'processing')", "expires_at > ?"]
    params: list = [utc_now()]
    if provider_id:
        filters.append("tts_provider_id = ?")
        params.append(provider_id)
    rows = conn.execute(
        f"SELECT * FROM tts_previews WHERE {' AND '.join(filters)} "
        "ORDER BY created_at ASC",
        params,
    ).fetchall()
    conn.close()
    return [_decode_tts_preview(row) for row in rows]


def update_tts_preview(preview_id: str, **changes) -> dict | None:
    invalid_fields = set(changes) - TTS_PREVIEW_MUTABLE_FIELDS
    if invalid_fields:
        raise ValueError(f"Unsupported TTS preview fields: {sorted(invalid_fields)}")
    if not changes:
        return get_tts_preview(preview_id)
    changes["updated_at"] = utc_now()
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute(
        f"UPDATE tts_previews SET {assignments} WHERE id = ?",
        (*changes.values(), preview_id),
    )
    conn.commit()
    conn.close()
    return get_tts_preview(preview_id)


def delete_expired_tts_previews(now: str | None = None) -> list[dict]:
    cutoff = now or utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM tts_previews WHERE expires_at <= ?",
            (cutoff,),
        ).fetchall()
        conn.execute(
            "DELETE FROM tts_previews WHERE expires_at <= ?",
            (cutoff,),
        )
        conn.execute("COMMIT")
        return [_decode_tts_preview(row) for row in rows]
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


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
    "voice_name",
    "tts_provider_id",
    "voice_revision",
    "voice_snapshot_json",
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
    voice_name: str = "",
    tts_provider_id: str = "genmax",
    voice_revision: int = 1,
    voice_snapshot_json: str = "{}",
) -> dict:
    now = utc_now()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    c = conn.cursor()
    c.execute(
        '''
        INSERT INTO system_jobs (
            id, job_type, status, title, progress, payload_json,
            result_json, error, prompt_version, voice_id, voice_name,
            tts_provider_id, voice_revision, voice_snapshot_json,
            created_at, updated_at
        ) VALUES (?, ?, 'queued', ?, ?, ?, '{}', '', ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            job_id,
            job_type,
            title,
            "Đang chờ trong hàng đợi",
            json.dumps(payload, ensure_ascii=False),
            prompt_version,
            voice_id,
            voice_name,
            tts_provider_id,
            max(1, int(voice_revision or 1)),
            voice_snapshot_json or "{}",
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
    limit: int | None = 100,
    job_type: str | None = None,
) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    filters = [
        "(system_jobs.video_id IS NULL OR videos.video_status = ?)"
    ]
    params: list = [VIDEO_STATUS_ACTIVE]
    if job_type:
        filters.append("system_jobs.job_type = ?")
        params.append(job_type)
    where_clause = f" WHERE {' AND '.join(filters)}"
    limit_clause = " LIMIT ?" if limit is not None else ""
    if limit is not None:
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
        {limit_clause}
        ''',
        params,
    )
    rows = c.fetchall()
    conn.close()
    return [_decode_system_job(row) for row in rows]


def list_active_system_jobs(job_type: str | None = None) -> list[dict]:
    """Return every active job in stable ownership order without a UI limit."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    filters = ["status IN ('queued', 'running', 'retry_wait', 'paused')"]
    params: list = []
    if job_type:
        filters.append("job_type = ?")
        params.append(job_type)
    rows = conn.execute(
        f'''
        SELECT * FROM system_jobs
        WHERE {' AND '.join(filters)}
        ORDER BY created_at ASC, id ASC
        ''',
        params,
    ).fetchall()
    conn.close()
    return [_decode_system_job(row) for row in rows]


def pause_queued_attention_jobs() -> int:
    """Keep legacy verification jobs paused until the user explicitly resumes.

    Older resume actions left the ``attention_required`` marker attached while
    changing the status back to queued. On restart that could immediately hit
    ChatGPT again. New resume actions clear the marker atomically below.
    """
    candidates = list_active_system_jobs()
    paused = 0
    for job in candidates:
        if job.get("status") not in {"queued", "retry_wait"}:
            continue
        if not (job.get("result") or {}).get("attention_required"):
            continue
        update_system_job(
            job["id"],
            status="paused",
            progress="Cần xác minh phiên ChatGPT trước khi tiếp tục",
            next_retry_at="",
            started_at="",
        )
        paused += 1
    return paused


def has_active_system_job_for_video(job_type: str, video_id: int) -> bool:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        '''
        SELECT 1 FROM system_jobs
        WHERE job_type = ? AND video_id = ?
          AND status IN ('queued', 'running', 'retry_wait', 'paused')
        LIMIT 1
        ''',
        (job_type, video_id),
    ).fetchone()
    conn.close()
    return row is not None


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


def update_editable_video_job(
    job_id: str,
    *,
    title: str,
    payload: dict,
    prompt_version: str,
    voice_id: str,
    voice_name: str = "",
    tts_provider_id: str = "genmax",
    voice_revision: int = 1,
    voice_snapshot_json: str = "{}",
) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM system_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        if row["job_type"] != "video_generation":
            raise ValueError("Chỉ có thể sửa job tạo video.")
        if row["status"] not in {"queued", "paused", "error", "canceled"}:
            raise ValueError("Không thể sửa job đang chạy hoặc đã hoàn thành.")
        if row["video_id"] is not None and row["status"] in {"queued", "paused"}:
            raise ValueError(
                "Job đã có checkpoint nên không thể sửa khi đang chờ tiếp tục."
            )

        changes = {
            "title": title,
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "prompt_version": prompt_version,
            "voice_id": voice_id,
            "voice_name": voice_name,
            "tts_provider_id": tts_provider_id,
            "voice_revision": max(1, int(voice_revision or 1)),
            "voice_snapshot_json": voice_snapshot_json or "{}",
            "updated_at": utc_now(),
        }
        if row["status"] in {"error", "canceled"}:
            changes.update({
                "progress": "Đã cập nhật; sẵn sàng chạy lại",
                "result_json": "{}",
                "error": "",
                "video_id": None,
                "recovery_count": 0,
                "resume_from_step": "",
                "next_retry_at": "",
                "cancel_requested": 0,
            })
        assignments = ", ".join(f"{field} = ?" for field in changes)
        conn.execute(
            f"UPDATE system_jobs SET {assignments} WHERE id = ?",
            (*changes.values(), job_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return get_system_job(job_id)


def delete_system_job(job_id: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM system_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        if row["status"] == "running":
            raise ValueError("Hãy dừng job đang chạy trước khi xóa.")
        conn.execute("DELETE FROM system_jobs WHERE id = ?", (job_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return _decode_system_job(row)


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
              AND (
                  ? NOT IN ('comment_publish', 'comment_sync') OR status = 'queued'
                  OR next_retry_at = '' OR next_retry_at <= ?
              )
              AND (
                  video_id IS NULL OR EXISTS (
                      SELECT 1 FROM videos
                      WHERE videos.id = system_jobs.video_id
                        AND videos.video_status = 'active'
                  )
              )
            ORDER BY created_at ASC
            LIMIT 1
            ''',
            (job_type, job_type, now),
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
        SET status = CASE
                WHEN cancel_requested = 1 OR EXISTS (
                    SELECT 1 FROM videos
                    WHERE videos.id = system_jobs.video_id
                      AND videos.video_status = 'error'
                ) THEN 'canceled'
                ELSE 'queued'
            END,
            progress = CASE
                WHEN cancel_requested = 1 THEN 'Đã hủy khi ứng dụng khởi động lại'
                WHEN EXISTS (
                    SELECT 1 FROM videos
                    WHERE videos.id = system_jobs.video_id
                      AND videos.video_status = 'error'
                ) THEN 'Đã bỏ qua vì video ở trạng thái Lỗi'
                ELSE 'Đã khôi phục sau khi ứng dụng khởi động lại'
            END,
            finished_at = CASE
                WHEN cancel_requested = 1 OR EXISTS (
                    SELECT 1 FROM videos
                    WHERE videos.id = system_jobs.video_id
                      AND videos.video_status = 'error'
                ) THEN ? ELSE '' END,
            recovery_count = CASE
                WHEN cancel_requested = 1 OR EXISTS (
                    SELECT 1 FROM videos
                    WHERE videos.id = system_jobs.video_id
                      AND videos.video_status = 'error'
                ) THEN recovery_count
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
          AND (
              ? NOT IN ('comment_publish', 'comment_sync') OR status = 'queued'
              OR next_retry_at = '' OR next_retry_at <= ?
          )
          AND (
              video_id IS NULL OR EXISTS (
                  SELECT 1 FROM videos
                  WHERE videos.id = system_jobs.video_id
                    AND videos.video_status = 'active'
              )
          )
        ORDER BY created_at ASC
        LIMIT 1
        ''',
        (job_type, job_type, now),
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
          AND (
              video_id IS NULL OR EXISTS (
                  SELECT 1 FROM videos
                  WHERE videos.id = system_jobs.video_id
                    AND videos.video_status = 'active'
              )
          )
        ORDER BY
            CASE WHEN ? IN ('comment_publish', 'comment_sync') AND status = 'queued' THEN 0 ELSE 1 END,
            CASE WHEN ? IN ('comment_publish', 'comment_sync') THEN next_retry_at ELSE created_at END ASC,
            created_at ASC
        LIMIT 1
        ''',
        (job_type, job_type, job_type),
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
    changes = {
        "status": "queued",
        "progress": "Đang chờ sau khi tiếp tục",
        "next_retry_at": "",
    }
    if (job.get("result") or {}).get("attention_required"):
        changes.update({"result_json": {}, "error": "", "started_at": ""})
    return update_system_job(job_id, **changes)


def retry_system_job(job_id: str) -> dict | None:
    job = get_system_job(job_id)
    if not job:
        return None
    if job["status"] not in {"error", "canceled"}:
        raise ValueError("Chỉ có thể chạy lại job lỗi hoặc đã hủy.")
    if job.get("video_id") is not None:
        video = get_video(int(job["video_id"]))
        if video and video.get("video_status") == VIDEO_STATUS_ERROR:
            raise ValueError(
                "Video đang ở trạng thái Lỗi. Hãy khôi phục trạng thái trước."
            )
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
