"""Dashboard-only orchestration helpers for the Viral Shorts Factory.

This module changes the dashboard experience without changing discovery, script,
audio, visual retrieval, rendering, provider adapters or upload implementations.
It adds only a dashboard-side visual review gate and presentation helpers.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps

from workflow_runtime import WorkflowController


def _manual_crop_box_to_shorts(
    img: Image.Image,
    crop_box: dict[str, Any],
    free_size: bool = False,
) -> Image.Image:
    """Apply the dashboard crop selection and return a renderer-safe Shorts frame."""
    source = img.convert("RGB")
    try:
        left = int(crop_box.get("left", 0))
        top = int(crop_box.get("top", 0))
        width = int(crop_box.get("width", 0))
        height = int(crop_box.get("height", 0))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("The selected crop area is invalid.")

    left = max(0, min(source.width - 1, left))
    top = max(0, min(source.height - 1, top))
    width = max(1, min(source.width - left, width))
    height = max(1, min(source.height - top, height))

    cropped = source.crop((left, top, left + width, top + height))
    if free_size:
        return ImageOps.fit(
            cropped,
            (1080, 1920),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    aspect = width / max(1, height)
    if abs(aspect - (9 / 16)) > 0.025:
        raise ValueError("The selected crop area must remain 9:16 for Shorts.")
    return cropped.resize((1080, 1920), Image.Resampling.LANCZOS)


def _manual_crop_to_shorts(img: Image.Image, zoom: float = 1.0, x_center: float = 0.5, y_center: float = 0.5) -> Image.Image:
    """Create a user-positioned 9:16 crop from the preserved original source."""
    source = img.convert("RGB")
    target_aspect = 1080 / 1920
    source_aspect = source.width / max(1, source.height)

    if source_aspect >= target_aspect:
        crop_h = source.height
        crop_w = max(1, int(round(crop_h * target_aspect)))
    else:
        crop_w = source.width
        crop_h = max(1, int(round(crop_w / target_aspect)))

    zoom = max(1.0, min(4.0, float(zoom or 1.0)))
    crop_w = max(1, int(round(crop_w / zoom)))
    crop_h = max(1, int(round(crop_h / zoom)))

    max_left = max(0, source.width - crop_w)
    max_top = max(0, source.height - crop_h)
    x_center = max(0.0, min(1.0, float(x_center)))
    y_center = max(0.0, min(1.0, float(y_center)))

    left = int(round(max_left * x_center))
    top = int(round(max_top * y_center))
    cropped = source.crop((left, top, left + crop_w, top + crop_h))
    return cropped.resize((1080, 1920), Image.Resampling.LANCZOS)


LIVE_STAGE_SPEC = (
    {"label": "Headlines", "key": "discovery", "min_percent": 0, "max_percent": 14},
    {"label": "Research", "key": "research", "min_percent": 15, "max_percent": 23},
    {"label": "Script", "key": "script_review", "min_percent": 24, "max_percent": 40},
    {"label": "Voiceover", "key": "audio", "min_percent": 41, "max_percent": 54},
    {"label": "Visuals", "key": "visual_approval", "min_percent": 55, "max_percent": 76},
    {"label": "Render", "key": "render", "min_percent": 77, "max_percent": 95},
    {"label": "Final QC", "key": "qc", "min_percent": 96, "max_percent": 100},
)

LIVE_STAGE_BY_KEY = {item["key"]: item for item in LIVE_STAGE_SPEC}

LIVE_MONITOR_INTERACTIVE_STAGES = frozenset({"script_review", "visual_approval"})


def live_monitor_should_poll(snapshot: dict[str, Any]) -> bool:
    """Poll only while the worker is running outside a user-input checkpoint."""
    snapshot = snapshot or {}
    if not bool(snapshot.get("thread_alive")):
        return False
    return str(snapshot.get("stage") or "").strip() not in LIVE_MONITOR_INTERACTIVE_STAGES


def upload_ready_for_manual_decision(snapshot: dict[str, Any]) -> bool:
    """Return True only when a completed, idle render is ready for upload visibility selection."""
    video_path = str(snapshot.get("video_path") or "").strip()
    return (
        bool(snapshot.get("completed"))
        and not bool(snapshot.get("thread_alive"))
        and bool(video_path)
    )


def build_discovery_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, explainable evidence profile for one event candidate."""
    dimensions = candidate.get("discovery_dimensions") or {}
    evidence = [
        item for item in (candidate.get("event_evidence") or [])
        if isinstance(item, dict)
    ]
    independent_publishers = list(candidate.get("event_evidence_publishers") or [])
    domains = list(candidate.get("event_source_domains") or [])
    official_records = sum(
        1 for item in evidence
        if str(item.get("collection_source") or "").strip().lower() == "official"