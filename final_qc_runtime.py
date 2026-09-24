"""Final artifact and upload-metadata QC for the newsroom workflow."""
from __future__ import annotations

import os
import re


def _validate_final_artifact(path: str) -> tuple[bool, str]:
    """Validate that the rendered video is a real, decodable final artifact."""
    if not path or not os.path.isfile(path):
        return False, "final video file is missing"
    try:
        from branding_runtime import _artifact_qc
        artifact_ok, artifact_reason = _artifact_qc(path)
        if not artifact_ok:
            return False, artifact_reason
    except Exception as exc:
        return False, f"artifact QC unavailable: {type(exc).__name__}: {exc}"

    try:
        from moviepy import VideoFileClip
        with VideoFileClip(path) as clip:
            duration = float(clip.duration or 0.0)
    except Exception as exc:
        return False, f"final video duration could not be validated: {type(exc).__name__}: {exc}"

    if duration <= 0.0:
        return False, "final video duration is invalid"
    return True, f"final video artifact validated ({duration:.2f}s)"


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


