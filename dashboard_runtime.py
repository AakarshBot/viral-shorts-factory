"""Dashboard-only orchestration helpers for the Viral Shorts Factory.

This module changes the dashboard experience without changing discovery, script,
audio, visual retrieval, rendering, provider adapters or upload implementations.
It adds only a dashboard-side visual review gate and presentation helpers.
"""
from __future__ import annotations

import os
import sqlite3
import sys
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



def _recent_topic_cooldown(conn, stories: list[dict[str, Any]], *, hours: int = 48) -> list[dict[str, Any]]:
    """Remove stories that substantially overlap topics used in the recent cooldown window."""
    if conn is None:
        return stories

    recent_topics: list[str] = []
    try:
        rows = conn.execute(
            "SELECT topic, COALESCE(date_used, created_at) FROM vault "
            "WHERE topic IS NOT NULL AND topic != ''"
        ).fetchall()
        now = datetime.now(timezone.utc)
        for topic, raw_date in rows:
            if not topic or not raw_date:
                continue
            value = raw_date if isinstance(raw_date, datetime) else str(raw_date).strip()
            if not value:
                continue
            try:
                when = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                try:
                    when = datetime.strptime(value[:10], "%Y-%m-%d")
                except ValueError:
                    continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            age_hours = (now - when.astimezone(timezone.utc)).total_seconds() / 3600.0
            if 0 <= age_hours <= hours:
                recent_topics.append(str(topic))
    except Exception as exc:
        print(f"   [Dashboard Discovery] Recent topic history unavailable: {type(exc).__name__}", flush=True)
        return stories

    if not recent_topics:
        return stories

    from story_ranker import _tokens, _topic_overlap

    kept: list[dict[str, Any]] = []
    excluded = 0
    for story in stories:
        title = str(story.get("title") or "").strip()
        current_tokens = _tokens(title)
        is_repeat = False
        for old_topic in recent_topics:
            overlap = _topic_overlap(title, old_topic)
            shared = len(current_tokens & _tokens(old_topic))
            if overlap >= 0.50 or (shared >= 3 and overlap >= 0.32):
                is_repeat = True
                break
        if is_repeat:
            story["discovery_rejection"] = "Recent topic cooldown (48 hours)"
            excluded += 1
            continue
        kept.append(story)

    if excluded:
        print(
            f"   [Dashboard Discovery] Recent topic cooldown removed {excluded} repeated candidate(s); "
            f"window={hours}h.",
            flush=True,
        )
    return kept




# Dashboard-only AI topic selection. This deliberately lives here so the
# factory's production CONTENT_CATEGORIES and format contracts stay unchanged.
AI_DISCOVERY_CATEGORY_KEYS = (
    "national_global_affairs",
    "technology",
    "business_finance",
    "entertainment",
    "sports_stories_of_day",
    "health_lifestyle",
    "viral_phenomenon",
)


