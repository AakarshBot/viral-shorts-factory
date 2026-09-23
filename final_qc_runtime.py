"""Final artifact and upload-metadata QC for the newsroom workflow."""
from __future__ import annotations

import os
import re


def _validate_final_artifact(path: str) -> tuple[bool, str]:
    if not path or not os.path.isfile(path):
        return False, "final video file is missing"
    try:
        from branding_runtime import _artifact_qc
        return _artifact_qc(path)
    except Exception as exc:
        return False, f"artifact QC unavailable: {type(exc).__name__}: {exc}"


def _validate_metadata(title: str, description: str, comment: str = "") -> tuple[bool, str]:
    title = str(title or "").strip()
    description = str(description or "").strip()
    comment = str(comment or "").strip()
    if not title:
        return False, "final title is empty"
    if len(title) > 100:
        return False, "final title exceeds 100 characters"
    if len(description.split()) < 10:
        return False, "final description is too short"
    if len(description) > 5000:
        return False, "final description exceeds 5000 characters"
    if len(comment) > 4900:
        return False, "creator comment exceeds 4900 characters"
    return True, "metadata checks passed"


def validate_final_video(path: str) -> None:
    ok, reason = _validate_final_artifact(path)
    if not ok:
        raise RuntimeError(reason)


def validate_final_upload_metadata(title: str, description: str, comment: str = "") -> tuple[str, str, str]:
    from youtube_comment_runtime import ensure_shorts_title
    cleaned_title = ensure_shorts_title(title)
    cleaned_description = str(description or "").strip()
    cleaned_comment = str(comment or "").strip()
    ok, reason = _validate_metadata(cleaned_title, cleaned_description, cleaned_comment)
    if not ok:
        raise ValueError(reason)
    return cleaned_title, cleaned_description, cleaned_comment


def _install_exact_run_identity(controller_cls):
    if getattr(controller_cls, "_exact_run_identity_patched", False):
        return
    original_install = controller_cls._install_production_wrappers

    def install_with_exact_identity(self):
        original_install(self)
        if getattr(self, "_exact_identity_runner_installed", False):
            return
        original_run_robot = (
            getattr(self.bot, "_vsf_canonical_run_robot", None)
            or getattr(self.bot, "run_robot", None)
        )
        if not callable(original_run_robot):
            return

        self.bot._vsf_canonical_run_robot = original_run_robot

        def exact_identity_runner(web_config=None):
            from db_runtime import run_robot_with_exact_identity
            self.bot.run_robot = original_run_robot
            try:
                return run_robot_with_exact_identity(self.bot, web_config=web_config)
            finally:
                self.bot.run_robot = exact_identity_runner

        exact_identity_runner._exact_identity_runner = True
        exact_identity_runner._canonical_run_robot = original_run_robot
        self.bot.run_robot = exact_identity_runner
        self._exact_identity_runner_installed = True
        print("   [Final QC] Exact production run identity bridge installed.", flush=True)

    controller_cls._install_production_wrappers = install_with_exact_identity
    controller_cls._exact_run_identity_patched = True


def _mark_exact_run_ready_for_upload(controller, fallback_ready, topic: str):
    """Use exact run identity only; never silently fall back to topic matching."""
    row_id = getattr(controller.bot, "_last_run_row_id", None)
    run_id = str(getattr(controller.bot, "_last_run_run_id", "") or "").strip()
    if row_id is None or not run_id:
        raise RuntimeError("Exact production run identity is unavailable; refusing topic-based READY_FOR_UPLOAD fallback.")

    import sqlite3
    import ultimate_bot
    conn = sqlite3.connect(ultimate_bot.DB_PATH)
    try:
        row = conn.execute("SELECT run_id FROM vault WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"exact production run row {row_id} was not found")
        if str(row[0] or "").strip() != run_id:
            raise RuntimeError(
                f"exact production run identity mismatch for row {row_id}: "
                f"controller={run_id!r}, database={str(row[0] or '').strip()!r}"
            )
        updated = conn.execute(
            """UPDATE vault
               SET video_id='READY_FOR_UPLOAD', status='READY_FOR_UPLOAD', updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND run_id=?""",
            (row_id, run_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if updated != 1:
        raise RuntimeError(f"exact production run row {row_id} was not updated")
    print(f"   [Final QC] Marked exact production run row {row_id} READY_FOR_UPLOAD.", flush=True)
    return None


def patch_workflow_qc(bot) -> bool:
    try:
        import workflow_runtime
    except Exception as exc:
        raise RuntimeError(
            f"Workflow runtime unavailable for final-QC binding: {type(exc).__name__}: {exc}"
        ) from exc

    controller_cls = getattr(workflow_runtime, "WorkflowController", None)
    if controller_cls is None or getattr(controller_cls, "_final_qc_patched", False):
        return controller_cls is not None

    original_ready = controller_cls._mark_latest_run_ready_for_qc
    original_upload = controller_cls.upload_manual
    _install_exact_run_identity(controller_cls)

    def guarded_ready(self, topic):
        video_path = str(getattr(getattr(self, "state", None), "video_path", "") or "")
        validate_final_video(video_path)
        script_data = dict(getattr(getattr(self, "state", None), "script_data", {}) or {})
        try:
            from youtube_comment_runtime import _build_clean_metadata, build_pinned_comment
            category = str(getattr(self.bot, "_active_web_config", {}).get("category", "national_global_affairs"))
            genre_cfg = self.bot.CONTENT_CATEGORIES.get(category, self.bot.CONTENT_CATEGORIES["national_global_affairs"])
            title, description, _tags = _build_clean_metadata(script_data, genre_cfg, getattr(self.bot, "_active_web_config", {}).get("trend_keyword", ""))
            comment = build_pinned_comment(script_data, title, genre_cfg.get("label", ""))
        except Exception as exc:
            raise RuntimeError(f"Final metadata QC could not run before READY_FOR_UPLOAD: {type(exc).__name__}: {exc}") from exc

        validate_final_upload_metadata(title, description, comment)
        print("   [Final QC] READY_FOR_UPLOAD technical checks passed.", flush=True)
        return _mark_exact_run_ready_for_upload(self, original_ready, topic)

    def guarded_upload(self, video_path, script_data, title, description, comment, publish_mode, genre_cfg, trend_keyword=""):
        validate_final_video(video_path)
        validate_final_upload_metadata(title, description, comment)
        print("   [Final QC] Manual-upload technical checks passed.", flush=True)
        return original_upload(self, video_path, script_data, title, description, comment, publish_mode, genre_cfg, trend_keyword)

    guarded_ready._final_qc_wrapped = True
    guarded_upload._final_qc_wrapped = True
    controller_cls._mark_latest_run_ready_for_qc = guarded_ready
    controller_cls.upload_manual = guarded_upload
    controller_cls._final_qc_patched = True
    print("   [Final QC] Workflow artifact + metadata gates installed.", flush=True)
    return True
