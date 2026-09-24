import sqlite3

import ultimate_bot
from db_architecture import create_run_record, migrate_vault
from workflow_runtime import WorkflowController


def test_ready_for_upload_uses_controller_run_id_not_topic_lookup(tmp_path):
    path = str(tmp_path / "vault.db")
    db = sqlite3.connect(path)
    migrate_vault(db)
    first_id, _ = create_run_record(db, "Repeated topic", "news", run_id="run-old")
    second_id, _ = create_run_record(db, "Repeated topic", "news", run_id="run-current")
    db.close()

    controller = WorkflowController(type("Bot", (), {})())
    controller.state.run_id = "run-current"

    original = ultimate_bot.DB_PATH
    ultimate_bot.DB_PATH = path
    try:
        controller._mark_latest_run_ready_for_qc("Repeated topic")
    finally:
        ultimate_bot.DB_PATH = original

    db = sqlite3.connect(path)
    rows = db.execute(
        "SELECT id, run_id, status, video_id FROM vault ORDER BY id"
    ).fetchall()
    db.close()

    assert rows[0] == (first_id, "run-old", "PENDING_QC", "PENDING_QC")
    assert rows[1] == (second_id, "run-current", "READY_FOR_UPLOAD", "READY_FOR_UPLOAD")


def test_ready_for_upload_requires_controller_run_id(tmp_path):
    path = str(tmp_path / "vault.db")
    db = sqlite3.connect(path)
    migrate_vault(db)
    create_run_record(db, "Repeated topic", "news", run_id="run-actual")
    db.close()

    controller = WorkflowController(type("Bot", (), {})())
    original = ultimate_bot.DB_PATH
    ultimate_bot.DB_PATH = path
    try:
        try:
            controller._mark_latest_run_ready_for_qc("Repeated topic")
        except RuntimeError as exc:
            assert "run ID" in str(exc)
        else:
            raise AssertionError("missing controller run ID must fail closed")
    finally:
        ultimate_bot.DB_PATH = original