def discover_ai_topics(bot, web_config: dict[str, Any], conn, max_candidates: int = 10) -> list[dict[str, Any]]:
    """Build a Top-10 current-topic list using one intentional query per useful genre."""
    from story_ranker import (
        _canonical_url,
        _candidate_reason,
        _cheap_filter,
        _deduplicate_stage,
        _editorial_score,
        _fact_source_stage,
        _gnews_items,
        _load_history,
        _load_used_topics,
        _official_feed_items,
        _originality_stage,
        _reddit_items,
        _rss_items,
        _source_label,
        _story_key,
        _story_url,
        _tokens,
        discover_event_pool,
    )

    max_candidates = max(3, min(10, int(max_candidates or 10)))
    rows = _load_history(conn)
    used_topics = _load_used_topics(conn)
    language = str(web_config.get("language", "english"))
    api_key = str(os.getenv("GNEWS_API_KEY") or getattr(bot, "GNEWS_API_KEY", "") or "").strip()
    raw: list[dict[str, Any]] = []

    for category in AI_DISCOVERY_CATEGORY_KEYS:
        cfg = bot.CONTENT_CATEGORIES.get(category) or {}
        query = str(cfg.get("gnews_q", "") or "").strip()
        if query:
            raw.extend(_gnews_items(query, api_key, category))
        rss_url = str(cfg.get("rss_url", "") or "").strip()
        if rss_url:
            raw.extend(_rss_items(rss_url, category))
        raw.extend(_official_feed_items(category, cfg))

    social_rows = _reddit_items("viral_phenomenon")
    raw.extend(social_rows)
    social_titles = [row.get("title", "") for row in social_rows]

    compact: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = _canonical_url(item.get("url") or item.get("link"))
        if not key:
            key = "title:" + " ".join(sorted(_tokens(item.get("title", ""))))
        if key and key not in seen:
            seen.add(key)
            compact.append(item)

    event_pool = discover_event_pool(
        query="India OR world OR technology OR business OR entertainment OR cricket",
        existing_articles=compact,
        timespan="48h",
        max_gdelt_records=75,
    )
    candidates = event_pool.get("events") or compact

    stage30 = _cheap_filter(candidates, max_items=40, max_age_hours=48)
    stage20 = _deduplicate_stage(stage30, max_items=20)
    stage20 = _recent_topic_cooldown(conn, stage20, hours=48)
    stage12 = _fact_source_stage(stage20, max_items=12)
    stage10 = _originality_stage(stage12, used_topics, max_items=max_candidates)

    ranked: list[dict[str, Any]] = []
    for item in stage10:
        category = str(
            item.get("primary_genre")
            or item.get("genre")
            or "national_global_affairs"
        )
        scored = _editorial_score(
            item,
            rows,
            category,
            "regular",
            language,
            social_titles,
            ai_cricket=False,
        )
        # _editorial_score already includes channel-history fit. Reuse its
        # normalized dimension for the dashboard instead of adding history twice.
        scored["channel_history_fit"] = float(
            (scored.get("discovery_dimensions") or {}).get("channel_history") or 0.0
        )
        scored["recommended_category"] = category if category in bot.CONTENT_CATEGORIES else "national_global_affairs"
        scored["recommended_format"] = "regular"
        scored["ai_recommendation"] = True
        ranked.append(scored)

    ranked.sort(key=lambda item: float(item.get("candidate_score") or -9999.0), reverse=True)
    pool = ranked[:max_candidates]
    for rank, item in enumerate(pool, 1):
        item["discovery_rank"] = rank
        item["discovery_reason"] = _candidate_reason(item)
        item["source_label"] = _source_label(item)
        item["story_url"] = _story_url(item)
        item["story_key"] = _story_key(item)
        item["ai_fit_summary"] = (
            f"Current momentum + freshness + source support + channel-history fit "
            f"({item.get('channel_history_fit', 0):.1f}/10)."
        )

    if len(pool) < 3:
        raise ValueError(f"AI discovery produced only {len(pool)} usable candidate(s). At least 3 are required.")
    print(
        f"   [AI Discovery] current intake={len(compact)} -> events={len(candidates)} -> "
        f"Top {len(pool)}; history used as a fit signal, not a repetition target.",
        flush=True,
    )
    return pool


def discover_ranked_topics(bot, web_config: dict[str, Any], conn, max_candidates: int = 20) -> list[dict[str, Any]]:
    """Dashboard discovery pool: return up to 20 ranked, distinct recent topics."""
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

    max_candidates = max(3, min(20, int(max_candidates or 20)))
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
    stage50 = _cheap_filter(relevance_filtered, max_items=50, max_age_hours=24 if ai_cricket else 48)
    stage30 = _deduplicate_stage(stage50, max_items=30)
    fresh_stage = _recent_topic_cooldown(conn, stage30, hours=48)
    stage25 = _fact_source_stage(fresh_stage, max_items=25)

    # Do not silently turn originality failures into passes. If fewer
    # candidates survive, return the smaller evidence-backed pool.
    stage20 = _originality_stage(stage25, used_topics, max_items=max_candidates)

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
        for item in stage20
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
        f"   [Dashboard Discovery] Ranked topic list ready: {len(pool)} candidate(s); "
        f"recent 48-hour repeats excluded.",
        flush=True,
    )
    return pool


