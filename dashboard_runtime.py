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
from concurrent.futures import ThreadPoolExecutor
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


_ARTIFACT_QC_CACHE: dict[tuple[str, int], tuple[bool, str]] = {}

def evaluate_live_qc_gates(snapshot: dict[str, Any], metadata: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Evaluate the dashboard's real release gates from the current production state."""
    snapshot = snapshot or {}
    script = snapshot.get("script_data") or {}
    scenes = script.get("script") if isinstance(script, dict) else None
    scenes = scenes if isinstance(scenes, list) else []
    format_mode = str((snapshot.get("selected_story") or {}).get("format_mode") or "regular").lower()
    minimum = 7 if format_mode == "top5" else 5
    maximum = 7 if format_mode == "top5" else 8
    selected = snapshot.get("selected_story") or {}
    story_ok = bool(
        str(selected.get("title") or "").strip()
        and str(selected.get("story_key") or "").strip()
        and str(selected.get("discovery_rank") or "").isdigit()
    )
    script_ok = (
        minimum <= len(scenes) <= maximum
        and len(script.get("titles") or []) == 3
        and script.get("recommended_title_index") in (0, 1, 2)
        and len(str(script.get("seo_description") or "").split()) >= 10
        and all(
            isinstance(scene, dict)
            and str(scene.get("voiceover") or "").strip()
            and str(scene.get("primary_entity") or "").strip()
            and len(str(scene.get("specific_search_prompt") or "").split()) >= 2
            for scene in scenes
        )
    )
    audio_paths = [str(path).strip() for path in (snapshot.get("audio_paths") or []) if str(path or "").strip()]
    audio_ok = bool(scenes) and len(audio_paths) >= len(scenes) and all(os.path.isfile(path) for path in audio_paths)
    packages = snapshot.get("visual_packages") or []
    visual_items = []
    for package in packages:
        layer = package[0] if isinstance(package, list) and package else package
        if isinstance(layer, dict):
            visual_items.append(layer)
    visual_package_ok = (
        bool(scenes)
        and len(visual_items) == len(scenes)
        and all(str(item.get("image") or "").strip() and os.path.isfile(str(item.get("image") or "").strip()) for item in visual_items)
    )
    visual_verified_ok = visual_package_ok and all(bool(item.get("visual_verified")) for item in visual_items)
    video_path = str(snapshot.get("video_path") or "").strip()
    render_ok = bool(video_path) and os.path.isfile(video_path)

    artifact_ok = False
    artifact_detail = "Final artifact is not available yet."
    if render_ok:
        try:
            stat = os.stat(video_path)
            cache_key = (video_path, int(stat.st_mtime_ns))
            cached = _ARTIFACT_QC_CACHE.get(cache_key)
            if cached is None:
                from final_qc_runtime import _validate_final_artifact
                cached = _validate_final_artifact(video_path)
                _ARTIFACT_QC_CACHE.clear()
                _ARTIFACT_QC_CACHE[cache_key] = cached
            artifact_ok, artifact_detail = cached
        except Exception as exc:
            artifact_detail = f"Artifact QC error: {type(exc).__name__}: {exc}"

    md = metadata or {}
    title = str(md.get("title") or script.get("title") or "").strip()
    description = str(md.get("description") or script.get("seo_description") or "").strip()
    comment = str(md.get("comment") or script.get("pinned_comment") or "").strip()
    try:
        from final_qc_runtime import _validate_metadata
        metadata_ok, metadata_detail = _validate_metadata(title, description, comment)
    except Exception as exc:
        metadata_ok, metadata_detail = False, f"Metadata QC error: {type(exc).__name__}: {exc}"

    try:
        from final_qc_runtime import evaluate_originality_gate
        originality_gate = evaluate_originality_gate(script)
    except Exception as exc:
        originality_gate = {
            "passed": False,
            "public_blocked": True,
            "label": "Originality + Creator Insight",
            "detail": f"Originality QC unavailable: {type(exc).__name__}: {exc}",
        }

    return [
        {"key": "originality", "label": originality_gate["label"], "passed": originality_gate["passed"],
         "public_blocked": bool(originality_gate.get("public_blocked")),
         "detail": originality_gate["detail"]},
        {"key": "story_lock", "label": "Verified story selection", "passed": story_ok,
         "detail": "Selected headline is tied to the discovery pool." if story_ok else "Production input is not tied to a verified discovery selection."},
        {"key": "script_contract", "label": "Script contract", "passed": script_ok,
         "detail": f"{len(scenes)} scenes satisfy the structure and metadata contract." if script_ok else f"Script contract failed: {len(scenes)} scenes; required {minimum}-{maximum} plus required metadata."},
        {"key": "narration", "label": "Narration + timings", "passed": audio_ok,
         "detail": f"{len(audio_paths)} narration track(s) are present." if audio_ok else "Narration tracks are missing or incomplete."},
        {"key": "visual_package", "label": "Visual package", "passed": visual_package_ok,
         "detail": f"{len(visual_items)} renderable scene visual(s) are present." if visual_package_ok else "Visual package is incomplete or contains missing files."},
        {"key": "visual_semantic_qc", "label": "Visual semantic QC", "passed": visual_verified_ok,
         "detail": "Every scene visual carries a verified QC verdict." if visual_verified_ok else "At least one scene visual is not semantically verified."},
        {"key": "visual_review", "label": "Human visual review", "passed": bool(snapshot.get("visual_review_approved")),
         "detail": "Visual review was explicitly approved." if snapshot.get("visual_review_approved") else "Human visual approval is still required."},
        {"key": "render", "label": "Final render", "passed": render_ok,
         "detail": "Final video file exists." if render_ok else "Final rendered video is missing."},
        {"key": "artifact_qc", "label": "Final artifact QC", "passed": artifact_ok, "detail": artifact_detail},
        {"key": "metadata_qc", "label": "Upload metadata QC", "passed": metadata_ok, "detail": metadata_detail},
        {"key": "run_identity", "label": "Exact production run identity", "passed": bool(str(snapshot.get("run_id") or "").strip()),
         "detail": "Current run has an exact run identifier." if snapshot.get("run_id") else "Exact run identity is missing; upload is blocked."},
    ]

def live_qc_passes(snapshot: dict[str, Any], metadata: dict[str, str] | None = None) -> bool:
    return all(bool(gate.get("passed")) for gate in evaluate_live_qc_gates(snapshot, metadata))

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


def discover_ai_topics(bot, web_config: dict[str, Any], conn, max_candidates: int = 28) -> list[dict[str, Any]]:
    """Build a diverse current-topic list from bounded global discovery lenses."""
    from story_ranker import (
        _canonical_url,
        _candidate_reason,
        _cheap_filter,
        _discovery_source_pass,
        _discovery_query_lanes,
        _recent_topic_cooldown,
        _deduplicate_stage,
        _editorial_score,
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
        diversity_rerank,
    )

    max_candidates = max(1, min(28, int(max_candidates or 28)))
    rows = _load_history(conn)
    used_topics = _load_used_topics(conn)
    language = str(web_config.get("language", "english"))
    api_key = str(os.getenv("GNEWS_API_KEY") or getattr(bot, "GNEWS_API_KEY", "") or "").strip()
    raw: list[dict[str, Any]] = []

    gnews_jobs = []
    source_jobs = []
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="ai-discovery") as pool:
        for category in AI_DISCOVERY_CATEGORY_KEYS:
            cfg = bot.CONTENT_CATEGORIES.get(category) or {}
            query = str(cfg.get("gnews_q", "") or "").strip()
            for lane in _discovery_query_lanes(query, genre_key=category, broad=True)[:4]:
                if lane and api_key:
                    gnews_jobs.append((
                        category,
                        pool.submit(
                            _gnews_items,
                            lane,
                            api_key,
                            category,
                            global_scope=True,
                        ),
                    ))
            rss_url = str(cfg.get("rss_url", "") or "").strip()
            if rss_url:
                source_jobs.append(pool.submit(_rss_items, rss_url, category))
            source_jobs.append(pool.submit(_official_feed_items, category, cfg))
        for category, future in gnews_jobs:
            raw.extend(future.result())
        for future in source_jobs:
            raw.extend(future.result())

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
        query=(
            "breaking OR latest OR announced OR decision OR deal OR launch OR "
            "discovery OR incident OR crisis OR court OR business OR technology OR "
            "sports OR entertainment OR culture OR viral"
        ),
        existing_articles=compact,
        timespan="48h",
        max_gdelt_records=150,
    )
    candidates = event_pool.get("events") or compact

    stage30 = _cheap_filter(candidates, max_items=90, max_age_hours=48)
    stage20 = _deduplicate_stage(stage30, max_items=70)
    stage20 = _recent_topic_cooldown(conn, stage20, hours=36)
    stage20 = [item for item in stage20 if _discovery_source_pass(item)]
    # Dashboard discovery keeps provenance on every event but does not require
    # multi-source corroboration before showing it to the human selector.
    stage12 = stage20
    stage10 = _originality_stage(stage12, used_topics, max_items=max_candidates * 2)

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

    ranked = diversity_rerank(ranked, max_items=max_candidates)
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

    if not pool:
        print("   [AI Discovery] No source-backed candidates survived the discovery gates.", flush=True)
    print(
        f"   [AI Discovery] current intake={len(compact)} -> events={len(candidates)} -> "
        f"Top {len(pool)}; history used as a fit signal, not a repetition target.",
        flush=True,
    )
    return pool


