import sqlite3
from unittest.mock import patch

import ultimate_bot
from db_architecture import create_run_record, migrate_vault
from workflow_runtime import WorkflowController


def test_ready_for_upload_updates_exact_run_not_latest_topic():
    conn = sqlite3.connect(":memory:")
    migrate_vault(conn)
    first_id, first_run = create_run_record(conn, "Repeated topic", "news", run_id="run-old")
    second_id, second_run = create_run_record(conn, "Repeated topic", "news", run_id="run-current")
    db_path = ":memory:"
    conn.close()

    # Use a temporary file database because WorkflowController opens its own
    # connection when applying the QC state transition.
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db") as fh:
        path = fh.name
        db = sqlite3.connect(path)
        migrate_vault(db)
        first_id, _ = create_run_record(db, "Repeated topic", "news", run_id="run-old")
        second_id, _ = create_run_record(db, "Repeated topic", "news", run_id="run-current")
        db.close()

        controller = WorkflowController(type("Bot", (), {})())
        controller.bot._last_run_row_id = first_id
        controller.bot._last_run_run_id = "run-old"

        with patch.object(ultimate_bot, "DB_PATH", path):
            controller._mark_latest_run_ready_for_qc("Repeated topic")

        db = sqlite3.connect(path)
        rows = db.execute(
            "SELECT id, run_id, status, video_id FROM vault ORDER BY id"
        ).fetchall()
        db.close()

        assert rows[0] == (first_id, "run-old", "READY_FOR_UPLOAD", "READY_FOR_UPLOAD")
        assert rows[1] == (second_id, "run-current", "PENDING_QC", "PENDING_QC")


def test_ready_for_upload_fails_closed_on_run_id_mismatch(tmp_path):
    path = str(tmp_path / "vault.db")
    db = sqlite3.connect(path)
    migrate_vault(db)
    row_id, _ = create_run_record(db, "Repeated topic", "news", run_id="run-actual")
    db.close()

    controller = WorkflowController(type("Bot", (), {})())
    controller.bot._last_run_row_id = row_id
    controller.bot._last_run_run_id = "run-wrong"

    with patch.object(ultimate_bot, "DB_PATH", path):
        try:
            controller._mark_latest_run_ready_for_qc("Repeated topic")
        except RuntimeError as exc:
            assert "identity" in str(exc).lower()
        else:
            raise AssertionError("run identity mismatch must fail closed")
