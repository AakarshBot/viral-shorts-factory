"""Small, deterministic hardening layer for the final manual upload gate.

This does not automate publishing. It only makes the existing dashboard upload
more reliable and keeps the user's Private/Public choice intact.
"""
from __future__ import annotations

import os
from functools import wraps


def _resolve_video_path(bot, video_path: str) -> str:
    raw = os.path.expanduser(str(video_path or "").strip())
    candidates = []
    if raw:
        candidates.append(raw)
        if not os.path.isabs(raw):
            candidates.append(os.path.join(str(getattr(bot, "BASE_DIR", "")), raw))
    basename = os.path.basename(raw) if raw else ""
    if basename:
        assets_dir = str(getattr(bot, "ASSETS_DIR", "") or "")
        base_dir = str(getattr(bot, "BASE_DIR", "") or "")
        if assets_dir:
            candidates.append(os.path.join(assets_dir, basename))
        if base_dir:
            candidates.append(os.path.join(base_dir, basename))

    seen = set()
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate):
            return candidate

    # Return the original absolute path so the existing error remains useful.
    return os.path.abspath(raw) if raw else raw


def install():
    """Patch WorkflowController.upload_manual once. Returns True when installed."""
    try:
        from workflow_runtime import WorkflowController
    except Exception:
        return False

    if getattr(WorkflowController, "_upload_hardening_installed", False):
        return True

    original = WorkflowController.upload_manual

    @wraps(original)
    def hardened_upload_manual(self, video_path, script_data, title, description, comment, publish_mode, genre_cfg, trend_keyword=""):
        resolved = _resolve_video_path(self.bot, video_path)
        visibility = str(publish_mode or "private").strip().lower()
        if visibility not in {"private", "public"}:
            visibility = "private"
        print(f"   [Upload] Local file: {resolved}", flush=True)
        print(f"   [Upload] YouTube visibility: {visibility}", flush=True)
        result = original(
            self,
            resolved,
            script_data,
            title,
            description,
            comment,
            visibility,
            genre_cfg,
            trend_keyword,
        )
        if not result:
            raise RuntimeError(
                "YouTube upload did not return a video ID. The dashboard will not claim the upload succeeded."
            )
        print(f"   [Upload] Confirmed YouTube video ID: {result}", flush=True)
        return str(result)

    WorkflowController.upload_manual = hardened_upload_manual
    WorkflowController._upload_hardening_installed = True
    return True
