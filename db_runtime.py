"""Runtime bridge that gives the legacy pipeline exact database run identity.

The production pipeline still lives in ultimate_bot.py. This bridge makes its
legacy topic-based INSERT/UPDATE safe without requiring a risky rewrite of the
large production file.
"""
import json
import os
import re

from db_architecture import migrate_vault, make_run_id, update_run_record


class _IdentityState:
    def __init__(self, run_id=None):
        self.run_id = run_id or make_run_id()
        self.row_id = None
        self.inserted = False


class _CursorProxy:
    def __init__(self, cursor, state):
        self._cursor = cursor
        self._state = state

    def execute(self, sql, parameters=()):
        sql_text = str(sql)
        upper = sql_text.upper()

        # Stale PENDING_QC records are explicitly rejected rather than being
        # left with a misleading PENDING_QC status. The rewrite is intentionally
        # fail-closed if the known legacy statement shape changes.
        if (
            "UPDATE VAULT SET VIDEO_ID = 'REJECTED'" in upper
            and "WHERE VIDEO_ID = 'PENDING_QC'" in upper
        ):
            pattern = (
                r"UPDATE\s+vault\s+SET\s+video_id\s*=\s*'REJECTED'\s*,\s*reported\s*=\s*1\s*,\s*"
                r"rejected_reason\s*=\s*'Stale timeout'"
            )
            if not re.search(pattern, sql_text, flags=re.IGNORECASE):
                raise ValueError("Refusing to rewrite an unexpected stale-run SQL statement.")
            sql_text = re.sub(
                pattern,
                "UPDATE vault SET video_id = 'REJECTED', reported = 1, rejected_reason = 'Stale timeout', status = 'REJECTED', updated_at = CURRENT_TIMESTAMP",
                sql_text,
                count=1,
                flags=re.IGNORECASE,
            )
            return self._cursor.execute(sql_text, parameters)

        # Create a new row for EVERY production run. Never use INSERT OR IGNORE
        # here: two runs are allowed to have the same topic. Only the exact known
        # legacy statement is rewritten; a similar statement fails closed.
        if "INSERT OR IGNORE INTO VAULT" in upper and "(TOPIC, DATE_USED, GENRE, VIDEO_ID)" in upper:
            pattern = (
                r"INSERT\s+OR\s+IGNORE\s+INTO\s+vault\s*\(topic,\s*date_used,\s*genre,\s*video_id\)\s*"
                r"VALUES\s*\(\?,\s*\?,\s*\?,\s*\?\)"
            )
            if not re.fullmatch(r"\s*" + pattern + r"\s*;?\s*", sql_text, flags=re.IGNORECASE):
                raise ValueError("Refusing to rewrite an unexpected run-record INSERT statement.")
            sql_text = re.sub(
                pattern,
                "INSERT INTO vault (topic, date_used, genre, video_id, run_id, status) VALUES (?, ?, ?, ?, ?, 'PENDING_QC')",
                sql_text,
                count=1,
                flags=re.IGNORECASE,
            )
            params = tuple(parameters) + (self._state.run_id,)
            result = self._cursor.execute(sql_text, params)
            self._state.row_id = self._cursor.lastrowid
            self._state.inserted = self._state.row_id is not None
            return result

        # Pin the final upload write to the exact row created above instead of
        # the historical topic, which may no longer be unique.
        is_final_update = (
            "UPDATE VAULT SET" in upper
            and "VIDEO_ID=?" in upper
            and "TITLE_USED=?" in upper
            and "WHERE TOPIC=?" in upper
            and self._state.row_id is not None
        )
        if is_final_update:
            pattern = r"\s*WHERE\s+topic\s*=\s*\?"
            if not re.search(pattern, sql_text, flags=re.IGNORECASE):
                raise ValueError("Refusing to rewrite an unexpected topic-based final update.")
            sql_text = re.sub(
                pattern,
                ", status='UPLOADED', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                sql_text,
                count=1,
                flags=re.IGNORECASE,
            )
            params = tuple(parameters[:-1]) + (self._state.row_id,)
            return self._cursor.execute(sql_text, params)

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


