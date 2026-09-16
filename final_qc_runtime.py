"""Final artifact QC handoff for the newsroom workflow."""
from __future__ import annotations

import os


def _validate_final_artifact(path: str) -> tuple[bool, str]:
    if not path or not os.path.isfile(path):
        return False, "final video file is missing"
    try:
        from branding_runtime import _artifact_qc
        return _artifact_qc(path)
    except Exception as exc:
        return False, f"artifact QC unavailable: {type(exc).__name__}: {exc}"


def patch_workflow_qc(bot) -> bool:
    """Require final artifact QC before READY_FOR_UPLOAD and before manual upload."""
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
        print(f"   [Final QC] READY_FOR_UPLOAD gate passed: {reason}", flush=True)
        return original_ready(self, topic)

    def guarded_upload(self, video_path, script_data, title, description, comment, publish_mode, genre_cfg, trend_keyword=""):
        ok, reason = _validate_final_artifact(video_path)
        if not ok:
            raise RuntimeError(f"Final artifact QC failed before manual upload: {reason}")
        print(f"   [Final QC] Manual-upload gate passed: {reason}", flush=True)
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
    print("   [Final QC] Workflow handoff and manual-upload artifact gates installed.", flush=True)
    return True
