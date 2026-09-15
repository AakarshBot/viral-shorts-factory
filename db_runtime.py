"""Runtime bridge that gives the legacy pipeline exact database run identity.

The main production file is intentionally left stable. This module intercepts
only the legacy vault INSERT/UPDATE statements so each execution gets its own
run_id and later writes are pinned to the exact SQLite row id.
"""
import os
import re

from db_architecture import migrate_vault, make_run_id


class _IdentityState:
    def __init__(self, run_id=None):
        self.run_id = run_id or make_run_id()
        self.row_id = None


class _CursorProxy:
    def __init__(self, cursor, state):
        self._cursor = cursor
        self._state = state

    def execute(self, sql, parameters=()):
        sql_text = str(sql)
        upper = sql_text.upper()

        # The legacy pipeline creates the production row with topic as its
        # apparent identity. Add a real run_id and capture SQLite's row id.
        if "INSERT OR IGNORE INTO VAULT" in upper and "(TOPIC, DATE_USED, GENRE, VIDEO_ID)" in upper:
            sql_text = re.sub(
                r"INSERT\s+OR\s+IGNORE\s+INTO\s+vault\s*\(topic,\s*date_used,\s*genre,\s*video_id\)\s*VALUES\s*\(\?,\s*\?,\s*\?,\s*\?\)",
                "INSERT INTO vault (topic, date_used, genre, video_id, run_id, status) VALUES (?, ?, ?, ?, ?, 'PENDING_QC')",
                sql_text,
                flags=re.IGNORECASE,
            )
            params = tuple(parameters) + (self._state.run_id,)
            result = self._cursor.execute(sql_text, params)
            self._state.row_id = self._cursor.lastrowid
            return result

        # The legacy final write uses WHERE topic=?. Replace that identity
        # with the exact row id created above. This prevents duplicate topics
        # or repeated runs from ever updating another run's record.
        is_final_update = (
            "UPDATE VAULT SET" in upper
            and "VIDEO_ID=?" in upper
            and "TITLE_USED=?" in upper
            and "WHERE TOPIC=?" in upper
            and self._state.row_id is not None
        )
        if is_final_update:
            sql_text = re.sub(
                r"WHERE\s+topic\s*=\s*\?",
                ", status='UPLOADED', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                sql_text,
                count=1,
                flags=re.IGNORECASE,
            )
            params = tuple(parameters[:-1]) + (self._state.row_id,)
            self._cursor.execute(sql_text, params)
            return self._cursor

        return self._cursor.execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self._cursor.executemany(sql, seq_of_parameters)

    def executescript(self, script):
        return self._cursor.executescript(script)

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _ConnectionProxy:
    def __init__(self, conn, state):
        self._conn = conn
        self._state = state

    def cursor(self, *args, **kwargs):
        return _CursorProxy(self._conn.cursor(*args, **kwargs), self._state)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self._conn.executemany(sql, seq_of_parameters)

    def executescript(self, script):
        return self._conn.executescript(script)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        return self._conn.__exit__(exc_type, exc_value, tb)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def run_robot_with_exact_identity(bot, web_config=None):
    """Run the legacy robot while enforcing run_id/id database identity."""
    original_connect = bot.sqlite3.connect
    state = _IdentityState()

    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        try:
            db_path = os.path.abspath(str(args[0])) if args else ""
            target = os.path.abspath(str(getattr(bot, "DB_PATH", "")))
            if db_path == target:
                migrate_vault(conn)
                return _ConnectionProxy(conn, state)
        except Exception:
            # Do not hide the original connection if migration/proxy setup
            # cannot be applied. The production run will surface the error.
            pass
        return conn

    bot.sqlite3.connect = connect
    try:
        return bot.run_robot(web_config=web_config)
    finally:
        bot.sqlite3.connect = original_connect