def _discovery_snapshot(web_config):
    if not isinstance(web_config, dict):
        return None
    story = web_config.get("selected_story")
    if not isinstance(story, dict):
        return None

    dimensions = story.get("discovery_dimensions") or {}
    keys = (
        "event_article_count", "event_source_count", "event_entities", "event_actions",
        "event_momentum_score", "independent_corroboration_score", "freshness_score",
        "visual_potential", "originality_score", "social_signal",
        "google_trends_signal", "source_quality_score", "risk_signal_count",
    )
    snapshot = {key: story.get(key) for key in keys if key in story}
    snapshot["discovery_dimensions"] = dict(dimensions)
    snapshot["event_evidence_publishers"] = list(story.get("event_evidence_publishers") or [])
    snapshot["event_source_domains"] = list(story.get("event_source_domains") or [])
    snapshot["discovery_rank"] = story.get("discovery_rank")
    return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))


def _persist_discovery_snapshot(raw_conn, row_id, web_config):
    snapshot = _discovery_snapshot(web_config)
    if row_id is None or not snapshot:
        return
    raw_conn.execute(
        "UPDATE vault SET discovery_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (snapshot, row_id),
    )
    raw_conn.commit()


def run_robot_with_exact_identity(bot, web_config=None):
    """Run the factory and guarantee that a created run cannot remain pending."""
    original_connect = bot.sqlite3.connect
    state = _IdentityState()
    bot._last_run_identity = state
    bot._last_run_row_id = None
    bot._last_run_run_id = state.run_id

    def connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        try:
            db_path = os.path.abspath(str(args[0])) if args else ""
            target = os.path.abspath(str(getattr(bot, "DB_PATH", "")))
            if db_path == target:
                migrate_vault(conn)
                return _ConnectionProxy(conn, state)
        except Exception:
            pass
        return conn

    bot.sqlite3.connect = connect
    try:
        result = bot.run_robot(web_config=web_config)
    except Exception as exc:
        if state.row_id is not None:
            try:
                raw = original_connect(bot.DB_PATH)
                migrate_vault(raw)
                _persist_discovery_snapshot(raw, state.row_id, web_config)
                update_run_record(
                    raw,
                    state.row_id,
                    status="FAILED",
                    reported=1,
                    rejected_reason=f"{type(exc).__name__}: {str(exc)[:500]}",
                )
                raw.close()
            except Exception as db_exc:
                print(f"   [DB] Could not mark run FAILED: {db_exc}")
        raise
    finally:
        bot.sqlite3.connect = original_connect

    bot._last_run_row_id = state.row_id
    bot._last_run_run_id = state.run_id

    if state.row_id is not None:
        try:
            raw = original_connect(bot.DB_PATH)
            migrate_vault(raw)
            _persist_discovery_snapshot(raw, state.row_id, web_config)
            raw.close()
        except Exception as db_exc:
            print(f"   [DB] Could not persist discovery snapshot: {db_exc}")

    # A normal return can still mean the legacy pipeline stopped early.
    # Dashboard/headless runs never use the interactive QC rejection gate.
    if state.row_id is not None:
        try:
            raw = original_connect(bot.DB_PATH)
            migrate_vault(raw)
            row = raw.execute(
                "SELECT status, video_id FROM vault WHERE id = ?",
                (state.row_id,),
            ).fetchone()
            if row and row[0] == "PENDING_QC":
                if web_config is None:
                    update_run_record(
                        raw,
                        state.row_id,
                        status="REJECTED",
                        reported=1,
                        rejected_reason="Run ended at manual QC gate",
                    )
                else:
                    update_run_record(
                        raw,
                        state.row_id,
                        status="FAILED",
                        reported=1,
                        rejected_reason="Pipeline stopped before upload",
                    )
            raw.close()
        except Exception as db_exc:
            print(f"   [DB] Could not finalise run status: {db_exc}")

    return result
