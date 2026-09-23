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
    """Use the canonical posted-only discovery cooldown implementation."""
    from story_ranker import _recent_topic_cooldown as canonical_recent_topic_cooldown

    return canonical_recent_topic_cooldown(conn, stories, hours=hours)



def _merge_retained_topics(
    bot,
    web_config: dict[str, Any],
    conn,
    fresh_candidates: list[dict[str, Any]],
    retained_candidates: list[dict[str, Any]] | None,
    *,
    max_candidates: int,
) -> list[dict[str, Any]]:
    """Keep every unpublished selection visible; only completed uploads suppress it."""
    import story_ranker as sr
    from story_ranker import _load_uploaded_story_identities, _uploaded_story_match

    limit = max(1, int(max_candidates or 1))
    uploaded = _load_uploaded_story_identities(conn)

    retained = [
        dict(item)
        for item in (retained_candidates or [])
        if isinstance(item, dict)
        and str(item.get("title") or "").strip()
        and not _uploaded_story_match(item, uploaded)
    ]
    fresh = [
        dict(item)
        for item in (fresh_candidates or [])
        if isinstance(item, dict)
        and str(item.get("title") or "").strip()
        and not _uploaded_story_match(item, uploaded)
    ]

    output = []
    seen = set()

    def identity(item):
        return str(
            item.get("event_identity_key")
            or item.get("event_id")
            or item.get("story_key")
            or item.get("story_url")
            or item.get("url")
            or item.get("title")
            or ""
        ).strip().casefold()

    # A user-selected but unpublished headline has priority over a newly
    # rediscovered representation of the same event. This prevents a click from
    # making the headline disappear on the next dashboard refresh.
    for source, is_retained in ((retained, True), (fresh, False)):
        for item in source:
            key = identity(item)
            if key and key in seen:
                continue
            # Event IDs can legitimately change when a fresh run finds an extra
            # article, so also use a high semantic similarity threshold to stop
            # the same underlying headline reappearing beside its retained copy.
            if any(
                sr._story_theme_similarity(item, old) >= 0.86
                for old in output
                if isinstance(old, dict)
            ):
                continue
            if key:
                seen.add(key)
            if is_retained:
                item["retained_from_previous_run"] = True
            output.append(item)
            if len(output) >= limit:
                return output

    return output[:limit]

