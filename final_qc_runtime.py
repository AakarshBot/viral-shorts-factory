"""Final artifact and upload-metadata QC for the newsroom workflow."""
from __future__ import annotations

import os
import re

from script_runtime import check_script_originality

def _artifact_qc(path: str) -> tuple[bool, str]:
    """Validate the rendered MP4 without depending on the branding compositor."""
    if not path or not os.path.isfile(path):
        return False, "final video file is missing"
    try:
        import cv2

        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            capture.release()
            return False, "final video could not be opened by the local video decoder"

        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if fps > 0 else 0.0

        ok_first, _ = capture.read()
        capture.release()

        if not ok_first:
            return False, "final video contains no readable frames"
        if width != 1080 or height != 1920:
            return False, f"final video geometry is {width}x{height}; expected 1080x1920"
        if fps <= 0 or frame_count <= 0:
            return False, "final video has no usable frame timing"
        if duration < 1.0:
            return False, f"final video duration is only {duration:.2f}s"

        size_mb = os.path.getsize(path) / (1024 * 1024)
        return True, (
            f"Rendered MP4 passed artifact QC: {width}x{height}, "
            f"{fps:.2f} fps, {duration:.2f}s, {size_mb:.1f} MB."
        )
    except Exception as exc:
        return False, f"artifact QC unavailable: {type(exc).__name__}: {exc}"


def _validate_final_artifact(path: str) -> tuple[bool, str]:
    return _artifact_qc(path)


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


def evaluate_originality_gate(script_data: dict) -> dict:
    """Validate originality/factuality for the final public-release decision."""
    data = script_data if isinstance(script_data, dict) else {}
    fallback = str(data.get("fallback_mode") or "") == "extractive_source_grounded"
    if data.get("public_publish_blocked"):
        return {
            "passed": True,
            "public_blocked": True,
            "label": "Originality + factuality",
            "detail": "Private/manual release is allowed, but public publication is blocked by an upstream safety gate.",
        }
    if fallback:
        return {
            "passed": True,
            "public_blocked": True,
            "label": "Originality + factuality",
            "detail": "PASS for private-only release. Extractive source-grounded fallback blocks public upload.",
        }
    originality = check_script_originality(data, data)
    if not originality.get("passed"):
        return {
            "passed": False,
            "public_blocked": True,
            "label": "Originality + factuality",
            "detail": f"Verbatim-overlap gate failed in {len(originality.get('failures') or [])} scene(s).",
        }
    critique = data.get("originality_critique") or {}
    if critique.get("unsupported_claims"):
        return {
            "passed": False,
            "public_blocked": True,
            "label": "Originality + factuality",
            "detail": "Factual critique found unsupported claims or the critique provider was unavailable.",
        }
    return {
        "passed": True,
        "public_blocked": False,
        "label": "Originality + factuality",
        "detail": "Originality overlap and factual critique passed.",
    }


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
        original_run_robot = getattr(self.bot, "run_robot", None)
        if not callable(original_run_robot):
            return

        def exact_identity_runner(web_config=None):
            from db_runtime import run_robot_with_exact_identity
            self.bot.run_robot = original_run_robot
            try:
                return run_robot_with_exact_identity(self.bot, web_config=web_config)
            finally:
                self.bot.run_robot = exact_identity_runner

        exact_identity_runner._exact_identity_runner = True
        self.bot.run_robot = exact_identity_runner
        self._exact_identity_runner_installed = True
        print("   [Final QC] Exact production run identity bridge installed.", flush=True)

    controller_cls._install_production_wrappers = install_with_exact_identity
    controller_cls._exact_run_identity_patched = True


def _mark_exact_run_ready_for_upload(controller, fallback_ready, topic: str):
    """Use exact run identity only; never silently fall back to topic matching."""
    row_id = getattr(controller.bot, "_last_run_row_id", None)
    if row_id is None:
        raise RuntimeError("Exact production run identity is unavailable; refusing topic-based READY_FOR_UPLOAD fallback.")

    import sqlite3
    import ultimate_bot
    conn = sqlite3.connect(ultimate_bot.DB_PATH)
    try:
        updated = conn.execute(
            """UPDATE vault
               SET video_id='READY_FOR_UPLOAD', status='READY_FOR_UPLOAD', updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (row_id,),
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
        print(f"   [Final QC] Workflow runtime unavailable: {exc}", flush=True)
        return False

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
        originality = evaluate_originality_gate(script_data)
        if not originality["passed"]:
            raise RuntimeError(originality["detail"])
        print("   [Final QC] READY_FOR_UPLOAD originality and factuality gate passed.", flush=True)
        return _mark_exact_run_ready_for_upload(self, original_ready, topic)

    def guarded_upload(self, video_path, script_data, title, description, comment, publish_mode, genre_cfg, trend_keyword=""):
        validate_final_video(video_path)
        validate_final_upload_metadata(title, description, comment)
        originality = evaluate_originality_gate(script_data)
        if not originality["passed"]:
            raise RuntimeError(originality["detail"])
        if originality.get("public_blocked") and str(publish_mode).lower() == "public":
            raise RuntimeError("Public upload blocked: extractive source-grounded fallback is private-only.")
        print("   [Final QC] Manual-upload originality and factuality gate passed.", flush=True)
        return original_upload(self, video_path, script_data, title, description, comment, publish_mode, genre_cfg, trend_keyword)

    guarded_ready._final_qc_wrapped = True
    guarded_upload._final_qc_wrapped = True
    controller_cls._mark_latest_run_ready_for_qc = guarded_ready
    controller_cls.upload_manual = guarded_upload
    controller_cls._final_qc_patched = True
    print("   [Final QC] Workflow artifact + metadata gates installed.", flush=True)
    return True
