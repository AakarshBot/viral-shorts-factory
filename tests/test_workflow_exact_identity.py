import sqlite3
from unittest.mock import patch

import ultimate_bot
from db_architecture import create_run_record, migrate_vault
from workflow_runtime import WorkflowController




def _qc_ready_controller(controller):
    import final_qc_runtime

    controller.bot.CONTENT_CATEGORIES = ultimate_bot.CONTENT_CATEGORIES
    controller.bot._active_web_config = {
        "category": "national_global_affairs",
        "trend_keyword": "",
    }
    controller.state.script_data = {
        "title": "Repeated topic",
        "seo_description": "This is a sufficiently detailed description for the exact run identity test.",
        "script": [],
    }
    controller.state.video_path = "synthetic-final.mp4"
    return (
        patch.object(final_qc_runtime, "validate_final_video", lambda *_args, **_kwargs: None),
        patch.object(
            final_qc_runtime,
            "validate_final_upload_metadata",
            lambda title, description, comment: (title, description, comment),
        ),
        patch.object(
            final_qc_runtime,
            "evaluate_originality_gate",
            lambda _data: {"passed": True, "public_blocked": False},
        ),
    )

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
    import os
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
        fh.close()
        db = sqlite3.connect(path)
        migrate_vault(db)
        first_id, _ = create_run_record(db, "Repeated topic", "news", run_id="run-old")
        second_id, _ = create_run_record(db, "Repeated topic", "news", run_id="run-current")
        db.close()

        controller = WorkflowController(type("Bot", (), {})())
        controller.bot._last_run_row_id = first_id
        controller.bot._last_run_run_id = "run-old"

        final_video_patch, metadata_patch, originality_patch = _qc_ready_controller(controller)
        with final_video_patch, metadata_patch, originality_patch:
            with patch.object(ultimate_bot, "DB_PATH", path):
                controller._mark_latest_run_ready_for_qc("Repeated topic")

        db = sqlite3.connect(path)
        rows = db.execute(
            "SELECT id, run_id, status, video_id FROM vault ORDER BY id"
        ).fetchall()
        db.close()
        os.unlink(path)

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

    final_video_patch, metadata_patch, originality_patch = _qc_ready_controller(controller)
    with final_video_patch, metadata_patch, originality_patch:
        with patch.object(ultimate_bot, "DB_PATH", path):
            try:
                controller._mark_latest_run_ready_for_qc("Repeated topic")
            except RuntimeError as exc:
                assert "identity" in str(exc).lower()
            else:
                raise AssertionError("run identity mismatch must fail closed")
