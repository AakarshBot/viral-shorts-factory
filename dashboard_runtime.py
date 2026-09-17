"""Dashboard-only orchestration helpers for the Viral Shorts Factory.

This module changes the dashboard experience without changing discovery, script,
audio, visual retrieval, rendering, provider adapters or upload implementations.
It adds only a dashboard-side visual review gate and presentation helpers.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from workflow_runtime import WorkflowController


class DashboardWorkflowController(WorkflowController):
    """WorkflowController with a dashboard-side visual approval checkpoint."""

    def __init__(self, bot):
        super().__init__(bot)
        self._visual_approval_event = threading.Event()
        self._visual_approved = False
        self._visual_rejected = False
        self._visual_packages: list[Any] = []
        self._dashboard_logs: list[str] = []
        self._last_dashboard_message = ""

    def reset(self):
        self._visual_approval_event.clear()
        self._visual_approved = False
        self._visual_rejected = False
        self._visual_packages = []
        self._dashboard_logs = []
        self._last_dashboard_message = ""
        super().reset()

    @staticmethod
    def _friendly_message(stage: str, message: str) -> str:
        text = str(message or "").strip()
        if not text:
            return f"{stage.title()} is in progress."
        replacements = {
            "Preparing the production pipeline": "Preparing the selected story for production.",
            "Researching multiple sources": "Checking the story against multiple sources.",
            "selected story locked": "The selected story is locked. Production is starting.",
            "Writing and self-critiquing": "Writing the script from the selected story and checking its structure.",
            "Script complete": "The script is ready. Moving to narration.",
            "Generating narration and word timings": "Creating the voiceover and matching word timings.",
            "Narration complete": "Voiceover is ready. Building the visuals.",
            "Sourcing and verifying content-first visuals": "Finding, checking and preparing the story visuals.",
            "Visual package complete": "All visuals are ready for your review.",
            "Stitching scenes": "Combining scenes, subtitles, audio and branding into the final Short.",
            "Final video rendered": "The final Short is rendered and ready for QC.",
            "Automatic upload blocked": "Automatic upload is disabled; your dashboard controls the upload.",
        }
        for needle, friendly in replacements.items():
            if needle.lower() in text.lower():
                return friendly
        return text.replace("…", "...")

    def update(self, stage: str, percent: int, message: str):
        friendly = self._friendly_message(stage, message)
        super().update(stage, percent, friendly)
        with self._lock:
            if friendly != self._last_dashboard_message:
                self._dashboard_logs.append(friendly)
                self._dashboard_logs = self._dashboard_logs[-18:]
                self._last_dashboard_message = friendly

    def _install_production_wrappers(self):
        super()._install_production_wrappers()
        if getattr(self, "_dashboard_visual_gate_bound", False):
            return
        run_robot = getattr(self.bot, "run_robot", None)
        namespace = getattr(run_robot, "__globals__", None)
        if not isinstance(namespace, dict):
            return

        current = namespace.get("process_visuals_async")
        if not callable(current):
            return

        async def dashboard_visual_gate(*args, **kwargs):
            packages = await current(*args, **kwargs)
            self._visual_packages = list(packages or [])
            if not self._visual_packages:
                raise RuntimeError("Visual review could not start because no visual packages were returned.")

            self._visual_approval_event.clear()
            self._visual_approved = False
            self._visual_rejected = False
            self.update(
                "visual_approval",
                76,
                "The visuals are ready. Review them on the dashboard before rendering continues.",
            )
            self._visual_approval_event.wait(timeout=24 * 60 * 60)

            if self._visual_rejected:
                raise RuntimeError("Visual review was rejected. The production run was stopped before rendering.")
            if not self._visual_approved:
                raise RuntimeError("Visual review timed out. The production run was stopped before rendering.")
            self.update("render", 77, "Visuals approved. Rendering the final Short now.")
            return packages

        dashboard_visual_gate._dashboard_visual_gate_bound = True
        namespace["process_visuals_async"] = dashboard_visual_gate
        self.bot.process_visuals_async = dashboard_visual_gate
        self._dashboard_visual_gate_bound = True

    def approve_visuals(self) -> bool:
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False
        self._visual_approved = True
        self.update("render", 77, "Visuals approved. Rendering the final Short now.")
        self._visual_approval_event.set()
        return True

    def reject_visuals(self) -> bool:
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False
        self._visual_rejected = True
        self.update("error", 100, "Visual review rejected. Stopping this production run.")
        self._visual_approval_event.set()
        return True

    def snapshot(self):
        data = super().snapshot()
        with self._lock:
            data.update(
                {
                    "visual_packages": list(self._visual_packages),
                    "visual_review_required": data.get("stage") == "visual_approval",
                    "visual_review_approved": self._visual_approved,
                    "dashboard_logs": list(self._dashboard_logs),
                }
            )
        return data


def collect_channel_statistics(db_path: str) -> dict[str, Any]:
    """Read channel/factory performance already recorded in the vault database."""
    conn = sqlite3.connect(db_path)
    try:
        total_runs = int(conn.execute("SELECT COUNT(*) FROM vault").fetchone()[0] or 0)
        completed = int(
            conn.execute(
                "SELECT COUNT(*) FROM vault WHERE status='COMPLETED' OR (video_id IS NOT NULL AND video_id NOT IN ('PENDING_QC','REJECTED','READY_FOR_UPLOAD'))"
            ).fetchone()[0]
            or 0
        )
        total_views = int(conn.execute("SELECT COALESCE(SUM(views),0) FROM vault").fetchone()[0] or 0)
        avg_view_percentage = conn.execute(
            "SELECT AVG(avg_view_percentage) FROM vault WHERE avg_view_percentage IS NOT NULL"
        ).fetchone()[0]
        avg_ctr = conn.execute(
            "SELECT AVG(title_ctr) FROM vault WHERE title_ctr IS NOT NULL"
        ).fetchone()[0]

        by_format = conn.execute(
            """SELECT COALESCE(format_used,'Unknown') AS format, COUNT(*) AS runs,
                      COALESCE(SUM(views),0) AS views
               FROM vault GROUP BY COALESCE(format_used,'Unknown')
               ORDER BY runs DESC"""
        ).fetchall()
        by_language = conn.execute(
            """SELECT COALESCE(language_used,'Unknown') AS language, COUNT(*) AS runs,
                      COALESCE(SUM(views),0) AS views
               FROM vault GROUP BY COALESCE(language_used,'Unknown')
               ORDER BY runs DESC"""
        ).fetchall()
        recent = conn.execute(
            """SELECT date_used, topic, genre, format_used, language_used, views,
                      avg_view_percentage, title_ctr, status
               FROM vault
               ORDER BY COALESCE(date_used, created_at) DESC
               LIMIT 12"""
        ).fetchall()
    finally:
        conn.close()

    def rows_to_dict(rows, fields):
        return [dict(zip(fields, row)) for row in rows]

    return {
        "total_runs": total_runs,
        "completed_runs": completed,
        "total_views": total_views,
        "avg_view_percentage": float(avg_view_percentage) if avg_view_percentage is not None else None,
        "avg_ctr": float(avg_ctr) if avg_ctr is not None else None,
        "by_format": rows_to_dict(by_format, ["format", "runs", "views"]),
        "by_language": rows_to_dict(by_language, ["language", "runs", "views"]),
        "recent": rows_to_dict(
            recent,
            [
                "date_used",
                "topic",
                "genre",
                "format_used",
                "language_used",
                "views",
                "avg_view_percentage",
                "title_ctr",
                "status",
            ],
        ),
    }


def _run_synthetic_renderer_demo() -> dict[str, Any]:
    """Exercise the current premium subtitle/card renderers without network calls."""
    from subtitle_runtime import (
        create_glossy_logo_watermark,
        generate_readable_karaoke_clip,
        render_premium_top5_card,
    )

    temp_dir = tempfile.mkdtemp(prefix="vsf_demo_")
    background_path = os.path.join(temp_dir, "background.png")
    subtitle_path = os.path.join(temp_dir, "subtitle.png")
    top5_path = os.path.join(temp_dir, "top5.png")

    Image.new("RGB", (1080, 1920), (28, 42, 58)).save(background_path)
    generate_readable_karaoke_clip(
        [
            {"word": "This"},
            {"word": "is"},
            {"word": "a"},
            {"word": "premium"},
            {"word": "demo"},
        ],
        -1,
        None,
        1080,
        subtitle_path,
        bg_img_path=background_path,
    )
    top5 = render_premium_top5_card(
        Image.open(background_path),
        3,
        5,
        "This is a sample Top-5 glass card.",
    )
    top5.save(top5_path, "PNG")

    logo = None
    logo_candidates = [
        Path(getattr(__import__("ultimate_bot"), "BRAND_ASSETS_DIR", "")) / "logo.png",
        Path(getattr(__import__("ultimate_bot"), "BRAND_ASSETS_DIR", "")) / "channels4_profile.jpg",
    ]
    for candidate in logo_candidates:
        if candidate.exists():
            logo = create_glossy_logo_watermark(str(candidate), size=128)
            break
    logo_path = None
    if logo is not None:
        logo_path = os.path.join(temp_dir, "logo_badge.png")
        logo.save(logo_path, "PNG")

    return {
        "status": "PASS",
        "detail": "Premium subtitle, Top-5 card and glass-logo rendering completed without API calls.",
        "artifacts": {
            "subtitle": subtitle_path,
            "top5": top5_path,
            "logo": logo_path,
        },
    }


def run_demo_section(section: str) -> dict[str, Any]:
    """Run one dashboard-visible code section in a no-API demo harness."""
    from diagnostics_runtime import (
        _test_dashboard_architecture,
        _test_database,
        _test_environment,
        _test_imports,
        _test_provider_boundary,
        _test_runtime_bindings,
        _test_scene_branding,
        _test_script_and_audio,
        _test_visual_strategy,
    )

    checks: dict[str, Callable[[], str]] = {
        "imports": _test_imports,
        "environment": _test_environment,
        "database": _test_database,
        "visual_strategy": _test_visual_strategy,
        "scene_branding": _test_scene_branding,
        "script_audio": _test_script_and_audio,
        "runtime_bindings": _test_runtime_bindings,
        "provider_boundary": _test_provider_boundary,
        "dashboard_architecture": _test_dashboard_architecture,
    }

    if section == "premium_renderers":
        return _run_synthetic_renderer_demo()

    if section not in checks:
        return {"status": "FAIL", "detail": f"Unknown demo section: {section}"}

    try:
        return {"status": "PASS", "detail": checks[section]()}
    except Exception as exc:
        return {"status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"}


__all__ = [
    "DashboardWorkflowController",
    "collect_channel_statistics",
    "run_demo_section",
]
