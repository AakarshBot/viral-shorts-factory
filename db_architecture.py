"""SQLite schema and run-identity helpers for the Viral Shorts Factory."""
import uuid
from datetime import datetime

VAULT_COLUMNS = [
    ("topic", "TEXT"), ("date_used", "TIMESTAMP"), ("genre", "TEXT"),
    ("video_id", "TEXT"), ("reported", "INTEGER DEFAULT 0"), ("views", "INTEGER DEFAULT 0"),
    ("title_used", "TEXT"), ("hook_type", "TEXT"), ("structure_used", "TEXT"),
    ("persona_used", "TEXT"), ("hook_strength", "REAL"), ("narrative_completeness", "REAL"),
    ("audience_fit", "REAL"), ("monetization_risk", "REAL"), ("shelf_life", "REAL"),
    ("composite_score", "REAL"), ("rejected_reason", "TEXT"), ("script_json", "TEXT"),
    ("asset_credits_json", "TEXT"), ("ai_image_ratio", "REAL"), ("voice_gender", "TEXT"), ("format_used", "TEXT"),
    ("language_used", "TEXT"), ("avg_view_duration", "REAL"), ("avg_view_percentage", "REAL"),
    ("engaged_views", "INTEGER"), ("stayed_to_watch", "REAL"), ("likes", "INTEGER"), ("comments", "INTEGER"),
    ("combo_key", "TEXT"), ("title_ctr", "REAL"), ("hook_style_used", "TEXT"),
    ("trend_keyword", "TEXT"), ("discovery_event_key", "TEXT"),
]


