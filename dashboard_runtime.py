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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from workflow_runtime import WorkflowController


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
    )
    reddit_records = sum(
        1 for item in evidence
        if str(item.get("collection_source") or "").strip().lower() in {"reddit", "social"}
    )

    return {
        "articles": int(candidate.get("event_article_count") or len(evidence) or 1),
        "independent_publishers": len(set(independent_publishers)),
        "publishers": sorted(set(str(item) for item in independent_publishers if str(item))),
        "independent_domains": len(set(domains)),
        "domains": sorted(set(str(item) for item in domains if str(item))),
        "official_records": official_records,
        "reddit_records": reddit_records,
        "latest_published_at": str(candidate.get("event_latest_published_at") or ""),
        "event_momentum": float(dimensions.get("event_momentum") or candidate.get("event_momentum_score") or 0.0),
        "freshness": float(dimensions.get("freshness") or candidate.get("freshness_score") or 0.0),
        "corroboration": float(dimensions.get("corroboration") or candidate.get("corroboration_bonus") or 0.0),
        "source_quality": float(dimensions.get("source_quality") or candidate.get("source_quality_score") or 0.0),
        "social_signal": float(dimensions.get("social_signal") or candidate.get("social_signal") or 0.0),
        "google_trends": float(dimensions.get("google_trends") or candidate.get("google_trends_signal") or 0.0),
        "channel_history": float(dimensions.get("channel_history") or 0.0),
        "originality": float(dimensions.get("originality") or candidate.get("originality_score") or 0.0),
        "visual_potential": float(dimensions.get("visual_potential") or candidate.get("visual_potential") or 0.0),
        "safety_risk": float(dimensions.get("safety_risk") or candidate.get("risk_signal_count") or 0.0),
        "sources": evidence[:6],
    }


def discover_ranked_topics(bot, web_config: dict[str, Any], conn, max_candidates: int = 12) -> list[dict[str, Any]]:
    """Dashboard-only discovery pool: preserve the factory ranker, but retain up to 12 ranked topics."""
    from story_ranker import (
        _cheap_filter,
        _cricket_relevance_pass,
        _deduplicate_stage,
        _editorial_score,
        _fact_source_stage,
        _load_history,
        _load_used_topics,
        _originality_stage,
        _requested_topic_pass,
        collect_high_recall_stories,
    )
    from workflow_runtime import _candidate_reason, _source_label, _story_key, _story_url

    max_candidates = max(3, min(12, int(max_candidates or 12)))
    fmt = str(web_config.get("format_mode", "regular"))
    category = str(web_config.get("category", ""))
    language = str(web_config.get("language", "english"))
    bot._active_web_config = dict(web_config)

    is_cricket = fmt == "cricket" or bool(web_config.get("cricket_pipeline"))
    if is_cricket:
        from workflow_runtime import CRICKET_CATEGORIES
        cricket_name = str(web_config.get("cricket_category", "AI-assisted top story in cricket"))
        cricket_cfg = CRICKET_CATEGORIES.get(cricket_name, CRICKET_CATEGORIES["AI-assisted top story in cricket"])
        genre_key = "sports_stories_of_day"
        genre_cfg = bot.CONTENT_CATEGORIES.get(genre_key, {})
        custom_q = str(web_config.get("requested_topic", "") or "").strip() or cricket_cfg["query"]
        custom_rss = cricket_cfg["rss"]
    else:
        genre_key = category or "national_global_affairs"
        genre_cfg = bot.CONTENT_CATEGORIES.get(genre_key)
        if not genre_cfg:
            raise ValueError(f"Unknown category: {genre_key}")
        custom_q = None
        custom_rss = None

    raw, social_titles = collect_high_recall_stories(
        bot,
        genre_key,
        genre_cfg,
        web_config.get("trend_keyword"),
        custom_q,
        custom_rss,
        ai_cricket=(
            genre_key == "sports_stories_of_day"
            and str(web_config.get("cricket_category", "")) == "AI-assisted top story in cricket"
        ),
    )

    requested_topic = str(web_config.get("requested_topic", "") or "").strip()
    relevance_filtered = []
    for candidate in raw:
        if not _cricket_relevance_pass(candidate, genre_key):
            continue
        if not _requested_topic_pass(candidate, requested_topic):
            continue
        relevance_filtered.append(candidate)

    ai_cricket = (
        genre_key == "sports_stories_of_day"
        and str(web_config.get("cricket_category", "")) == "AI-assisted top story in cricket"
    )
    rows = _load_history(conn)
    used_topics = _load_used_topics(conn)
    stage30 = _cheap_filter(relevance_filtered, max_items=30, max_age_hours=24 if ai_cricket else 48)
    stage15 = _deduplicate_stage(stage30, max_items=15)
    stage8 = _fact_source_stage(stage15, max_items=15)

    # The production ranker intentionally returns only its top 3. The
    # dashboard must expose a broader review pool, so retain additional
    # distinct candidates rather than truncating the factory selection.
    stage12 = _originality_stage(stage8, used_topics, max_items=max_candidates)
    if len(stage12) < min(max_candidates, len(stage8)):
        used = {id(item) for item in stage12}
        for item in stage8:
            if id(item) in used:
                continue
            if len(stage12) >= max_candidates:
                break
            item["originality_score"] = float(item.get("originality_score") or 5.0)
            item["originality_pass"] = True
            stage12.append(item)

    ranked = [
        _editorial_score(
            item,
            rows,
            category or genre_key,
            web_config.get("format_mode", "regular"),
            language,
            social_titles,
            ai_cricket,
        )
        for item in stage12
    ]
    ranked.sort(key=lambda item: float(item.get("candidate_score") or -9999.0), reverse=True)

    pool = ranked[:max_candidates]
    for rank, story in enumerate(pool, 1):
        story["discovery_rank"] = rank
        story["discovery_reason"] = _candidate_reason(story)
        story["source_label"] = _source_label(story)
        story["story_url"] = _story_url(story)
        story["story_key"] = _story_key(story)

    if len(pool) < 3:
        raise ValueError(
            f"Discovery produced only {len(pool)} dashboard candidate(s). At least 3 are required to start production."
        )
    print(
        f"   [Dashboard Discovery] Ranked topic pool ready: {len(pool)} candidate(s); dashboard shows 3 at a time.",
        flush=True,
    )
    return pool