class _DashboardStreamCapture:
    """Capture only the active factory worker's stdout/stderr without hiding it from the console."""
    def __init__(self, original):
        self.original = original
        self._lock = threading.Lock()
        self._sinks: dict[int, Callable[[str], None]] = {}

    def register(self, thread_id: int, sink: Callable[[str], None]) -> None:
        with self._lock:
            self._sinks[thread_id] = sink

    def unregister(self, thread_id: int) -> None:
        with self._lock:
            self._sinks.pop(thread_id, None)

    def write(self, data) -> int:
        written = self.original.write(data)
        if not data:
            return written
        with self._lock:
            sink = self._sinks.get(threading.get_ident())
        if sink is not None:
            try:
                sink(str(data))
            except Exception:
                pass
        return written

    def flush(self) -> None:
        self.original.flush()


_DASHBOARD_STDOUT = _DashboardStreamCapture(sys.stdout)
_DASHBOARD_STDERR = _DashboardStreamCapture(sys.stderr)
_DASHBOARD_STREAMS_INSTALLED = False


def _install_dashboard_stream_capture() -> None:
    global _DASHBOARD_STREAMS_INSTALLED
    if _DASHBOARD_STREAMS_INSTALLED:
        return
    if sys.stdout is not _DASHBOARD_STDOUT:
        _DASHBOARD_STDOUT.original = sys.stdout
        sys.stdout = _DASHBOARD_STDOUT
    if sys.stderr is not _DASHBOARD_STDERR:
        _DASHBOARD_STDERR.original = sys.stderr
        sys.stderr = _DASHBOARD_STDERR
    _DASHBOARD_STREAMS_INSTALLED = True


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
        self._console_lines: list[str] = []
        self._console_partial: str = ""
        self._last_dashboard_message = ""
        _install_dashboard_stream_capture()

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
        self._console_lines = []
        self._console_partial = ""
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

    def _capture_console(self, data: str) -> None:
        text = self._console_partial + str(data or "")
        text = text.replace("\r", "\n")
        parts = text.split("\n")
        self._console_partial = parts.pop() if parts else ""
        lines = [line.rstrip() for line in parts if line.strip()]
        if not lines:
            return
        with self._lock:
            self._console_lines.extend(lines)
            self._console_lines = self._console_lines[-160:]

    def console_lines(self) -> list[str]:
        with self._lock:
            lines = list(self._console_lines)
            if self._console_partial.strip():
                lines.append(self._console_partial.rstrip())
            return lines[-120:]

    def _install_production_wrappers(self):
        super()._install_production_wrappers()
        if getattr(self, "_dashboard_visual_gate_bound", False):
            return
        run_robot = getattr(self.bot, "run_robot", None)
        namespace = getattr(run_robot, "__globals__", None)
        if not isinstance(namespace, dict):
            return

        current_audio = namespace.get("generate_voiceover_and_timestamps")
        if callable(current_audio) and not getattr(current_audio, "_dashboard_audio_capture", False):
            async def dashboard_audio_capture(*args, **kwargs):
                result = await current_audio(*args, **kwargs)
                audio_paths = result[0] if isinstance(result, (tuple, list)) and result else result
                if isinstance(audio_paths, (list, tuple)):
                    with self._lock:
                        self._audio_paths = [
                            os.path.abspath(os.fspath(path))
                            for path in audio_paths
                            if path and os.path.isfile(os.fspath(path))
                        ]
                self.update("audio", 52, "Narration complete. Building visual package…")
                return result
            dashboard_audio_capture._dashboard_audio_capture = True
            namespace["generate_voiceover_and_timestamps"] = dashboard_audio_capture
            self.bot.generate_voiceover_and_timestamps = dashboard_audio_capture

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
        console_lines = self.console_lines()
        with self._lock:
            data.update(
                {
                    "visual_packages": list(self._visual_packages),
                    "visual_review_required": data.get("stage") == "visual_approval",
                    "visual_review_approved": self._visual_approved,
                    "dashboard_logs": list(self._dashboard_logs),
                    "activity_events": list(self._activity_events),
                    "audio_paths": list(self._audio_paths),
                    "console_lines": console_lines,
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
    "discover_ai_topics",
    "collect_channel_statistics",
    "collect_live_channel_statistics",
    "run_demo_section",
    "factory_function_coverage",
]
