"""SQLite schema migration for the Viral Shorts Factory vault.

The old vault used topic TEXT PRIMARY KEY. This migration adds a real row ID
and run ID while keeping the existing topic column so the rest of the legacy
pipeline can continue reading historical data safely.
"""
import sqlite3
from datetime import datetime

VAULT_COLUMNS = [
    ("topic", "TEXT"), ("date_used", "TIMESTAMP"), ("genre", "TEXT"),
    ("video_id", "TEXT"), ("reported", "INTEGER DEFAULT 0"), ("views", "INTEGER DEFAULT 0"),
    ("title_used", "TEXT"), ("hook_type", "TEXT"), ("structure_used", "TEXT"),
    ("persona_used", "TEXT"), ("hook_strength", "REAL"), ("narrative_completeness", "REAL"),
    ("audience_fit", "REAL"), ("monetization_risk", "REAL"), ("shelf_life", "REAL"),
    ("composite_score", "REAL"), ("rejected_reason", "TEXT"), ("script_json", "TEXT"),
    ("ai_image_ratio", "REAL"), ("voice_gender", "TEXT"), ("format_used", "TEXT"),
    ("language_used", "TEXT"), ("avg_view_duration", "REAL"), ("avg_view_percentage", "REAL"),
    ("combo_key", "TEXT"), ("title_ctr", "REAL"), ("hook_style_used", "TEXT"),
    ("trend_keyword", "TEXT"),
]


def _create(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS vault (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        topic TEXT,
        date_used TIMESTAMP,
        genre TEXT,
        video_id TEXT,
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
        ai_image_ratio REAL,
        voice_gender TEXT,
        format_used TEXT,
        language_used TEXT,
        avg_view_duration REAL,
        avg_view_percentage REAL,
        combo_key TEXT,
        title_ctr REAL,
        hook_style_used TEXT,
        trend_keyword TEXT
    )")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_topic ON vault(topic)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_date ON vault(date_used)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_video_id ON vault(video_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_genre ON vault(genre)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vault_run_id ON vault(run_id)")


def migrate_vault(conn):
    """Upgrade an existing database without deleting its historical rows."""
    table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vault'").fetchone()
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
        new_cols = old_cols + ["run_id"]
        select_cols = ",".join(old_cols)
        placeholders = ",".join("?" for _ in new_cols)
        # Preserve every old value and generate a stable run ID for each row.
        rows = conn.execute(f'SELECT {select_cols} FROM "{backup}"').fetchall()
        for row in rows:
            values = list(row) + [f"legacy-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{len(row)}"]
            conn.execute(f'INSERT INTO vault ({",".join(new_cols)}) VALUES ({placeholders})', values)
        conn.commit()
        print(f"   [DB] Migrated {len(rows)} historical vault rows. Legacy backup: {backup}")
    else:
        _create(conn)
        conn.commit()


def make_run_id():
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")