def _create(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS vault (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        topic TEXT,
        date_used TIMESTAMP,
        genre TEXT,
        video_id TEXT,
        status TEXT DEFAULT 'COMPLETED',
        reported INTEGER DEFAULT 0,
        views INTEGER DEFAULT 0,
        title_used TEXT,
        hook_type TEXT,
        structure_used TEXT,
        persona_used TEXT,
        hook_strength REAL,
        narrative_completeness REAL,
        audience_fit REAL,
        monetization_risk REAL,
        shelf_life REAL,
        composite_score REAL,
        rejected_reason TEXT,
        script_json TEXT,
        asset_credits_json TEXT,
        ai_image_ratio REAL,
        voice_gender TEXT,
        format_used TEXT,
        language_used TEXT,
        avg_view_duration REAL,
        avg_view_percentage REAL,
        engaged_views INTEGER,
        stayed_to_watch REAL,
        likes INTEGER,
        comments INTEGER,
        combo_key TEXT,
        title_ctr REAL,
        hook_style_used TEXT,
        trend_keyword TEXT,
        discovery_event_key TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_topic ON vault(topic)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_date ON vault(date_used)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_video_id ON vault(video_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_genre ON vault(genre)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_run_id ON vault(run_id) WHERE run_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_status ON vault(status)")


def _add_column(conn, name, definition):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(vault)").fetchall()}
    if name not in columns:
        conn.execute(f"ALTER TABLE vault ADD COLUMN {name} {definition}")


def migrate_vault(conn):
    """Upgrade the old topic-primary-key vault without losing historical rows."""
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='vault'"
    ).fetchone()

    if not table:
        _create(conn)
        conn.commit()
        return

    columns = [r[1] for r in conn.execute("PRAGMA table_info(vault)").fetchall()]

    if "id" not in columns:
        backup = "vault_legacy_backup_" + datetime.now().strftime("%Y%m%d%H%M%S")
        conn.execute(f'ALTER TABLE vault RENAME TO "{backup}"')
        _create(conn)
        old_cols = [c for c, _ in VAULT_COLUMNS if c in columns]
        if old_cols:
            select_cols = ",".join(old_cols)
            rows = conn.execute(f'SELECT {select_cols} FROM "{backup}"').fetchall()
            placeholders = ",".join("?" for _ in old_cols)
            for row_index, row in enumerate(rows):
                run_id = f"legacy-{row_index + 1}-{uuid.uuid4().hex}"
                conn.execute(
                    f'INSERT INTO vault ({select_cols}, run_id) VALUES ({placeholders}, ?)',
                    list(row) + [run_id],
                )
            print(f"   [DB] Migrated {len(rows)} historical vault rows. Legacy backup: {backup}")
        conn.commit()
        return

    _add_column(conn, "run_id", "TEXT")
    _add_column(conn, "status", "TEXT DEFAULT 'COMPLETED'")
    _add_column(conn, "asset_credits_json", "TEXT")
    _add_column(conn, "engaged_views", "INTEGER")
    _add_column(conn, "stayed_to_watch", "REAL")
    _add_column(conn, "likes", "INTEGER")
    _add_column(conn, "comments", "INTEGER")
    _add_column(conn, "created_at", "TIMESTAMP")
    _add_column(conn, "updated_at", "TIMESTAMP")
    _add_column(conn, "discovery_event_key", "TEXT")
    conn.execute(
        """UPDATE vault
           SET status = CASE
               WHEN status IN (
                   'RUNNING', 'WAITING_SCRIPT_REVIEW', 'WAITING_VISUAL_REVIEW',
                   'READY_FOR_UPLOAD', 'UPLOADED', 'UPLOADED_PRIVATE',
                   'FAILED', 'REJECTED'
               ) THEN status
               WHEN video_id = 'PENDING_QC' THEN 'PENDING_QC'
               WHEN video_id = 'REJECTED' THEN 'REJECTED'
               WHEN video_id IS NULL OR video_id = '' THEN 'FAILED'
               ELSE COALESCE(status, 'COMPLETED')
           END"""
    )
    conn.execute("UPDATE vault SET created_at = COALESCE(created_at, date_used, CURRENT_TIMESTAMP)")
    conn.execute("UPDATE vault SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_topic ON vault(topic)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_date ON vault(date_used)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_video_id ON vault(video_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_genre ON vault(genre)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_run_id ON vault(run_id) WHERE run_id IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_status ON vault(status)")
    conn.commit()


def make_run_id():
    """Return a collision-resistant identifier for one factory execution."""
    return uuid.uuid4().hex


def create_run_record(conn, topic, genre, run_id=None):
    """Create the authoritative database row for one production run."""
    migrate_vault(conn)
    run_id = run_id or make_run_id()
    cur = conn.execute(
        """INSERT INTO vault
        (run_id, topic, date_used, genre, video_id, status, reported)
        VALUES (?, ?, ?, ?, 'PENDING_QC', 'PENDING_QC', 0)""",
        (run_id, topic, datetime.now(), genre),
    )
    conn.commit()
    return cur.lastrowid, run_id


def update_run_record(conn, row_id, **fields):
    """Update exactly one run row. Topic is never used as identity."""
    if not fields:
        return
    allowed = {
        "topic", "date_used", "genre", "video_id", "status", "reported", "views",
        "title_used", "hook_type", "structure_used", "persona_used", "hook_strength",
        "narrative_completeness", "audience_fit", "monetization_risk", "shelf_life",
        "composite_score", "rejected_reason", "script_json", "asset_credits_json", "ai_image_ratio", "voice_gender",
        "format_used", "language_used", "avg_view_duration", "avg_view_percentage",
        "engaged_views", "stayed_to_watch", "likes", "comments", "combo_key", "title_ctr", "hook_style_used",
        "trend_keyword", "discovery_event_key",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unsupported vault fields: {sorted(unknown)}")
    assignments = ", ".join(f"{key} = ?" for key in fields)
    assignments += ", updated_at = CURRENT_TIMESTAMP"
    conn.execute(f"UPDATE vault SET {assignments} WHERE id = ?", list(fields.values()) + [row_id])
    conn.commit()