def discover_ai_topics(
    bot,
    web_config: dict[str, Any],
    conn,
    max_candidates: int = 60,
    retained_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build the AI dashboard portfolio through the same broad-recall topic desk."""
    from dashboard_topic_discovery_runtime import discover_dashboard_topics
    from story_ranker import _candidate_reason, _source_label, _story_key, _story_url

    max_candidates = max(1, min(60, int(max_candidates or 60)))
    requested_topic = str(web_config.get("requested_topic", "") or "").strip()
    configured_category = str(web_config.get("category", "") or "").strip().lower()
    language = str(web_config.get("language", "english"))

    ai_sports_mode = (
        str(web_config.get("discovery_mode", "") or "").strip().lower() == "ai_sports"
        or configured_category in {"sports", "ai_recommendation"}
    )

    if ai_sports_mode:
        genre_key = "sports"
        genre_cfg = dict(bot.CONTENT_CATEGORIES.get("sports", {}))
        target_category = "sports"
    else:
        genre_key = configured_category or "national_global_affairs"
        genre_cfg = dict(bot.CONTENT_CATEGORIES.get(genre_key, {}))
        if not genre_cfg:
            raise ValueError(f"Unknown category: {genre_key}")
        target_category = configured_category or genre_key

    ranked = discover_dashboard_topics(
        bot,
        genre_key,
        genre_cfg,
        conn=conn,
        requested_topic=requested_topic,
        custom_rss_url="",
        cricket_scope="",
        target_category=target_category,
        target_format=web_config.get("format_mode", "regular"),
        target_language=language,
        ai_cricket=False,
        max_candidates=max_candidates,
    )

    ranked = _merge_retained_topics(
        bot,
        web_config,
        conn,
        ranked,
        retained_candidates,
        max_candidates=max_candidates,
    )
    pool = ranked[:max_candidates]

    for rank, item in enumerate(pool, 1):
        item["discovery_rank"] = rank
        item["discovery_reason"] = item.get("discovery_reason") or _candidate_reason(item)
        item["source_label"] = item.get("source_label") or _source_label(item)
        item["story_url"] = item.get("story_url") or _story_url(item)
        item["story_key"] = item.get("story_key") or _story_key(item)
        item["recommended_category"] = item.get("recommended_category") or target_category
        item["recommended_format"] = "regular"
        item["ai_recommendation"] = True
        item["ai_fit_summary"] = (
            f"Current momentum + freshness + source support + channel-history fit."
        )

    print(
        f"   [AI Discovery] intake desk returned {len(pool)} topics for {genre_key}; "
        f"dashboard discovery v2 is active.",
        flush=True,
    )
    return pool


def discover_ranked_topics(
    bot,
    web_config: dict[str, Any],
    conn,
    max_candidates: int = 28,
    retained_candidates: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Dashboard discovery entry point using the dedicated broad-recall topic desk."""
    from story_ranker import _candidate_reason, _source_label, _story_key, _story_url
    from workflow_runtime import CRICKET_CATEGORIES

    max_candidates = max(1, min(60, int(max_candidates or 60)))
    fmt = str(web_config.get("format_mode", "regular"))
    category = str(web_config.get("category", ""))
    language = str(web_config.get("language", "english"))
    bot._active_web_config = dict(web_config)

    is_cricket = fmt == "cricket" or bool(web_config.get("cricket_pipeline"))
    cricket_name = ""
    requested_topic = str(web_config.get("requested_topic", "") or "").strip()

    if is_cricket:
        cricket_name = str(
            web_config.get("cricket_category", "AI-assisted top story in cricket")
        )
        cricket_cfg = CRICKET_CATEGORIES.get(
            cricket_name,
            CRICKET_CATEGORIES["AI-assisted top story in cricket"],
        )
        genre_key = "sports_stories_of_day"
        genre_cfg = dict(bot.CONTENT_CATEGORIES.get(genre_key, {}))

        if cricket_name == "India / Asia":
            genre_cfg["india_gnews_q"] = cricket_cfg["query"]
            genre_cfg["global_gnews_q"] = ""
            genre_cfg["gnews_q"] = cricket_cfg["query"]
        elif cricket_name == "Global":
            genre_cfg["india_gnews_q"] = ""
            genre_cfg["global_gnews_q"] = cricket_cfg["query"]
            genre_cfg["gnews_q"] = cricket_cfg["query"]

        custom_rss = cricket_cfg["rss"]
        # Cricket dashboard discovery must use the cricket-specific editorial
        # scorer even when the UI also carries a generic sports category value.
        target_category = genre_key
        ai_cricket = cricket_name == "AI-assisted top story in cricket"
    else:
        genre_key = category or "national_global_affairs"
        genre_cfg = bot.CONTENT_CATEGORIES.get(genre_key)
        if not genre_cfg:
            raise ValueError(f"Unknown category: {genre_key}")
        custom_rss = ""
        target_category = category or genre_key
        ai_cricket = False

    if is_cricket:
        from sports_topic_desk_runtime import discover_cricket_topics
        ranked = discover_cricket_topics(
            bot=bot,
            conn=conn,
            scope=cricket_name,
            requested_topic=requested_topic,
            max_candidates=60,
            retained_candidates=retained_candidates,
        )
    else:
        from dashboard_topic_discovery_runtime import discover_dashboard_topics
        ranked = discover_dashboard_topics(
            bot,
            genre_key,
            genre_cfg,
            conn=conn,
            requested_topic=requested_topic,
            custom_rss_url=custom_rss,
            cricket_scope=cricket_name,
            target_category=target_category,
            target_format=web_config.get("format_mode", "regular"),
            target_language=language,
            ai_cricket=ai_cricket,
            max_candidates=max_candidates,
        )

    ranked = _merge_retained_topics(
        bot,
        web_config,
        conn,
        ranked,
        retained_candidates,
        max_candidates=max_candidates,
    )
    pool = ranked[:max_candidates]

    for rank, story in enumerate(pool, 1):
        story["discovery_rank"] = rank
        story["discovery_reason"] = story.get("discovery_reason") or _candidate_reason(story)
        story["source_label"] = story.get("source_label") or _source_label(story)
        story["story_url"] = story.get("story_url") or _story_url(story)
        story["story_key"] = story.get("story_key") or _story_key(story)

    print(
        f"   [Dashboard Discovery] {len(pool)} diverse headline(s) returned by "
        f"{'cricket ' + cricket_name if is_cricket else genre_key} discovery v2.",
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
        self._script_visual_queries: list[str] = []
        self._visual_approved = False
        self._visual_rejected = False
        self._visual_packages: list[Any] = []
        self._visual_replacement_history: dict[int, list[dict[str, Any]]] = {}
        self._visual_search_options: dict[int, list[dict[str, Any]]] = {}
        self._visual_pool: list[dict[str, Any]] = []
        self._visual_search_groups: list[dict[str, Any]] = []
        self._visual_search_operation_lock = threading.Lock()
        self._visual_pool_crop_target: str = ""
        self._manual_gate_state = None
        self._manual_visual_review_complete_id = None
        self._dashboard_logs: list[str] = []
        self._activity_events: list[dict[str, Any]] = []
        self._audio_paths: list[str] = []
        self._console_lines: list[str] = []
        self._console_partial: str = ""
        self._console_capture_thread_id: int | None = None
        self._last_dashboard_message = ""
        _install_dashboard_stream_capture()

    def reset(self):
        # Do not reset a live worker out from under its synchronization state.
        # The explicit Reject button is the supported way to stop a run paused
        # at visual review; Reset is safe only after the worker has exited.
        if getattr(self, "state", None) is not None and self.state.thread_alive:
            return
        self._script_visual_queries = []
        self._visual_approved = False
        self._visual_rejected = False
        self._visual_packages = []
        self._visual_replacement_history = {}
        self._visual_search_options = {}
        self._visual_pool = []
        self._visual_search_groups = []
        self._visual_pool_crop_target = ""
        self._dashboard_logs = []
        self._activity_events = []
        self._audio_paths = []
        self._console_lines = []
        self._console_partial = ""
        self._last_dashboard_message = ""
        # The base controller deliberately reinstalls production wrappers for
        # each new run. Dashboard-specific wrappers must be eligible for the
        # same fresh binding rather than remaining marked as already installed.
        self._manual_gate_state = None
        self._manual_visual_review_complete_id = None
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

    def _worker_started(self) -> None:
        thread_id = threading.get_ident()
        self._console_capture_thread_id = thread_id
        _DASHBOARD_STDOUT.register(thread_id, self._capture_console)
        _DASHBOARD_STDERR.register(thread_id, self._capture_console)

    def _worker_finished(self) -> None:
        thread_id = self._console_capture_thread_id
        if thread_id is None:
            return
        _DASHBOARD_STDOUT.unregister(thread_id)
        _DASHBOARD_STDERR.unregister(thread_id)
        self._console_capture_thread_id = None

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

    def _ensure_manual_gate_state(self):
        """Create the shared manual gate state for both production and direct dashboard tests."""
        if not isinstance(self._manual_gate_state, dict):
            self._manual_gate_state = {
                "script_event": threading.Event(),
                "visual_event": threading.Event(),
                "script_submitted": False,
                "visual_approved": False,
                "visual_rejected": False,
            }
        return self._manual_gate_state

    def _prepare_production_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Install dashboard checkpoints into the core production call path."""
        self._manual_gate_state = None
        gate = self._ensure_manual_gate_state()
        gate["script_event"].clear()
        gate["visual_event"].clear()
        gate["script_submitted"] = False
        gate["visual_approved"] = False
        gate["visual_rejected"] = False
        config["manual_qc_required"] = True
        config["_dashboard_manual_control"] = True
        config["_manual_script_review_hook"] = self._manual_script_review_hook
        config["_manual_visual_review_hook"] = self._manual_visual_review_hook
        config["_manual_post_render_hook"] = self._manual_post_render_hook
        return config

    def _manual_script_review_hook(self, result: dict[str, Any]) -> dict[str, Any]:
        """Pause only for the user's per-slide manual visual-query check."""
        if not isinstance(result, dict):
            raise RuntimeError("Script review could not start because the script payload is invalid.")

        scenes = result.get("script") or []
        if not isinstance(scenes, list) or not scenes:
            raise RuntimeError("Script review could not start because no script scenes were returned.")

        gate = self._ensure_manual_gate_state()
        gate["script_event"].clear()
        gate["script_submitted"] = False

        with self._lock:
            self._script_visual_queries = [""] * len(scenes)
            self.state.script_data = result

        self.update(
            "script_review",
            40,
            f"The script is ready. Check {len(scenes)} slides for any missing manual image searches.",
        )
        gate["script_event"].wait(timeout=24 * 60 * 60)

        if not gate["script_submitted"]:
            raise RuntimeError(
                "Script review timed out or manual visual-query review was not submitted. "
                "The production run was stopped."
            )

        with self._lock:
            queries = list(self._script_visual_queries)
            live_script = self.state.script_data if isinstance(self.state.script_data, dict) else result

        script_scenes = live_script.get("script") or []
        if not isinstance(script_scenes, list) or len(script_scenes) != len(scenes):
            raise RuntimeError("Script review returned an invalid scene list.")

        for index, scene in enumerate(script_scenes):
            if not isinstance(scene, dict):
                raise RuntimeError(f"Script review returned an invalid scene at position {index + 1}.")
            query = queries[index] if index < len(queries) else ""
            if query:
                scene["manual_visual_query"] = query
                scene["manual_visual_query_source"] = "dashboard_slide"
            else:
                scene.pop("manual_visual_query", None)
                scene.pop("manual_visual_query_score", None)
                scene.pop("manual_visual_query_index", None)
                scene.pop("manual_visual_query_source", None)

        live_script["script"] = script_scenes
        self.update(
            "audio",
            42,
            "Script review complete. Creating the voiceover and preparing visuals.",
        )
        return live_script

    def _manual_visual_review_hook(self, packages: list[Any]) -> list[Any]:
        """Pause the core factory until every generated visual has been reviewed."""
        packages = list(packages or [])
        if not packages:
            raise RuntimeError("Visual review could not start because no visual packages were returned.")

        package_id = id(packages)
        if (
            self._manual_visual_review_complete_id == package_id
            and self._visual_approved
        ):
            return packages

        gate = self._ensure_manual_gate_state()

        self._visual_packages = packages
        script_data = self.state.script_data if isinstance(self.state.script_data, dict) else {}
        manual_pool = script_data.get("visual_manual_pool") if isinstance(script_data, dict) else []
        if not manual_pool:
            first_package = packages[0][0] if isinstance(packages[0], list) and packages[0] else packages[0]
            if isinstance(first_package, dict):
                manual_pool = first_package.get("visual_manual_pool") or []
        self._visual_pool = [
            dict(item) for item in (manual_pool or [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        self._visual_search_groups = []
        self._visual_pool_crop_target = ""
        gate["visual_event"].clear()
        gate["visual_approved"] = False
        gate["visual_rejected"] = False
        self._visual_approved = False
        self._visual_rejected = False

        self.update(
            "visual_approval",
            76,
            "The visuals are ready. Review them on the dashboard before rendering continues.",
        )
        gate["visual_event"].wait(timeout=24 * 60 * 60)

        if gate["visual_rejected"]:
            raise RuntimeError("Visual review was rejected. The production run was stopped before rendering.")
        if not gate["visual_approved"]:
            raise RuntimeError("Visual review timed out. The production run was stopped before rendering.")

        for package in packages:
            layer = package[0] if isinstance(package, list) and package else package
            if isinstance(layer, dict):
                layer["human_visual_approved"] = True

        self._manual_visual_review_complete_id = package_id
        self.update("render", 77, "Visuals approved. Rendering the final Short now.")
        return packages

    def _manual_post_render_hook(self, video_path: str) -> None:
        with self._lock:
            self.state.video_path = os.path.abspath(os.fspath(video_path)) if video_path else ""
        self.update("render", 94, "Video rendered and ready for final QC.")

    def _install_production_wrappers(self):
        # These wrappers preserve the dashboard's direct callable contract for
        # Streamlit/unit-test callers. The core run_robot hooks remain the
        # authoritative fail-closed checkpoints.
        super()._install_production_wrappers()

        self._ensure_manual_gate_state()

        run_robot = (
            getattr(self.bot, "_vsf_canonical_run_robot", None)
            or getattr(self.bot, "run_robot", None)
        )
        namespace = getattr(run_robot, "__globals__", None)
        if not isinstance(namespace, dict):
            return

        current_script = namespace.get("write_script")
        if callable(current_script) and not getattr(current_script, "_dashboard_script_review", False):
            def dashboard_script_review(*args, **kwargs):
                result = current_script(*args, **kwargs)
                if not isinstance(result, dict):
                    return result
                reviewed = self._manual_script_review_hook(result)
                if isinstance(reviewed, dict):
                    reviewed["_dashboard_script_review_complete"] = True
                return reviewed

            dashboard_script_review._dashboard_script_review = True
            dashboard_script_review._research_layer_live = bool(
                getattr(current_script, "_research_layer_live", False)
            )
            namespace["write_script"] = dashboard_script_review
            self.bot.write_script = dashboard_script_review

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

        current_visual = namespace.get("process_visuals_async")
        if callable(current_visual) and not getattr(current_visual, "_dashboard_visual_gate_bound", False):
            async def dashboard_visual_gate(*args, **kwargs):
                packages = await current_visual(*args, **kwargs)
                return self._manual_visual_review_hook(packages)

            dashboard_visual_gate._dashboard_visual_gate_bound = True
            namespace["process_visuals_async"] = dashboard_visual_gate
            self.bot.process_visuals_async = dashboard_visual_gate

        self._dashboard_visual_gate_bound = True

    def _locate_visual_pool_asset(self, asset_hash: str):
        target = str(asset_hash or "").strip()
        if not target:
            return None, None, None
        for position, item in enumerate(self._visual_pool):
            if isinstance(item, dict) and str(item.get("hash") or "").strip() == target:
                return "pool", position, item
        for group_index, group in enumerate(self._visual_search_groups):
            for item_index, item in enumerate(group.get("items") or []):
                if isinstance(item, dict) and str(item.get("hash") or "").strip() == target:
                    return f"search:{group_index}", item_index, item
        return None, None, None

    def _return_slide_visual_to_pool(self, layer: dict[str, Any], scene: dict[str, Any] | None = None) -> None:
        """Return the current verified slide image to the shared unused pool."""
        if not isinstance(layer, dict) or not bool(layer.get("visual_verified")):
            return
        source_path = str(layer.get("visual_original_path") or layer.get("image") or "").strip()
        if not source_path or not os.path.isfile(source_path):
            return
        provenance = dict(layer.get("asset_provenance") or {})
        source_url = str(layer.get("source_image_url") or provenance.get("url") or "").strip()
        selected_hash = str(layer.get("visual_selected_hash") or layer.get("bank_selected_hash") or "").strip()
        if not selected_hash:
            try:
                from visual_retrieval_runtime import _hash_image
                with open(source_path, "rb") as fh:
                    selected_hash = str(_hash_image(self.bot, fh.read()) or "").strip()
            except Exception:
                selected_hash = ""
        scene = scene if isinstance(scene, dict) else {}
        entry = {
            "path": source_path,
            "original_path": str(layer.get("visual_original_path") or source_path).strip(),
            "hash": selected_hash,
            "source": str(layer.get("source_type") or "visual").strip(),
            "query": str(layer.get("manual_visual_query") or layer.get("visual_query_used") or layer.get("bank_selected_query") or "").strip(),
            "visual_type": str(layer.get("visual_type") or scene.get("visual_type") or "").strip().upper(),
            "visual_genre": str(layer.get("visual_genre") or scene.get("visual_genre") or "").strip().upper(),
            "provenance": provenance,
            "source_image_url": source_url,
            "status": "previously-selected",
            "used": False,
            "assigned_slide": 0,
            "assigned_time": "",
        }
        with self._lock:
            for item in self._visual_pool:
                if not isinstance(item, dict):
                    continue
                item_hash = str(item.get("hash") or "").strip()
                item_path = str(item.get("path") or "").strip()
                item_original = str(item.get("original_path") or "").strip()
                item_url = str(item.get("source_image_url") or (item.get("provenance") or {}).get("url") or "").strip()
                if (
                    (selected_hash and item_hash == selected_hash)
                    or item_path == source_path
                    or item_original == source_path
                    or (source_url and item_url == source_url)
                ):
                    item.update(entry)
                    return
            self._visual_pool.insert(0, entry)
            self._visual_pool = self._visual_pool[:40]

    def search_visual_pool(self, replacement_query: str) -> tuple[bool, str]:
        """Fetch up to ten additional AI-checked images for the global QC pool."""
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False, "Visual review is no longer active."
        query = str(replacement_query or "").strip()
        if not query:
            return False, "Enter a search term."
        if not self._visual_search_operation_lock.acquire(blocking=False):
            return False, "A visual search is already running. Please wait for that search to finish."
        try:
            from visual_retrieval_runtime import collect_manual_visual_search, materialize_manual_visual_pool
            script_data = snapshot.get("script_data") or {}
            video_title = str(
                script_data.get("title")
                or (script_data.get("titles") or [""])[0]
                or ""
            ).strip()
            used_hashes: set[str] = set()
            used_source_image_urls: set[str] = set()
            packages = snapshot.get("visual_packages") or []
            for package in packages:
                layer = package[0] if isinstance(package, list) and package else package
                if not isinstance(layer, dict):
                    continue
                for candidate in (
                    layer.get("visual_selected_hash"),
                    layer.get("bank_selected_hash"),
                ):
                    if str(candidate or "").strip():
                        used_hashes.add(str(candidate).strip())
                source_image_url = str(layer.get("source_image_url") or "").strip()
                if source_image_url:
                    used_source_image_urls.add(source_image_url)
                image_path = str(layer.get("visual_original_path") or layer.get("image") or "").strip()
                if image_path and os.path.isfile(image_path):
                    try:
                        from visual_retrieval_runtime import _hash_image
                        with open(image_path, "rb") as fh:
                            image_hash = _hash_image(self.bot, fh.read())
                        if image_hash:
                            used_hashes.add(image_hash)
                    except Exception:
                        pass
            for item in self._visual_pool:
                if isinstance(item, dict) and str(item.get("hash") or "").strip():
                    used_hashes.add(str(item.get("hash")).strip())
            for group in self._visual_search_groups:
                for item in group.get("items") or []:
                    if isinstance(item, dict) and str(item.get("hash") or "").strip():
                        used_hashes.add(str(item.get("hash")).strip())

            import visual_runtime

            result = collect_manual_visual_search(
                visual_runtime,
                self.bot,
                query,
                video_title=video_title,
                used_hashes=used_hashes,
                used_source_image_urls=used_source_image_urls,
                search_round=(
                    sum(
                        1
                        for group in self._visual_search_groups
                        if str(group.get("query") or "").strip().casefold()
                        == query.casefold()
                    )
                    + 1
                ),
            )
            assets = list(result.get("assets") or [])
            materialized = materialize_manual_visual_pool(
                self.bot,
                assets,
                pool_id=f"search_{len(self._visual_search_groups) + 1}_{abs(hash(query)) & 0xfffffff}",
            )
            group_id = f"search-{len(self._visual_search_groups) + 1}"
            for item in materialized:
                item["search_group_id"] = group_id
                item["used"] = False
                item["assigned_slide"] = 0
            group = {
                "id": group_id,
                "query": query,
                "items": materialized,
                "target": 10,
            }
            with self._lock:
                self._visual_search_groups.append(group)
                self._visual_approved = False
                self._visual_rejected = False

            count = len(materialized)
            if count == 10:
                message = f"Found 10 new AI-checked images for '{query}'."
            elif count:
                message = f"Found {count} new AI-checked images for '{query}'; no error was raised because the configured sources were exhausted."
            else:
                message = f"No new AI-checked images were returned for '{query}'. Try a different query."
            self.update("visual_approval", 76, message)
            return True, message
        except Exception as exc:
            return False, f"New visual search failed safely: {type(exc).__name__}: {exc}"
        finally:
            self._visual_search_operation_lock.release()

    def assign_visual_pool_asset(self, asset_hash: str, visual_index: int) -> tuple[bool, str]:
        """Assign one unused pool image to exactly one slide; it cannot be reused elsewhere."""
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False, "Visual review is no longer active."
        try:
            index = int(visual_index)
        except (TypeError, ValueError):
            return False, "Invalid slide number."

        packages = snapshot.get("visual_packages") or []
        script_data = snapshot.get("script_data") or {}
        scenes = script_data.get("script") if isinstance(script_data, dict) else None
        if index < 1 or index > len(packages) or not isinstance(scenes, list) or index > len(scenes):
            return False, "That slide is no longer available."

        origin, position, selected = self._locate_visual_pool_asset(asset_hash)
        if selected is None:
            return False, "That image is no longer available."
        if bool(selected.get("used")):
            assigned = int(selected.get("assigned_slide") or 0)
            return False, f"That image is already assigned to slide {assigned}."

        if not str(selected.get("path") or "").strip() or not os.path.isfile(str(selected.get("path") or "").strip()):
            return False, "That image is no longer available on the dashboard host."

        layer = packages[index - 1][0] if isinstance(packages[index - 1], list) and packages[index - 1] else packages[index - 1]
        if not isinstance(layer, dict):
            return False, "The selected slide is invalid."

        backup_bank = list(layer.get("visual_asset_bank") or [])
        layer["visual_asset_bank"] = [dict(selected)]
        try:
            ok, message = self.replace_visual_from_bank(index, 1)
        finally:
            layer["visual_asset_bank"] = backup_bank
        if not ok:
            return False, message

        with self._lock:
            if origin == "pool":
                live_item = self._visual_pool[position]
            else:
                group_index = int(str(origin).split(":", 1)[1])
                live_item = self._visual_search_groups[group_index]["items"][position]
            live_item["used"] = True
            live_item["assigned_slide"] = index
            live_item["assigned_time"] = datetime.now(timezone.utc).isoformat()
            if origin == "pool":
                self._visual_pool[position] = live_item
            else:
                self._visual_search_groups[group_index]["items"][position] = live_item

            live_package = self._visual_packages[index - 1]
            live_layer = live_package[0] if isinstance(live_package, list) and live_package else live_package
            if isinstance(live_layer, dict):
                live_layer["visual_asset_bank"] = []
                live_layer["visual_verification_source"] = (
                    "manual_qc" if str(live_item.get("status") or "") == "new-search" else "identity_ai"
                )
                live_layer["visual_original_path"] = str(
                    live_item.get("original_path") or live_item.get("path") or ""
                ).strip()
                live_layer["visual_selected_hash"] = str(live_item.get("hash") or "").strip()
                live_layer["source_image_url"] = str(live_item.get("source_image_url") or "").strip()

            self._visual_approved = False
            self._visual_rejected = False

        self.update(
            "visual_approval",
            76,
            f"Image assigned to slide {index}. It is now locked from reuse on another slide.",
        )
        return True, f"Image assigned to slide {index}."

    def crop_visual_pool_asset(
        self,
        asset_hash: str,
        crop_box: dict[str, Any],
        crop_mode: str = "shorts",
    ) -> tuple[bool, str]:
        """Apply a dashboard crop to a pool image without changing its identity record."""
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False, "Visual review is no longer active."

        origin, position, asset = self._locate_visual_pool_asset(asset_hash)
        if asset is None:
            return False, "That image is no longer available."
        source_path = str(asset.get("original_path") or asset.get("path") or "").strip()
        if not source_path or not os.path.isfile(source_path):
            return False, "That image is no longer available on the dashboard host."

        try:
            left = int(crop_box.get("left", 0))
            top = int(crop_box.get("top", 0))
            width = int(crop_box.get("width", 0))
            height = int(crop_box.get("height", 0))
        except (TypeError, ValueError, AttributeError):
            return False, "The selected crop area is invalid."

        try:
            source = Image.open(source_path).convert("RGB")
            right = left + width
            bottom = top + height
            if width < 2 or height < 2 or left < 0 or top < 0 or right > source.width or bottom > source.height:
                return False, "The selected crop area is outside the image."
            cropped = _manual_crop_box_to_shorts(
                source,
                {"left": left, "top": top, "width": width, "height": height},
                free_size=str(crop_mode or "").strip().casefold() == "free",
            )
            crop_key = hashlib.sha1(f"{asset.get('hash','')}:{left}:{top}:{width}:{height}".encode("utf-8")).hexdigest()[:16]
            target_path = os.path.join(self.bot.ASSETS_DIR, f"visual_pool_crop_{crop_key}.jpg")
            cropped.save(target_path, "JPEG", quality=95)
        except Exception as exc:
            return False, f"Crop could not be applied safely: {type(exc).__name__}: {exc}"

        with self._lock:
            if origin == "pool":
                live_item = self._visual_pool[position]
            else:
                group_index = int(str(origin).split(":", 1)[1])
                live_item = self._visual_search_groups[group_index]["items"][position]
            live_item["original_path"] = source_path
            live_item["path"] = target_path
            live_item["cropped"] = True
            live_item["crop_mode"] = "free" if str(crop_mode or "").strip().casefold() == "free" else "shorts"
            live_item["crop_box"] = {
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }
        self.update("visual_approval", 76, "Crop saved for the selected pool image.")
        return True, "Crop saved."

    def submit_script_review(self, reviewed_script: dict[str, Any]) -> tuple[bool, str]:
        """Validate optional human edits, then release the paused script-review gate."""
        snapshot = self.snapshot()
        if snapshot.get("stage") != "script_review":
            return False, "Script review is no longer active."

        current = snapshot.get("script_data") or {}
        original_scenes = current.get("script") if isinstance(current, dict) else None
        edited_scenes = reviewed_script.get("script") if isinstance(reviewed_script, dict) else None
        if not isinstance(original_scenes, list) or not original_scenes:
            return False, "The current script is unavailable."
        if not isinstance(edited_scenes, list) or len(edited_scenes) != len(original_scenes):
            return False, "Script edits cannot change the number of scenes during this review."

        candidate = dict(current)
        candidate["script"] = []
        for index, original_scene in enumerate(original_scenes):
            if not isinstance(original_scene, dict):
                return False, f"Scene {index + 1} is malformed."
            incoming_scene = edited_scenes[index] if isinstance(edited_scenes[index], dict) else {}
            updated_scene = dict(original_scene)
            updated_scene["voiceover"] = str(
                incoming_scene.get("voiceover", original_scene.get("voiceover") or "")
                or ""
            ).strip()
            candidate["script"].append(updated_scene)

        if isinstance(reviewed_script.get("titles"), list):
            candidate["titles"] = [
                str(title or "").strip()
                for title in reviewed_script.get("titles", [])
            ]
        if "editorial_angle" in reviewed_script:
            candidate["editorial_angle"] = str(
                reviewed_script.get("editorial_angle") or ""
            ).strip()

        try:
            selected_index = int(
                reviewed_script.get(
                    "recommended_title_index",
                    current.get("recommended_title_index", 1),
                )
            )
        except (TypeError, ValueError):
            selected_index = 1
        titles = candidate.get("titles") or []
        if not isinstance(titles, list) or not titles:
            return False, "At least one title candidate is required."
        if selected_index < 1 or selected_index > len(titles):
            return False, f"Title choice must be between 1 and {len(titles)}."
        candidate["recommended_title_index"] = selected_index

        try:
            from script_runtime import (
                assess_release_structure,
                check_script_originality,
                clean_script_data,
                rank_title_candidates,
                validate_content_density,
            )

            story_data = dict(snapshot.get("selected_story") or {})
            for key in (
                "research_evidence_text",
                "research_evidence_pack",
                "research_sources",
                "research_bundle",
            ):
                if key in current:
                    story_data[key] = current[key]

            cleaned, diagnostics = clean_script_data(
                candidate,
                story_data,
                snapshot.get("format_mode") or "regular",
            )
            valid, reason = validate_content_density(
                cleaned,
                story_data,
                snapshot.get("format_mode") or "regular",
            )
            if valid:
                valid, reason, _assessment = assess_release_structure(
                    cleaned,
                    snapshot.get("format_mode") or "regular",
                )
            if not valid:
                return False, f"Script edits need attention: {reason}"

            try:
                from audio_direction_runtime import choose_delivery_profile
                from script_runtime import classify_narration_duration, estimate_narration_duration

                delivery_profile = str(
                    cleaned.get("delivery_profile")
                    or choose_delivery_profile(self.bot, cleaned)
                    or cleaned.get("persona_used")
                    or "LISTICLE HOST"
                ).strip()
                profile = getattr(self.bot, "PERSONA_PROFILES", {}).get(
                    delivery_profile.upper(),
                    getattr(self.bot, "PERSONA_PROFILES", {}).get("LISTICLE HOST", {}),
                )
                duration_estimate = estimate_narration_duration(cleaned, profile)
                cleaned["delivery_profile"] = delivery_profile
                cleaned["estimated_duration_seconds"] = duration_estimate["seconds"]
                cleaned["estimated_duration_word_count"] = duration_estimate["word_count"]
                cleaned["estimated_duration_effective_wpm"] = duration_estimate["effective_wpm"]
                cleaned["duration_band"] = classify_narration_duration(
                    duration_estimate["seconds"]
                )
            except Exception as exc:
                return False, f"Script edits need attention: duration check failed: {type(exc).__name__}: {exc}"

            if float(cleaned.get("estimated_duration_seconds") or 0.0) >= 30.0:
                return False, (
                    "Script edits need attention: edited narration exceeds the 30-second limit "
                    f"({float(cleaned.get('estimated_duration_seconds')):.1f}s). "
                    "Shorten the edited narration before approving."
                )

            rank_title_candidates(cleaned, story_data)
            cleaned_titles = cleaned.get("titles") or []
            if not isinstance(cleaned_titles, list) or len(cleaned_titles) != 3:
                return False, "Script review requires exactly three usable title candidates."
            if selected_index < 1 or selected_index > len(cleaned_titles):
                return False, "The selected title is no longer valid after title cleanup."
            cleaned["recommended_title_index"] = selected_index

            original_voice = [
                str(scene.get("voiceover") or "").strip()
                for scene in original_scenes
                if isinstance(scene, dict)
            ]
            revised_voice = [
                str(scene.get("voiceover") or "").strip()
                for scene in candidate.get("script") or []
                if isinstance(scene, dict)
            ]
            voice_changed = original_voice != revised_voice
            if voice_changed:
                originality = check_script_originality(cleaned, story_data)
                if not originality.get("passed"):
                    return False, "Edited narration is too close to source wording. Rephrase the affected lines before approving."
                cleaned["originality_overlap"] = originality

            cleaned["human_script_reviewed"] = True
            cleaned["human_script_edit_applied"] = voice_changed
            with self._lock:
                self.state.script_data = cleaned
                self._script_visual_queries = list(
                    self._script_visual_queries
                    or (snapshot.get("script_visual_queries") or [])
                )
                gate = self._manual_gate_state
                if isinstance(gate, dict):
                    gate["script_submitted"] = True

            self.update(
                "audio",
                42,
                "Script review approved. Creating the voiceover and preparing visuals.",
            )
            if isinstance(gate, dict):
                gate["script_event"].set()
            return True, "Script approved and the production pipeline is continuing."
        except Exception as exc:
            return False, f"Script review could not be completed safely: {type(exc).__name__}: {exc}"

    def submit_script_visual_queries(self, queries: list[str]) -> bool:
        snapshot = self.snapshot()
        if snapshot.get("stage") != "script_review":
            return False

        script_data = snapshot.get("script_data") or {}
        scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
        if not isinstance(scenes, list) or not scenes:
            return False

        cleaned = [str(query or "").strip() for query in list(queries or [])]
        cleaned = (cleaned + [""] * len(scenes))[:len(scenes)]
        with self._lock:
            gate = self._manual_gate_state
            if isinstance(gate, dict):
                gate["script_submitted"] = True
            self._script_visual_queries = cleaned
            live_script = self.state.script_data
            if isinstance(live_script, dict) and isinstance(live_script.get("script"), list):
                for index, scene in enumerate(live_script["script"]):
                    if not isinstance(scene, dict):
                        continue
                    query = cleaned[index] if index < len(cleaned) else ""
                    if query:
                        scene["manual_visual_query"] = query
                        scene["manual_visual_query_source"] = "dashboard_slide"
                    else:
                        scene.pop("manual_visual_query", None)
                        scene.pop("manual_visual_query_score", None)
                        scene.pop("manual_visual_query_index", None)
                        scene.pop("manual_visual_query_source", None)
        self.update(
            "audio",
            42,
            "Slide queries saved. Creating the voiceover and preparing visuals.",
        )
        gate = self._manual_gate_state
        if isinstance(gate, dict):
            gate["script_event"].set()
        return True

    def approve_visuals(self) -> bool:
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False

        packages = snapshot.get("visual_packages") or []
        unresolved = []
        for index, package in enumerate(packages, 1):
            layer = package[0] if isinstance(package, list) and package else package
            if not isinstance(layer, dict):
                unresolved.append(index)
                continue
            image_path = str(layer.get("image") or "").strip()
            if (
                not image_path
                or not os.path.isfile(image_path)
                or not bool(layer.get("visual_verified"))
                or bool(layer.get("visual_qc_blocked"))
            ):
                unresolved.append(index)

        if unresolved:
            return False

        self._visual_approved = True
        gate = self._manual_gate_state
        if isinstance(gate, dict):
            gate["visual_approved"] = True
            gate["visual_event"].set()
        return True


    def search_visual_options(self, visual_index: int, replacement_query: str) -> tuple[bool, str]:
        """Search the exact manual query and retain up to ten AI-checked choices with no minimum."""
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False, "Visual review is no longer active."

        try:
            index = int(visual_index)
        except (TypeError, ValueError):
            return False, "Invalid visual number."

        query = str(replacement_query or "").strip()
        packages = snapshot.get("visual_packages") or []
        script_data = snapshot.get("script_data") or {}
        scenes = script_data.get("script") if isinstance(script_data, dict) else None
        if (
            index < 1
            or index > len(packages)
            or not isinstance(scenes, list)
            or index > len(scenes)
        ):
            return False, "That visual is no longer available."
        if not query:
            return False, "Enter a search term for this visual."

        scene = scenes[index - 1]
        if not isinstance(scene, dict):
            return False, "The selected slide is invalid."

        try:
            from visual_retrieval_runtime import (
                _hash_image,
                collect_manual_visual_options,
                materialize_manual_visual_pool,
            )
            import visual_runtime

            used_hashes: set[str] = set()
            used_source_pages: set[str] = set()
            for package in packages:
                layer = package[0] if isinstance(package, list) and package else package
                if not isinstance(layer, dict):
                    continue

                source_page = str(layer.get("source_page_url") or "").strip().casefold()
                if source_page:
                    used_source_pages.add(source_page.rstrip("/"))
                provenance_url = str(
                    (layer.get("asset_provenance") or {}).get("url") or ""
                ).strip().casefold()
                if provenance_url.startswith(("http://", "https://")):
                    used_source_pages.add(provenance_url.rstrip("/"))

                for prior_option in (layer.get("visual_search_options") or []):
                    if not isinstance(prior_option, dict):
                        continue
                    prior_hash = str(prior_option.get("hash") or "").strip()
                    if prior_hash:
                        used_hashes.add(prior_hash)
                    prior_page = str(prior_option.get("source_page_url") or "").strip().casefold()
                    if prior_page:
                        used_source_pages.add(prior_page.rstrip("/"))

                for bank_item in (layer.get("visual_asset_bank") or []):
                    if not isinstance(bank_item, dict):
                        continue
                    bank_hash = str(bank_item.get("hash") or "").strip()
                    if bank_hash:
                        used_hashes.add(bank_hash)
                    bank_page = str(bank_item.get("source_page_url") or "").strip().casefold()
                    if bank_page:
                        used_source_pages.add(bank_page.rstrip("/"))

                image_path = str(layer.get("image") or "").strip()
                if image_path and os.path.isfile(image_path):
                    try:
                        with open(image_path, "rb") as fh:
                            image_hash = _hash_image(self.bot, fh.read())
                        if image_hash:
                            used_hashes.add(image_hash)
                    except Exception:
                        pass

            video_title = str(
                script_data.get("title")
                or (script_data.get("titles") or [""])[0]
                or ""
            ).strip()
            attempt = len(self._visual_replacement_history.get(index, [])) + 1
            result = collect_manual_visual_options(
                visual_runtime,
                self.bot,
                scene,
                query,
                video_title=video_title,
                used_hashes=used_hashes,
                used_source_pages=used_source_pages,
                min_options=0,
                max_options=10,
                search_round=attempt,
            )
            assets = list(result.get("assets") or [])
            # Zero is a valid result. There is no minimum threshold for a
            # dashboard search; useful images are retained up to the ten-image cap.
            if not assets:
                with self._lock:
                    self._visual_search_options.pop(index, None)
                return (
                    True,
                    f"No AI-checked images were found for '{query}'. Try a different query.",
                )

            pool_id = hashlib.sha256(
                f"qc:{index}:{query}:{attempt}".encode("utf-8")
            ).hexdigest()[:16]
            options = materialize_manual_visual_pool(
                self.bot,
                assets[:10],
                pool_id=pool_id,
            )
            if not options:
                return (
                    True,
                    f"No AI-checked images could be prepared for '{query}'. Try a different query.",
                )

            with self._lock:
                self._visual_search_options[index] = [dict(item) for item in options[:10]]
                live_packages = self._visual_packages
                if 1 <= index <= len(live_packages):
                    live_layer = (
                        live_packages[index - 1][0]
                        if isinstance(live_packages[index - 1], list) and live_packages[index - 1]
                        else live_packages[index - 1]
                    )
                    if isinstance(live_layer, dict):
                        live_layer["visual_search_options"] = [dict(item) for item in options[:10]]

            self.update(
                "visual_approval",
                76,
                f"Found {len(options)} AI-checked alternatives for visual {index}. Choose one before continuing.",
            )
            return True, f"Found {len(options)} AI-checked alternatives for visual {index}."

        except Exception as exc:
            return False, f"Visual option search failed: {type(exc).__name__}: {exc}"

    def replace_visual_from_search_option(self, visual_index: int, option_index: int) -> tuple[bool, str]:
        """Replace a visual with one of the AI-checked manual-query choices."""
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False, "Visual review is no longer active."

        try:
            index = int(visual_index)
            selected_index = int(option_index)
        except (TypeError, ValueError):
            return False, "Invalid visual or search option number."

        packages = snapshot.get("visual_packages") or []
        if index < 1 or index > len(packages):
            return False, "That visual is no longer available."

        layer = packages[index - 1][0] if isinstance(packages[index - 1], list) and packages[index - 1] else packages[index - 1]
        if not isinstance(layer, dict):
            return False, "The selected visual package is invalid."

        options = [
            item for item in (layer.get("visual_search_options") or [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        if selected_index < 1 or selected_index > len(options):
            return False, "That search option is no longer available."

        # Reuse the existing verified-bank rendering/replacement path so the
        # selected image is handled exactly like any other reviewed bank asset.
        original_bank = list(layer.get("visual_asset_bank") or [])
        layer["visual_asset_bank"] = options
        ok = False
        message = "Search option replacement failed."
        try:
            ok, message = self.replace_visual_from_bank(index, selected_index)
        finally:
            current = self._visual_packages[index - 1] if index - 1 < len(self._visual_packages) else None
            current_layer = current[0] if isinstance(current, list) and current else current
            if isinstance(current_layer, dict):
                current_layer.pop("visual_search_options", None)
                self._visual_search_options.pop(index, None)
                # Preserve any pre-existing verified bank alternatives that were
                # not part of this one-off manual search.
                if ok and original_bank:
                    existing = list(current_layer.get("visual_asset_bank") or [])
                    seen_paths = {
                        str(item.get("path") or "").strip()
                        for item in existing
                        if isinstance(item, dict)
                    }
                    for item in original_bank:
                        if not isinstance(item, dict):
                            continue
                        path = str(item.get("path") or "").strip()
                        if path and path not in seen_paths:
                            existing.append(item)
                            seen_paths.add(path)
                    current_layer["visual_asset_bank"] = existing[:10]
                elif not ok:
                    current_layer["visual_asset_bank"] = original_bank
        return ok, message

    def replace_visual(self, visual_index: int, replacement_query: str) -> tuple[bool, str]:
        """Replace exactly one reviewed visual while the production worker is paused."""
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False, "Visual review is no longer active."

        try:
            index = int(visual_index)
        except (TypeError, ValueError):
            return False, "Invalid visual number."

        query = str(replacement_query or "").strip()
        packages = snapshot.get("visual_packages") or []
        script_data = snapshot.get("script_data") or {}
        scenes = script_data.get("script") if isinstance(script_data, dict) else None
        if index < 1 or index > len(packages) or not isinstance(scenes, list) or index > len(scenes):
            return False, "That visual is no longer available."
        if not query:
            return False, "Enter a search term for this visual."

        scene = scenes[index - 1]
        if not isinstance(scene, dict):
            return False, "The selected slide is invalid."

        # Work on a copy. A failed replacement must leave the current approved
        # candidate untouched so the reviewer never loses a usable visual.
        import copy
        replacement_scene = copy.deepcopy(scene)

        try:
            from visual_query_entities_runtime import search_slide_visual
            from visual_quality_runtime import fit_visual_image
            import visual_runtime
            from branding_runtime import source_credit_for_type
            from visual_retrieval_runtime import _hash_image
            from visual_strategy_runtime import classify_scene

            bot = self.bot
            video_title = str(
                script_data.get("title")
                or (script_data.get("titles") or [""])[0]
                or ""
            ).strip()
            category = str(
                replacement_scene.get("sport_or_topic_category")
                or ""
            ).lower()

            used_urls: set[str] = set()
            used_hashes: set[str] = set()
            for package in packages:
                layer = package[0] if isinstance(package, list) and package else package
                if not isinstance(layer, dict):
                    continue
                source_url = str(layer.get("source_image_url") or "").strip()
                if source_url:
                    used_urls.add(source_url)
                image_path = str(layer.get("image") or "").strip()
                if image_path and os.path.isfile(image_path):
                    try:
                        with open(image_path, "rb") as fh:
                            image_hash = _hash_image(bot, fh.read())
                        if image_hash:
                            used_hashes.add(image_hash)
                    except Exception:
                        pass

            replacement_scene["manual_visual_query"] = query
            replacement_scene["manual_visual_query_source"] = "dashboard_replacement"
            bg_img, used_ai, source_type = search_slide_visual(
                visual_runtime,
                bot,
                replacement_scene,
                category,
                used_urls,
                used_hashes,
                video_title,
                manual_query=query,
            )

            if bg_img is None:
                return False, "No replacement visual was returned."

            format_mode = str(
                getattr(bot, "_active_web_config", {}).get("format_mode", "regular")
            ).lower()
            language_cfg = {}
            active_config = getattr(bot, "_active_web_config", {}) or {}
            language_key = str(active_config.get("language") or "english")
            language_cfg = getattr(bot, "LANGUAGES", {}).get(language_key, {})
            target_size = (1080, 1920)
            font_choice = language_cfg.get("font")
            bg_img = fit_visual_image(
                bg_img,
                target_size,
                str(replacement_scene.get("visual_genre") or "GENERAL_CONTEXT"),
            ).convert("RGBA")

            try:
                visual_type = classify_scene(replacement_scene, category)
            except Exception:
                visual_type = str(replacement_scene.get("visual_type") or "GENERAL_CONTEXT")

            history = self._visual_replacement_history.setdefault(index, [])
            attempt = len(history) + 1
            old_layer = packages[index - 1][0] if isinstance(packages[index - 1], list) and packages[index - 1] else packages[index - 1]
            old_path = str(old_layer.get("image") or "").strip() if isinstance(old_layer, dict) else ""
            old_query = str(old_layer.get("manual_visual_query") or "").strip() if isinstance(old_layer, dict) else ""

            if format_mode == "top5" and index == 1:
                rendered = visual_runtime._render_image_slide(
                    bot,
                    bg_img,
                    video_title or replacement_scene.get("voiceover", "Top 5"),
                    "TODAY'S TOP 5",
                    font_choice,
                )
            elif format_mode == "top5":
                clean = re.sub(
                    r"(number\s*\d+|story\s*#?\d+|#\d+)",
                    "",
                    str(replacement_scene.get("voiceover", "")),
                    flags=re.IGNORECASE,
                ).strip()
                rendered = bot.render_top5_card(
                    bg_img,
                    max(1, 6 - index),
                    5,
                    clean or replacement_scene.get("voiceover", ""),
                    font_choice=font_choice,
                )
            else:
                rendered = bg_img

            replacement_path = os.path.join(
                bot.ASSETS_DIR,
                f"scene_{index}_replacement_{attempt}.jpg",
            )
            rendered.convert("RGBA").convert("RGB").save(replacement_path, "JPEG", quality=95)

            if str(source_type).lower() != "news_source":
                source_credit = source_credit_for_type(source_type)
            else:
                source_credit = str(old_layer.get("source_credit") or "").strip() if isinstance(old_layer, dict) else ""

            self._return_slide_visual_to_pool(old_layer, replacement_scene)

            replacement_provenance = dict(
                replacement_scene.get("asset_provenance") or {}
            )
            new_layer = {
                "image": replacement_path,
                "visual_original_path": str(
                    replacement_scene.get("visual_original_path") or ""
                ).strip(),
                "text": "" if format_mode == "top5" else replacement_scene.get("voiceover", ""),
                "ai_generated": used_ai,
                "source_type": source_type,
                "visual_type": visual_type,
                "visual_genre": replacement_scene.get("visual_genre", "GENERAL_CONTEXT"),
                "visual_verified": bool(replacement_scene.get("visual_verified", False)),
                "visual_qc_blocked": bool(replacement_scene.get("visual_qc_blocked", False)),
                "visual_qc_block_reason": replacement_scene.get("visual_qc_block_reason", ""),
                "visual_rescue_reason": replacement_scene.get("visual_rescue_reason", ""),
                "visual_fallback_reason": "",
                "visual_query_used": replacement_scene.get("visual_query_used", ""),
                "manual_visual_query": query,
                "manual_visual_query_score": replacement_scene.get("manual_visual_query_score", 0),
                "source_credit": source_credit,
                "source_image_url": str(
                    replacement_scene.get("source_image_url")
                    or replacement_provenance.get("url")
                    or replacement_provenance.get("source_page_url")
                    or ""
                ).strip(),
                "asset_provenance": replacement_provenance,
            }

            with self._lock:
                self._visual_packages[index - 1] = [new_layer]
                live_script = self.state.script_data
                if isinstance(live_script, dict) and isinstance(live_script.get("script"), list):
                    live_script["script"][index - 1] = replacement_scene
                history.append(
                    {
                        "old_path": old_path,
                        "old_query": old_query,
                        "new_query": query,
                        "attempt": attempt,
                        "time": datetime.now(timezone.utc).isoformat(),
                    }
                )
                self._visual_approved = False
                self._visual_rejected = False

            self.update(
                "visual_approval",
                76,
                f"Visual {index} replaced. Review the new image before continuing.",
            )
            return True, f"Visual {index} replaced successfully."

        except Exception as exc:
            return False, f"Replacement search failed: {type(exc).__name__}: {exc}"

    def replace_visual_from_bank(self, visual_index: int, bank_index: int) -> tuple[bool, str]:
        """Replace one reviewed visual with a previously entity-verified bank image."""
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False, "Visual review is no longer active."

        try:
            index = int(visual_index)
            bank_pos = int(bank_index)
        except (TypeError, ValueError):
            return False, "Invalid visual or bank image number."

        packages = snapshot.get("visual_packages") or []
        script_data = snapshot.get("script_data") or {}
        scenes = script_data.get("script") if isinstance(script_data, dict) else None
        if index < 1 or index > len(packages) or not isinstance(scenes, list) or index > len(scenes):
            return False, "That visual is no longer available."

        layer = packages[index - 1][0] if isinstance(packages[index - 1], list) and packages[index - 1] else packages[index - 1]
        if not isinstance(layer, dict):
            return False, "The selected visual package is invalid."

        bank = [
            item for item in (layer.get("visual_asset_bank") or [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        if bank_pos < 1 or bank_pos > len(bank):
            return False, "That bank image is no longer available."

        selected = bank[bank_pos - 1]
        selected_path = str(selected.get("path") or "").strip()
        if not selected_path or not os.path.isfile(selected_path):
            return False, "The selected bank image is no longer available on the dashboard host."

        scene = scenes[index - 1]
        if not isinstance(scene, dict):
            return False, "The selected slide is invalid."

        try:
            from visual_quality_runtime import fit_visual_image
            import visual_runtime
            from branding_runtime import source_credit_for_type

            bot = self.bot
            active_config = getattr(bot, "_active_web_config", {}) or {}
            format_mode = str(active_config.get("format_mode", "regular")).lower()
            language_key = str(active_config.get("language") or "english")
            language_cfg = getattr(bot, "LANGUAGES", {}).get(language_key, {})
            font_choice = language_cfg.get("font")
            video_title = str(
                script_data.get("title")
                or (script_data.get("titles") or [""])[0]
                or ""
            ).strip()

            bg_img = Image.open(selected_path).convert("RGB")
            bg_img = fit_visual_image(
                bg_img,
                (1080, 1920),
                str(scene.get("visual_genre") or selected.get("visual_genre") or "GENERAL_CONTEXT"),
            ).convert("RGBA")

            if format_mode == "top5" and index == 1:
                rendered = visual_runtime._render_image_slide(
                    bot,
                    bg_img,
                    video_title or scene.get("voiceover", "Top 5"),
                    "TODAY'S TOP 5",
                    font_choice,
                )
            elif format_mode == "top5":
                clean = re.sub(
                    r"(number\s*\d+|story\s*#?\d+|#\d+)",
                    "",
                    str(scene.get("voiceover", "")),
                    flags=re.IGNORECASE,
                ).strip()
                rendered = bot.render_top5_card(
                    bg_img,
                    max(1, 6 - index),
                    5,
                    clean or scene.get("voiceover", ""),
                    font_choice=font_choice,
                )
            else:
                rendered = bg_img

            history = self._visual_replacement_history.setdefault(index, [])
            attempt = len(history) + 1
            old_path = str(layer.get("image") or "").strip()
            old_query = str(layer.get("manual_visual_query") or layer.get("visual_query_used") or "").strip()

            new_bank = [
                item for item in bank
                if str(item.get("path") or "").strip() != selected_path
            ]
            old_original_path = str(layer.get("visual_original_path") or old_path).strip()
            if (
                old_original_path
                and os.path.isfile(old_original_path)
                and old_original_path != selected_path
                and bool(layer.get("visual_verified"))
            ):
                old_entry = {
                    "path": old_original_path,
                    "original_path": old_original_path,
                    "subject": str(
                        layer.get("related_subject")
                        or scene.get("primary_entity")
                        or scene.get("factual_primary_entity")
                        or ""
                    ).strip(),
                    "hash": "",
                    "source": str(layer.get("source_type") or "visual").strip(),
                    "query": str(layer.get("visual_query_used") or "").strip(),
                    "visual_type": str(layer.get("visual_type") or "").strip().upper(),
                    "visual_genre": str(layer.get("visual_genre") or "").strip().upper(),
                    "provenance": dict(layer.get("asset_provenance") or {}),
                    "status": "previously-selected",
                    "used": False,
                }
                if not any(str(item.get("path") or "") == old_original_path for item in new_bank):
                    new_bank.append(old_entry)
            bank_limit = 20 if bool(layer.get("visual_manual_pool_mode")) else 10
            new_bank = new_bank[:bank_limit]

            replacement_path = os.path.join(
                bot.ASSETS_DIR,
                f"scene_{index}_bank_replacement_{attempt}.jpg",
            )
            rendered.convert("RGBA").convert("RGB").save(replacement_path, "JPEG", quality=95)

            self._return_slide_visual_to_pool(layer, scene)

            selected_source = str(selected.get("source") or "verified-bank").strip()
            selected_query = str(selected.get("query") or "").strip()
            selected_status = str(selected.get("status") or "entity-verified").strip()
            low_resolution_manual_qc = selected_status == "factory-rejected-resolution"
            new_layer = dict(layer)
            selected_original_path = str(selected.get("original_path") or selected_path).strip()
            new_layer.update(
                {
                    "image": replacement_path,
                    "visual_original_path": selected_original_path,
                    "source_type": "verified-bank",
                    "visual_verified": True,
                    "visual_qc_blocked": False,
                    "visual_qc_block_reason": "",
                    "resolution_manual_override": low_resolution_manual_qc,
                    "resolution_review_note": (
                        "Selected by manual review despite soft resolution warning."
                        if low_resolution_manual_qc
                        else ""
                    ),
                    "visual_rescue_reason": "",
                    "visual_fallback_reason": "",
                    "visual_query_used": f"bank:{selected_query}",
                    "visual_selected_hash": str(selected.get("hash") or "").strip(),
                    "source_credit": source_credit_for_type(selected_source),
                    "bank_selected_status": selected_status,
                    "source_image_url": str(
                        selected.get("source_image_url")
                        or (selected.get("provenance") or {}).get("url")
                        or ""
                    ).strip(),
                    "asset_provenance": dict(selected.get("provenance") or {}),
                    "visual_asset_bank": new_bank,
                    "bank_selected_source": selected_source,
                    "bank_selected_query": selected_query,
                }
            )

            with self._lock:
                self._visual_packages[index - 1] = [new_layer]
                live_script = self.state.script_data
                if isinstance(live_script, dict) and isinstance(live_script.get("script"), list):
                    live_script["script"][index - 1]["visual_verified"] = True
                    live_script["script"][index - 1]["visual_source"] = "verified-bank"
                    live_script["script"][index - 1]["visual_query_used"] = f"bank:{selected_query}"
                history.append(
                    {
                        "old_path": old_path,
                        "old_query": old_query,
                        "new_query": f"bank:{selected_query}",
                        "attempt": attempt,
                        "time": datetime.now(timezone.utc).isoformat(),
                        "replacement_type": "verified_bank",
                    }
                )
                self._visual_approved = False
                self._visual_rejected = False

            self.update(
                "visual_approval",
                76,
                f"Visual {index} replaced from the verified image bank. Review the new image before continuing.",
            )
            return True, f"Visual {index} replaced from the verified image bank."

        except Exception as exc:
            return False, f"Bank replacement failed: {type(exc).__name__}: {exc}"


    def crop_visual(
        self,
        visual_index: int,
        zoom: float = 1.0,
        x_center: float = 0.5,
        y_center: float = 0.5,
        crop_box: dict[str, Any] | None = None,
        crop_mode: str = "shorts",
    ) -> tuple[bool, str]:
        """Apply a dashboard-selected crop to the preserved original visual source or an exact crop box."""
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False, "Visual review is no longer active."

        try:
            index = int(visual_index)
        except (TypeError, ValueError):
            return False, "Invalid visual number."

        packages = snapshot.get("visual_packages") or []
        script_data = snapshot.get("script_data") or {}
        scenes = script_data.get("script") if isinstance(script_data, dict) else None
        if index < 1 or index > len(packages) or not isinstance(scenes, list) or index > len(scenes):
            return False, "That visual is no longer available."

        layer = packages[index - 1][0] if isinstance(packages[index - 1], list) and packages[index - 1] else packages[index - 1]
        if not isinstance(layer, dict):
            return False, "The selected visual package is invalid."

        source_path = str(layer.get("visual_original_path") or "").strip()
        if not source_path or not os.path.isfile(source_path):
            source_path = str(layer.get("image") or "").strip()
        if not source_path or not os.path.isfile(source_path):
            return False, "The original visual source is not available for cropping."

        scene = scenes[index - 1]
        if not isinstance(scene, dict):
            return False, "The selected slide is invalid."

        try:
            import visual_runtime
            from branding_runtime import source_credit_for_type

            bot = self.bot
            active_config = getattr(bot, "_active_web_config", {}) or {}
            format_mode = str(active_config.get("format_mode", "regular")).lower()
            language_key = str(active_config.get("language") or "english")
            language_cfg = getattr(bot, "LANGUAGES", {}).get(language_key, {})
            font_choice = language_cfg.get("font")
            video_title = str(
                script_data.get("title")
                or (script_data.get("titles") or [""])[0]
                or ""
            ).strip()

            original = Image.open(source_path).convert("RGB")
            if crop_box:
                cropped = _manual_crop_box_to_shorts(
                    original,
                    crop_box,
                    free_size=str(crop_mode or "").strip().casefold() == "free",
                ).convert("RGBA")
            else:
                cropped = _manual_crop_to_shorts(original, zoom, x_center, y_center).convert("RGBA")

            if format_mode == "top5" and index == 1:
                rendered = visual_runtime._render_image_slide(
                    bot,
                    cropped,
                    video_title or scene.get("voiceover", "Top 5"),
                    "TODAY'S TOP 5",
                    font_choice,
                )
            elif format_mode == "top5":
                clean = re.sub(
                    r"(number\s*\d+|story\s*#?\d+|#\d+)",
                    "",
                    str(scene.get("voiceover", "")),
                    flags=re.IGNORECASE,
                ).strip()
                rendered = bot.render_top5_card(
                    cropped,
                    max(1, 6 - index),
                    5,
                    clean or scene.get("voiceover", ""),
                    font_choice=font_choice,
                )
            else:
                rendered = cropped

            history = self._visual_replacement_history.setdefault(index, [])
            attempt = len(history) + 1
            output_path = os.path.join(
                bot.ASSETS_DIR,
                f"scene_{index}_manual_crop_{attempt}.jpg",
            )
            rendered.convert("RGBA").convert("RGB").save(output_path, "JPEG", quality=95)

            new_layer = dict(layer)
            new_layer.update(
                {
                    "image": output_path,
                    "visual_original_path": source_path,
                    "visual_crop_zoom": float(max(1.0, min(4.0, zoom))),
                    "visual_crop_x": float(max(0.0, min(1.0, x_center))),
                    "visual_crop_y": float(max(0.0, min(1.0, y_center))),
                    "visual_crop_box": (
                        {
                            "left": int(crop_box.get("left", 0)),
                            "top": int(crop_box.get("top", 0)),
                            "width": int(crop_box.get("width", 0)),
                            "height": int(crop_box.get("height", 0)),
                        }
                        if isinstance(crop_box, dict)
                        else dict(layer.get("visual_crop_box") or {})
                    ),
                    "visual_crop_manual": True,
                    "visual_crop_mode": "free" if str(crop_mode or "").strip().casefold() == "free" else "shorts",
                    "source_credit": source_credit_for_type(
                        str(layer.get("source_type") or "visual")
                    ),
                }
            )

            with self._lock:
                self._visual_packages[index - 1] = [new_layer]
                live_script = self.state.script_data
                if isinstance(live_script, dict) and isinstance(live_script.get("script"), list):
                    live_script["script"][index - 1]["visual_crop_manual"] = True
                history.append(
                    {
                        "old_path": str(layer.get("image") or ""),
                        "new_query": "manual-crop",
                        "attempt": attempt,
                        "time": datetime.now(timezone.utc).isoformat(),
                        "replacement_type": "manual_crop",
                    }
                )
                self._visual_approved = False
                self._visual_rejected = False

            self.update(
                "visual_approval",
                76,
                f"Visual {index} manually cropped. Review the updated image before continuing.",
            )
            return True, f"Visual {index} manually cropped successfully."

        except Exception as exc:
            return False, f"Manual crop failed: {type(exc).__name__}: {exc}"

    def reject_visuals(self) -> bool:
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False
        self._visual_rejected = True
        gate = self._manual_gate_state
        if isinstance(gate, dict):
            gate["visual_rejected"] = True
            gate["visual_event"].set()
        self.update("error", 100, "Visual review rejected. Stopping this production run.")
        return True

    def snapshot(self):
        data = super().snapshot()
        console_lines = self.console_lines()
        with self._lock:
            active_config = getattr(self.bot, "_active_web_config", {}) or {}
        data.update(
            {
                "format_mode": str(
                    active_config.get("format_mode")
                    or (data.get("selected_story") or {}).get("format_mode")
                    or "regular"
                ).strip().lower(),
                "visual_pipeline": str(active_config.get("visual_pipeline") or "option1_scrape").strip(),
                "script_review_required": data.get("stage") == "script_review",
                "script_visual_queries": list(self._script_visual_queries),
                "visual_packages": list(self._visual_packages),
                "visual_review_required": data.get("stage") == "visual_approval",
                "visual_review_approved": self._visual_approved,
                "visual_search_options": {
                        key: [dict(item) for item in value]
                        for key, value in self._visual_search_options.items()
                    },
                    "visual_pool": [dict(item) for item in self._visual_pool],
                    "visual_search_groups": [
                        {
                            "id": str(group.get("id") or ""),
                            "query": str(group.get("query") or ""),
                            "items": [dict(item) for item in (group.get("items") or [])],
                            "target": int(group.get("target") or 10),
                            "available": len(
                                [item for item in (group.get("items") or []) if not bool(item.get("used"))]
                            ),
                        }
                        for group in self._visual_search_groups
                    ],
                    "visual_replacement_history": {
                        key: list(value)
                        for key, value in self._visual_replacement_history.items()
                    },
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


def _run_scene_branding_demo() -> dict[str, Any]:
    """Render a representative final-frame preview using the canonical compositor."""
    from PIL import ImageDraw
    from branding_runtime import build_scene_branding_overlays

    temp_dir = tempfile.mkdtemp(prefix="vsf_branding_demo_")
    preview_path = os.path.join(temp_dir, "final_branding_preview.png")
    width, height = 1080, 1920
    base = Image.new("RGB", (width, height))
    pixels = base.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        shade = int(18 + 48 * ratio)
        for x in range(width):
            pixels[x, y] = (12 + int(22 * (1 - ratio)), 28 + shade // 2, 52 + shade)
    draw = ImageDraw.Draw(base)
    draw.text((70, 160), "FINAL VIDEO PREVIEW", fill=(240, 245, 250), font=None)
    draw.text((70, 220), "Canonical end-of-video branding", fill=(145, 180, 205), font=None)
    draw.rounded_rectangle((70, 330, 1010, 1540), radius=32, outline=(80, 115, 145), width=3)
    draw.text((110, 430), "This is a synthetic frame.\nThe real video remains unchanged beneath the overlay.", fill=(220, 228, 235), font=None, spacing=18)

    overlays = build_scene_branding_overlays(__import__("ultimate_bot"), width, height, "Source: Reuters")
    composed = base.convert("RGBA")
    for layer in overlays:
        composed = Image.alpha_composite(composed, Image.fromarray(layer, mode="RGBA"))
    composed.convert("RGB").save(preview_path, "PNG")
    return {
        "status": "PASS",
        "detail": "Canonical final branding rendered successfully on a 1080x1920 synthetic frame. This is the same overlay path used by the final compositor.",
        "artifacts": {"final_branding_preview": preview_path},
    }


def _run_synthetic_renderer_demo() -> dict[str, Any]:
    """Exercise the current premium subtitle/card renderers without network calls."""
    from subtitle_runtime import (
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

    from branding_runtime import build_scene_branding_overlays
    branding_layers = build_scene_branding_overlays(__import__("ultimate_bot"), 1080, 1920, "Source: Reuters")
    logo_path = os.path.join(temp_dir, "logo_badge.png")
    Image.fromarray(branding_layers[0], mode="RGBA").crop((900, 0, 1080, 220)).save(logo_path, "PNG")

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
        _test_visual_queries,
    )

    checks: dict[str, Callable[[], str]] = {
        "imports": _test_imports,
        "environment": _test_environment,
        "database": _test_database,
        "visual_strategy": _test_visual_strategy,
        "visual_queries": _test_visual_queries,
        "scene_branding": _test_scene_branding,
        "script_audio": _test_script_and_audio,
        "runtime_bindings": _test_runtime_bindings,
        "provider_boundary": _test_provider_boundary,
        "dashboard_architecture": _test_dashboard_architecture,
        "factory_function_coverage": _test_factory_function_coverage,
    }

    if section == "scene_branding":
        return _run_scene_branding_demo()

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