def discover_ranked_topics(bot, web_config: dict[str, Any], conn, max_candidates: int = 28) -> list[dict[str, Any]]:
    """Dashboard discovery pool: return up to 28 diverse, evidence-backed topics."""
    from story_ranker import (
        _cricket_relevance_pass,
        _requested_topic_pass,
        collect_high_recall_stories,
        rank_discovery_candidates,
    )
    from workflow_runtime import CRICKET_CATEGORIES, _candidate_reason, _source_label, _story_key, _story_url

    max_candidates = max(1, min(28, int(max_candidates or 28)))
    fmt = str(web_config.get("format_mode", "regular"))
    category = str(web_config.get("category", ""))
    language = str(web_config.get("language", "english"))
    bot._active_web_config = dict(web_config)

    is_cricket = fmt == "cricket" or bool(web_config.get("cricket_pipeline"))
    if is_cricket:
        cricket_name = str(
            web_config.get("cricket_category", "AI-assisted top story in cricket")
        )
        cricket_cfg = CRICKET_CATEGORIES.get(
            cricket_name,
            CRICKET_CATEGORIES["AI-assisted top story in cricket"],
        )
        genre_key = "sports_stories_of_day"
        genre_cfg = bot.CONTENT_CATEGORIES.get(genre_key, {})
        requested_topic = str(web_config.get("requested_topic", "") or "").strip()
        custom_q = requested_topic or cricket_cfg["query"]
        custom_rss = cricket_cfg["rss"]
    else:
        genre_key = category or "national_global_affairs"
        genre_cfg = bot.CONTENT_CATEGORIES.get(genre_key)
        if not genre_cfg:
            raise ValueError(f"Unknown category: {genre_key}")
        requested_topic = str(web_config.get("requested_topic", "") or "").strip()
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
            and str(web_config.get("cricket_category", ""))
            == "AI-assisted top story in cricket"
        ),
        broad_discovery=True,
    )

    relevance_filtered = []
    for candidate in raw:
        if not _cricket_relevance_pass(candidate, genre_key):
            continue
        if not _requested_topic_pass(candidate, requested_topic):
            continue
        relevance_filtered.append(candidate)

    ai_cricket = (
        genre_key == "sports_stories_of_day"
        and str(web_config.get("cricket_category", ""))
        == "AI-assisted top story in cricket"
    )

    ranked = rank_discovery_candidates(
        relevance_filtered,
        conn=conn,
        target_category=category or genre_key,
        target_format=web_config.get("format_mode", "regular"),
        target_language=language,
        social_titles=social_titles,
        ai_cricket=ai_cricket,
        max_candidates=max_candidates,
    )

    pool = ranked[:max_candidates]
    for rank, story in enumerate(pool, 1):
        story["discovery_rank"] = rank
        story["discovery_reason"] = _candidate_reason(story)
        story["source_label"] = _source_label(story)
        story["story_url"] = _story_url(story)
        story["story_key"] = _story_key(story)

    print(
        f"   [Dashboard Discovery] {len(raw)} event candidates -> "
        f"{len(relevance_filtered)} relevant -> {len(pool)} diverse ranked headline(s).",
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
        self._script_review_event = threading.Event()
        self._script_review_submitted = False
        self._creator_insight = ""
        self._creator_insight_submitted = False
        self._script_visual_queries: list[str] = []
        self._visual_approved = False
        self._visual_rejected = False
        self._visual_packages: list[Any] = []
        self._visual_replacement_history: dict[int, list[dict[str, Any]]] = {}
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
        self._script_review_event.clear()
        self._script_review_submitted = False
        self._creator_insight = ""
        self._creator_insight_submitted = False
        self._script_visual_queries = []
        self._visual_approved = False
        self._visual_rejected = False
        self._visual_packages = []
        self._visual_replacement_history = {}
        self._dashboard_logs = []
        self._activity_events = []
        self._audio_paths = []
        self._console_lines = []
        self._console_partial = ""
        self._last_dashboard_message = ""
        # The base controller deliberately reinstalls production wrappers for
        # each new run. Dashboard-specific wrappers must be eligible for the
        # same fresh binding rather than remaining marked as already installed.
        self._dashboard_visual_gate_bound = False
        self._dashboard_audio_capture_wrapper = None
        self._dashboard_visual_gate_wrapper = None
        self._manual_gate_state = None
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

    def _prepare_production_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Install dashboard checkpoints into the core production call path."""
        self._manual_gate_state = {
            "script_event": threading.Event(),
            "visual_event": threading.Event(),
            "script_submitted": False,
            "creator_insight_submitted": False,
            "visual_approved": False,
            "visual_rejected": False,
        }
        config["manual_qc_required"] = True
        config["_manual_script_review_hook"] = self._manual_script_review_hook
        config["_manual_visual_review_hook"] = self._manual_visual_review_hook
        config["_manual_post_render_hook"] = self._manual_post_render_hook
        return config

    def _manual_script_review_hook(self, result: dict[str, Any]) -> dict[str, Any]:
        """Pause the core factory until script queries and Creator Insight are submitted."""
        if not isinstance(result, dict):
            raise RuntimeError("Script review could not start because the script payload is invalid.")

        scenes = result.get("script") or []
        if not isinstance(scenes, list) or not scenes:
            raise RuntimeError("Script review could not start because no script scenes were returned.")

        gate = self._manual_gate_state
        if not isinstance(gate, dict):
            raise RuntimeError("Manual script review gate is not active.")

        gate["script_event"].clear()
        gate["script_submitted"] = False
        gate["creator_insight_submitted"] = False
        self._creator_insight = ""
        self._creator_insight_submitted = False

        with self._lock:
            self._script_visual_queries = [""] * len(scenes)
            self.state.script_data = result

        self.update(
            "script_review",
            40,
            f"The script is ready. Add your Creator Insight and review {len(scenes)} slides.",
        )
        gate["script_event"].wait(timeout=24 * 60 * 60)

        if not gate["script_submitted"] or not gate["creator_insight_submitted"]:
            raise RuntimeError(
                "Script review timed out or Creator Insight was not submitted. The production run was stopped."
            )

        with self._lock:
            queries = list(self._script_visual_queries)
            script_scenes = result.get("script") or []
            insight = str(self._creator_insight or "").strip()

        if len(insight.split()) < 12:
            raise RuntimeError("Creator Insight must contain at least 12 words.")
        if len(script_scenes) < 2:
            raise RuntimeError("Creator Insight requires at least two generated scenes.")

        insight_scene = {
            "voiceover": insight,
            "primary_entity": str(
                script_scenes[-1].get("primary_entity")
                or script_scenes[0].get("primary_entity")
                or ""
            ).strip(),
            "visual_intent": "conceptual",
            "specific_search_prompt": "creator insight context",
            "sport_or_topic_category": str(
                script_scenes[-1].get("sport_or_topic_category") or ""
            ),
            "human_contributed": True,
        }
        insert_at = len(script_scenes) - 1
        script_scenes.insert(insert_at, insight_scene)
        queries = queries[:insert_at] + [""] + queries[insert_at:]
        result["script"] = script_scenes
        result["creator_insight"] = insight
        result["creator_insight_required"] = True

        with self._lock:
            self.state.script_data = result
            self._script_visual_queries = queries[:len(script_scenes)]

        for index, scene in enumerate(script_scenes):
            if not isinstance(scene, dict):
                continue
            query = queries[index] if index < len(queries) else ""
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
        return result

    def _manual_visual_review_hook(self, packages: list[Any]) -> list[Any]:
        """Pause the core factory until every generated visual has been reviewed."""
        packages = list(packages or [])
        if not packages:
            raise RuntimeError("Visual review could not start because no visual packages were returned.")

        gate = self._manual_gate_state
        if not isinstance(gate, dict):
            raise RuntimeError("Manual visual review gate is not active.")

        self._visual_packages = packages
        self._visual_approval_event = gate["visual_event"]
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

        self.update("render", 77, "Visuals approved. Rendering the final Short now.")
        return packages

    def _manual_post_render_hook(self, video_path: str) -> None:
        with self._lock:
            self.state.video_path = os.path.abspath(os.fspath(video_path)) if video_path else ""
        self.update("render", 94, "Video rendered and ready for final QC.")

    def _install_production_wrappers(self):
        # Safety-critical script and visual waits live in the core factory path.
        # These wrappers are presentation-only and may be rebound without removing
        # the actual manual checkpoints.
        super()._install_production_wrappers()

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

        self._dashboard_visual_gate_bound = True

    def submit_script_visual_queries(self, queries: list[str], creator_insight: str = "") -> bool:
        snapshot = self.snapshot()
        if snapshot.get("stage") != "script_review":
            return False

        script_data = snapshot.get("script_data") or {}
        scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
        if not isinstance(scenes, list) or not scenes:
            return False

        cleaned = [str(query or "").strip() for query in list(queries or [])]
        cleaned = (cleaned + [""] * len(scenes))[:len(scenes)]
        insight = str(creator_insight or "").strip()
        if len(insight.split()) < 12:
            return False

        with self._lock:
            self._creator_insight = insight
            self._creator_insight_submitted = True
            gate = self._manual_gate_state
            if isinstance(gate, dict):
                gate["script_submitted"] = True
                gate["creator_insight_submitted"] = True
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
            self._script_review_submitted = True

        self.update(
            "audio",
            42,
            "Slide queries saved. Creating the voiceover and preparing visuals.",
        )
        gate = self._manual_gate_state
        if isinstance(gate, dict):
            gate["script_event"].set()
        else:
            self._script_review_event.set()
        return True

    def approve_visuals(self) -> bool:
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False
        self._visual_approved = True
        gate = self._manual_gate_state
        if isinstance(gate, dict):
            gate["visual_approved"] = True
            gate["visual_event"].set()
        else:
            self._visual_approval_event.set()
        return True

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

            new_layer = {
                "image": replacement_path,
                "text": "" if format_mode == "top5" else replacement_scene.get("voiceover", ""),
                "ai_generated": used_ai,
                "source_type": source_type,
                "visual_type": visual_type,
                "visual_genre": replacement_scene.get("visual_genre", "GENERAL_CONTEXT"),
                "visual_verified": bool(replacement_scene.get("visual_verified", False)),
                "visual_rescue_reason": replacement_scene.get("visual_rescue_reason", ""),
                "visual_fallback_reason": "",
                "visual_query_used": replacement_scene.get("visual_query_used", ""),
                "manual_visual_query": query,
                "manual_visual_query_score": replacement_scene.get("manual_visual_query_score", 0),
                "source_credit": source_credit,
                "source_image_url": "",
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

    def reject_visuals(self) -> bool:
        snapshot = self.snapshot()
        if snapshot.get("stage") != "visual_approval":
            return False
        self._visual_rejected = True
        gate = self._manual_gate_state
        if isinstance(gate, dict):
            gate["visual_rejected"] = True
            gate["visual_event"].set()
        else:
            self._visual_approval_event.set()
        self.update("error", 100, "Visual review rejected. Stopping this production run.")
        return True

    def snapshot(self):
        data = super().snapshot()
        console_lines = self.console_lines()
        with self._lock:
            data.update(
                {
                    "script_review_required": data.get("stage") == "script_review",
                    "creator_insight": self._creator_insight,
                    "creator_insight_required": data.get("stage") == "script_review",
                    "script_visual_queries": list(self._script_visual_queries),
                    "visual_packages": list(self._visual_packages),
                    "visual_review_required": data.get("stage") == "visual_approval",
                    "visual_review_approved": self._visual_approved,
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
