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
    if re.search(r"#shorts\b", title, flags=re.IGNORECASE):
        return False, "#shorts is not allowed in the title"
    if len(description.split()) < 10:
        return False, "final description is too short"
    if len(description) > 5000:
        return False, "final description exceeds 5000 characters"
    if len(comment) > 4900:
        return False, "creator comment exceeds 4900 characters"
    return True, "metadata checks passed"


def patch_workflow_qc(bot) -> bool:
    """Require final artifact + metadata QC before READY_FOR_UPLOAD and upload."""
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

    def guarded_ready(self, topic):
        video_path = str(getattr(getattr(self, "state", None), "video_path", "") or "")
        ok, reason = _validate_final_artifact(video_path)
        if not ok:
            raise RuntimeError(f"Final artifact QC failed before READY_FOR_UPLOAD: {reason}")

        script_data = dict(getattr(getattr(self, "state", None), "script_data", {}) or {})
        try:
            from youtube_comment_runtime import _build_clean_metadata, build_pinned_comment
            category = str(getattr(self.bot, "_active_web_config", {}).get("category", "national_global_affairs"))
            genre_cfg = self.bot.CONTENT_CATEGORIES.get(category, self.bot.CONTENT_CATEGORIES["national_global_affairs"])
            title, description, _tags = _build_clean_metadata(
                script_data,
                genre_cfg,
                getattr(self.bot, "_active_web_config", {}).get("trend_keyword", ""),
            )
            comment = build_pinned_comment(script_data, title, genre_cfg.get("label", ""))
        except Exception as exc:
            raise RuntimeError(f"Final metadata QC could not run before READY_FOR_UPLOAD: {type(exc).__name__}: {exc}") from exc

        metadata_ok, metadata_reason = _validate_metadata(title, description, comment)
        if not metadata_ok:
            raise RuntimeError(f"Final metadata QC failed before READY_FOR_UPLOAD: {metadata_reason}")

        print(f"   [Final QC] READY_FOR_UPLOAD gate passed: {reason}; {metadata_reason}.", flush=True)
        return original_ready(self, topic)

    def guarded_upload(self, video_path, script_data, title, description, comment, publish_mode, genre_cfg, trend_keyword=""):
        ok, reason = _validate_final_artifact(video_path)
        if not ok:
            raise RuntimeError(f"Final artifact QC failed before manual upload: {reason}")
        metadata_ok, metadata_reason = _validate_metadata(title, description, comment)
        if not metadata_ok:
            raise RuntimeError(f"Final metadata QC failed before manual upload: {metadata_reason}")
        print(f"   [Final QC] Manual-upload gate passed: {reason}; {metadata_reason}.", flush=True)
        return original_upload(
            self,
            video_path,
            script_data,
            title,
            description,
            comment,
            publish_mode,
            genre_cfg,
            trend_keyword,
        )

    guarded_ready._final_qc_wrapped = True
    guarded_upload._final_qc_wrapped = True
    controller_cls._mark_latest_run_ready_for_qc = guarded_ready
    controller_cls.upload_manual = guarded_upload
    controller_cls._final_qc_patched = True
    print("   [Final QC] Workflow artifact + metadata gates installed.", flush=True)
    return True