class DashboardWorkflowController(WorkflowController):
    """WorkflowController with a dashboard-side visual approval checkpoint."""

    def __init__(self, bot):
        super().__init__(bot)
        self._visual_approval_event = threading.Event()
        self._visual_approved = False
        self._visual_rejected = False
        self._visual_packages: list[Any] = []
        self._dashboard_logs: list[str] = []
        self._activity_events: list[dict[str, Any]] = []
        self._audio_paths: list[str] = []
        self._last_dashboard_message = ""

    def reset(self):
        # Do not reset a live worker out from under its synchronization state.
        # The explicit Reject button is the supported way to stop a run paused
        # at visual review; Reset is safe only after the worker has exited.
        if getattr(self, "state", None) is not None and self.state.thread_alive:
            return
        self._visual_approval_event.clear()
        self._visual_approved = False
        self._visual_rejected = False
        self._visual_packages = []
        self._dashboard_logs = []
        self._activity_events = []
        self._audio_paths = []
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
                self._dashboard_logs = self._dashboard_logs[-24:]
                self._activity_events.append({
                    "time": datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S"),
                    "stage": str(stage or "factory").replace("_", " ").title(),
                    "message": friendly,
                })
                self._activity_events = self._activity_events[-24:]
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
                    "activity_events": list(self._activity_events),
                    "audio_paths": list(self._audio_paths),
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


def collect_live_channel_statistics(bot) -> dict[str, Any]:
    """Read current channel totals through the already-connected YouTube account."""
    try:
        import googleapiclient.discovery

        creds = bot.get_google_credentials()
        youtube = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
        response = (
            youtube.channels()
            .list(part="snippet,statistics", mine=True)
            .execute()
        )
        items = response.get("items") or []
        if not items:
            raise RuntimeError("The connected YouTube account returned no channel.")
        channel = items[0]
        statistics = channel.get("statistics") or {}
        snippet = channel.get("snippet") or {}
        return {
            "channel_title": str(snippet.get("title") or "Connected channel"),
            "subscriber_count": int(statistics.get("subscriberCount", 0) or 0),
            "video_count": int(statistics.get("videoCount", 0) or 0),
            "view_count": int(statistics.get("viewCount", 0) or 0),
            "hidden_subscriber_count": bool(statistics.get("hiddenSubscriberCount", False)),
        }
    except Exception as exc:
        return {
            "error": f"{type(exc).__name__}: {exc}",
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
    brand_root = Path(getattr(__import__("ultimate_bot"), "BRAND_ASSETS_DIR", ""))
    logo_candidates = [
        brand_root / "logo.png",
        brand_root / "channels4_profile.jpg",
        brand_root / "logo.png.jpg",
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


def factory_function_coverage() -> dict[str, Any]:
    """Return the explicit coverage audit used by Demo Factory and diagnostics."""
    from factory_function_coverage import collect_factory_function_coverage
    return collect_factory_function_coverage(Path(__file__).resolve().parent)


def run_demo_section(section: str) -> dict[str, Any]:
    """Run one dashboard-visible code section in a no-API demo harness."""
    from diagnostics_runtime import (
        _test_dashboard_architecture,
        _test_factory_function_coverage,
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
        "factory_function_coverage": _test_factory_function_coverage,
    }

    if section == "manual_visual_queries":
        try:
            from manual_visual_query_runtime import assign_manual_queries, parse_manual_visual_queries
            queries = parse_manual_visual_queries(
                "India Afghanistan cricket match; Shubman Gill batting; New Delhi cricket stadium"
            )
            scenes = [
                {"primary_entity": "India Afghanistan", "voiceover": "India and Afghanistan play the final match."},
                {"primary_entity": "Shubman Gill", "voiceover": "Shubman Gill leads India's batting."},
                {"primary_entity": "New Delhi", "voiceover": "The match is being played in New Delhi."},
            ]
            assignments = assign_manual_queries(scenes, queries)
            if len(assignments) != len(scenes) or any(not item.get("query") for item in assignments):
                raise AssertionError("Manual visual queries could not be assigned to all demo scenes.")
            return {
                "status": "PASS",
                "detail": "Semicolon-separated visual queries were parsed and intelligently assigned to the matching demo scenes.",
                "assignments": assignments,
            }
        except Exception as exc:
            return {"status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"}

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
    "discover_ranked_topics",
    "collect_channel_statistics",
    "collect_live_channel_statistics",
    "run_demo_section",
    "factory_function_coverage",
]
