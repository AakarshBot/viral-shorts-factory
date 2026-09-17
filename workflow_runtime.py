"""Newsroom-style staged workflow for the Viral Shorts Factory."""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from youtube_comment_runtime import _build_clean_metadata, build_pinned_comment
from db_runtime import run_robot_with_exact_identity
from db_architecture import migrate_vault, update_run_record

WORKFLOW_VERSION = "2026-09-16-newsroom-v2"
MAX_DISCOVERY_CANDIDATES = 12

FORMAT_OPTIONS = {
    "Deep Dive": "regular",
    "Top-5": "top5",
    "Cricket": "cricket",
}

CRICKET_CATEGORIES = {
    "India / Asia": {
        "key": "cricket_india_asia",
        "query": "India Cricket OR Pakistan Cricket OR Sri Lanka Cricket OR Bangladesh Cricket OR BCCI OR PCB OR SLC OR BCB OR ACB",
        "rss": "https://news.google.com/rss/search?q=India+Cricket+OR+Pakistan+Cricket+OR+BCCI+OR+PCB&hl=en-IN&gl=IN&ceid=IN:en",
    },
    "Global": {
        "key": "cricket_global",
        "query": "ICC Cricket OR Australia Cricket OR England Cricket OR New Zealand Cricket OR South Africa Cricket OR West Indies Cricket OR Test Cricket OR T20 Cricket",
        "rss": "https://news.google.com/rss/search?q=ICC+Cricket+OR+Australia+Cricket+OR+England+Cricket+OR+Test+Cricket&hl=en-IN&gl=IN&ceid=IN:en",
    },
    "AI-assisted top story in cricket": {
        "key": "cricket_ai_today",
        "query": "Cricket OR ICC OR BCCI OR Test Cricket OR T20 Cricket OR IPL OR PSL OR Big Bash",
        "rss": "https://news.google.com/rss/search?q=Cricket+OR+ICC+OR+BCCI+OR+Test+Cricket+OR+T20+Cricket&hl=en-IN&gl=IN&ceid=IN:en",
    },
}


def _token_set(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "into", "after",
        "before", "over", "under", "what", "how", "why", "world", "news",
        "latest", "today", "just", "will", "says", "said", "new", "breaking",
    }
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return {w for w in words if len(w) > 2 and w not in stop}


def _overlap(a: str, b: str) -> float:
    aa, bb = _token_set(a), _token_set(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _candidate_reason(story: Dict[str, Any]) -> str:
    parts: List[str] = []
    velocity = _num(story.get("velocity_score"))
    corr = _num(story.get("corroboration_bonus"))
    trend = _num(story.get("trend_bonus"))
    history = _num(story.get("historical_topic_signal"))
    if velocity >= 5:
        parts.append("strong freshness/velocity")
    if corr >= 2:
        parts.append("multiple-source corroboration")
    if trend >= 2:
        parts.append("current trend signal")
    if history >= 2:
        parts.append("relevant historical channel performance")
    if not parts:
        parts.append("strong current-story score after safety and duplicate filtering")
    return ", ".join(parts) + "."


def _source_label(story: Dict[str, Any]) -> str:
    for key in ("source", "publisher", "source_name", "domain"):
        value = story.get(key)
        if value:
            return str(value)
    url = str(story.get("url") or story.get("link") or "")
    match = re.search(r"https?://([^/]+)", url)
    return match.group(1) if match else "News source"


def _story_url(story: Dict[str, Any]) -> str:
    return str(story.get("url") or story.get("link") or "").strip()


def _story_key(story: Dict[str, Any]) -> str:
    title = str(story.get("title") or "").strip().lower()
    url = _story_url(story).lower()
    return re.sub(r"[^a-z0-9]+", " ", f"{title} {url}").strip()


def _load_used_topics(conn) -> List[str]:
    try:
        rows = conn.execute("SELECT topic FROM vault WHERE topic IS NOT NULL").fetchall()
        return [str(row[0]) for row in rows if row and row[0]]
    except Exception as exc:
        print(f"   [Workflow] Could not load used topics: {exc}")
        return []


def _remove_near_duplicates(stories: List[Dict[str, Any]], used_topics: List[str]) -> List[Dict[str, Any]]:
    kept: List[Dict[str, Any]] = []
    for story in stories:
        title = str(story.get("title") or "").strip()
        if not title:
            continue
        if any(_overlap(title, old) >= 0.78 for old in used_topics):
            continue
        if any(_overlap(title, other.get("title", "")) >= 0.78 for other in kept):
            continue
        kept.append(story)
    return kept


def _diverse_top_three(stories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for story in stories:
        if not selected:
            selected.append(story)
            continue
        if all(_overlap(story.get("title", ""), old.get("title", "")) < 0.55 for old in selected):
            selected.append(story)
        if len(selected) == 3:
            return selected
    for story in stories:
        if story not in selected:
            selected.append(story)
        if len(selected) == 3:
            break
    return selected[:3]


def _apply_sports_diversity_bonus(stories: List[Dict[str, Any]]) -> None:
    non_cricket = {
        "tennis", "football", "soccer", "badminton", "athletics", "basketball",
        "formula", "f1", "motogp", "golf", "rugby", "volleyball", "hockey",
        "cycling", "boxing", "mma", "wrestling", "olympic", "olympics",
    }
    for story in stories:
        title_tokens = _token_set(story.get("title", ""))
        if title_tokens & non_cricket:
            story["candidate_score"] = _num(story.get("candidate_score")) + 2.5
            story["sports_niche_bonus"] = 2.5


def discover_three_candidates(bot, web_config: Dict[str, Any], conn) -> List[Dict[str, Any]]:
    """Run one discovery pass and return a stable pool of up to 12 candidates."""
    fmt = str(web_config.get("format_mode", "regular"))
    category = str(web_config.get("category", ""))
    language = str(web_config.get("language", "english"))
    bot._active_web_config = dict(web_config)

    is_cricket = fmt == "cricket" or bool(web_config.get("cricket_pipeline"))
    if is_cricket:
        cricket_name = str(web_config.get("cricket_category", "AI-assisted top story in cricket"))
        cricket_cfg = CRICKET_CATEGORIES.get(cricket_name, CRICKET_CATEGORIES["AI-assisted top story in cricket"])
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
        custom_q = None
        custom_rss = None

    requested_topic = str(web_config.get("requested_topic", "") or "").strip()
    if requested_topic:
        print(f"   [Workflow] Requested topic locked: {requested_topic}", flush=True)
    print(f"   [Workflow] Discovery only: format={fmt}, category={category}, language={language}", flush=True)
    stories = bot.gather_and_filter_stories(
        conn,
        genre_key,
        genre_cfg,
        web_config.get("trend_keyword"),
        custom_q,
        custom_rss,
    )

    stories = _remove_near_duplicates(stories or [], _load_used_topics(conn))
    if category == "sports":
        _apply_sports_diversity_bonus(stories)
    stories = sorted(
        stories,
        key=lambda s: _num(s.get("candidate_score"), _num(s.get("velocity_score")) + _num(s.get("trend_bonus"))),
        reverse=True,
    )

    first_three = _diverse_top_three(stories)
    if len(first_three) != 3:
        raise ValueError(
            f"Discovery produced only {len(first_three)} strong diverse candidate(s); production is blocked until exactly 3 are available."
        )

    pool = list(first_three)
    for story in stories:
        if story in pool:
            continue
        pool.append(story)
        if len(pool) >= MAX_DISCOVERY_CANDIDATES:
            break

    for rank, story in enumerate(pool, 1):
        story["discovery_rank"] = rank
        story["discovery_reason"] = _candidate_reason(story)
        if story.get("sports_niche_bonus"):
            story["discovery_reason"] += " Broader-sport diversity bonus applied."
        story["source_label"] = _source_label(story)
        story["story_url"] = _story_url(story)
        story["story_key"] = _story_key(story)

    print(
        f"   [Workflow] Discovery finished: {len(pool)} candidate(s) available; first 3 shown initially. No production APIs were called.",
        flush=True,
    )
    return pool


@dataclass
class WorkflowState:
    stage: str = "idle"
    percent: int = 0
    message: str = "Ready."
    run_id: str = ""
    selected_story: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    script_data: Optional[Dict[str, Any]] = None
    video_path: str = ""
    final_metadata: Dict[str, str] = field(default_factory=dict)
    error: str = ""
    thread_alive: bool = False
    completed: bool = False


class WorkflowController:
    def __init__(self, bot):
        self.bot = bot
        self._lock = threading.Lock()
        self.state = WorkflowState()
        self._patched = False
        self._real_uploader = None

    def reset(self):
        with self._lock:
            self.state = WorkflowState()

    def update(self, stage: str, percent: int, message: str):
        with self._lock:
            self.state.stage = stage
            self.state.percent = max(0, min(100, int(percent)))
            self.state.message = message

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "stage": self.state.stage,
                "percent": self.state.percent,
                "message": self.state.message,
                "run_id": self.state.run_id,
                "selected_story": dict(self.state.selected_story or {}),
                "candidates": [dict(x) for x in self.state.candidates],
                "script_data": self.state.script_data,
                "video_path": self.state.video_path,
                "final_metadata": dict(self.state.final_metadata),
                "error": self.state.error,
                "thread_alive": self.state.thread_alive,
                "completed": self.state.completed,
            }

    def _reporter(self, stage: str, percent: int, message: str):
        self.update(stage, percent, message)

    def _install_production_wrappers(self):
        if self._patched:
            return
        run_robot = getattr(self.bot, "run_robot", None)
        if run_robot is None:
            raise RuntimeError("Legacy run_robot() is not available.")
        globals_dict = getattr(run_robot, "__globals__", {})

        original_write = globals_dict.get("write_script")
        if callable(original_write):
            def write_wrapper(*args, **kwargs):
                self._reporter("script", 28, "Writing and self-critiquing the selected story…")
                result = original_write(*args, **kwargs)
                if isinstance(result, dict):
                    with self._lock:
                        self.state.script_data = result
                self._reporter("script", 36, "Script complete. Audio and visuals are next.")
                return result
            globals_dict["write_script"] = write_wrapper

        original_audio = globals_dict.get("generate_audio_for_script") or globals_dict.get("generate_audio")
        if callable(original_audio):
            def audio_wrapper(*args, **kwargs):
                self._reporter("audio", 42, "Generating narration and word timings…")
                result = original_audio(*args, **kwargs)
                self._reporter("audio", 52, "Narration complete. Building visual package…")
                return result
            if globals_dict.get("generate_audio_for_script") is not None:
                globals_dict["generate_audio_for_script"] = audio_wrapper
            else:
                globals_dict["generate_audio"] = audio_wrapper

        original_visuals = globals_dict.get("process_visuals_async")
        if callable(original_visuals):
            def visuals_wrapper(*args, **kwargs):
                self._reporter("visuals", 56, "Sourcing and verifying content-first visuals…")
                result = original_visuals(*args, **kwargs)
                self._reporter("visuals", 75, "Visual package complete. Rendering the Short…")
                return result
            globals_dict["process_visuals_async"] = visuals_wrapper

        original_compile = globals_dict.get("compile_video")
        if callable(original_compile):
            def compile_wrapper(*args, **kwargs):
                self._reporter("render", 78, "Stitching scenes, subtitles and branding…")
                result = original_compile(*args, **kwargs)
                self._reporter("render", 94, "Final video rendered. Preparing manual QC…")
                if isinstance(result, str) and os.path.isfile(result):
                    with self._lock:
                        self.state.video_path = result
                return result
            globals_dict["compile_video"] = compile_wrapper

        real_upload = getattr(self.bot, "upload_to_youtube", None)
        if callable(real_upload):
            self._real_uploader = real_upload
            def production_blocked_upload(*args, **kwargs):
                self._reporter("qc", 98, "Video ready. Waiting for your title, description and visibility decision.")
                print("   [Workflow] Automatic upload blocked. Manual QC is required.", flush=True)
                return "PENDING_MANUAL_UPLOAD"
            globals_dict["upload_to_youtube"] = production_blocked_upload
        self._patched = True

    def _mark_latest_run_ready_for_qc(self, _topic: str = ""):
        """Mark only the exact production row as READY_FOR_UPLOAD."""
        import ultimate_bot

        row_id = getattr(self.bot, "_last_run_row_id", None)
        run_id = getattr(self.bot, "_last_run_run_id", None)
        if row_id is None or not run_id:
            raise RuntimeError("Cannot mark READY_FOR_UPLOAD without exact run identity.")

        conn = sqlite3.connect(ultimate_bot.DB_PATH)
        try:
            migrate_vault(conn)
            row = conn.execute(
                "SELECT run_id, status, topic FROM vault WHERE id = ?",
                (row_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Exact production row {row_id} was not found.")
            if row[0] != run_id:
                raise RuntimeError("Exact production row identity does not match the active run_id.")
            update_run_record(
                conn,
                row_id,
                video_id="READY_FOR_UPLOAD",
                status="READY_FOR_UPLOAD",
            )
        finally:
            conn.close()

    def start_production(self, web_config: Dict[str, Any], selected_story: Dict[str, Any]):
        selected_story = _validate_selected_story(selected_story)
        if self.state.thread_alive:
            return
        self._install_production_wrappers()
        self.reset()
        with self._lock:
            self.state.selected_story = dict(selected_story)
            self.state.run_id = datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")
            self.state.stage = "research"
            self.state.percent = 16
            self.state.message = "Researching multiple sources for the selected story…"
            self.state.thread_alive = True
            self.state.completed = False
            self.state.error = ""

        config = dict(web_config)
        if config.get("cricket_pipeline") or config.get("display_format") == "Cricket":
            config["format_mode"] = "cricket"
        config["selected_story"] = dict(selected_story)
        config["publish_mode"] = "private"
        config["manual_qc_required"] = True
        self.bot._active_web_config = dict(config)

        def worker():
            try:
                run_robot = self.bot.run_robot
                globals_dict = getattr(run_robot, "__globals__", {})
                original_gather = globals_dict.get("gather_and_filter_stories")
                selected = dict(selected_story)

                def selected_gather(*args, **kwargs):
                    return [dict(selected)]

                if original_gather is not None:
                    globals_dict["gather_and_filter_stories"] = selected_gather
                try:
                    self._reporter("research", 18, "Selected story locked. Preparing the production pipeline…")
                    run_robot_with_exact_identity(self.bot, web_config=config)
                finally:
                    if original_gather is not None:
                        globals_dict["gather_and_filter_stories"] = original_gather

                script = self.state.script_data or {}
                category_key = config.get("category", "national_global_affairs")
                genre_cfg = self.bot.CONTENT_CATEGORIES.get(category_key, self.bot.CONTENT_CATEGORIES["national_global_affairs"])
                title, description, _tags = _build_clean_metadata(
                    script,
                    genre_cfg,
                    config.get("trend_keyword", ""),
                )
                comment = build_pinned_comment(script, title, genre_cfg.get("label", ""))
                self._mark_latest_run_ready_for_qc(selected.get("title", ""))

                with self._lock:
                    self.state.final_metadata = {
                        "title": title or str(selected.get("title") or "").strip(),
                        "description": description,
                        "pinned_comment": comment,
                    }
                    self.state.stage = "qc"
                    self.state.percent = 100
                    self.state.message = "Production complete. Final QC is waiting for you."
                    self.state.completed = True
            except Exception as exc:
                with self._lock:
                    self.state.error = f"{type(exc).__name__}: {exc}"
                    self.state.stage = "error"
                    self.state.percent = 100
                    self.state.message = "Factory stopped with an error."
            finally:
                with self._lock:
                    self.state.thread_alive = False

        threading.Thread(target=worker, name="viral-shorts-production", daemon=True).start()

    def upload_manual(
        self,
        video_path: str,
        script_data: Dict[str, Any],
        title: str,
        description: str,
        comment: str,
        publish_mode: str,
        genre_cfg: Dict[str, Any],
        trend_keyword: str = "",
    ):
        if self.state.thread_alive:
            raise RuntimeError("Production is still running. Final upload is locked until QC is ready.")
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"Final video file not found: {video_path}")
        from final_qc_runtime import validate_final_upload_metadata, validate_final_video

        validate_final_video(video_path)
        clean_title, clean_description, clean_tags = _build_clean_metadata(
            {**script_data, "title": title, "seo_description": description},
            genre_cfg,
            trend_keyword,
        )
        final_title, final_description, final_comment = validate_final_upload_metadata(
            clean_title,
            clean_description,
            comment,
        )
        if self._real_uploader is None:
            self._real_uploader = getattr(self.bot, "upload_to_youtube", None)
        if not callable(self._real_uploader):
            raise RuntimeError("YouTube uploader is not available.")
        result = self._real_uploader(
            video_path,
            script_data,
            genre_cfg,
            publish_mode,
            trend_keyword,
            title_override=final_title,
            description_override=final_description,
            comment_override=final_comment,
        )
        if not result:
            raise RuntimeError("YouTube uploader returned no video ID.")
        return result


def _validate_selected_story(selected_story: Dict[str, Any]) -> Dict[str, Any]:
    """Require production input to come from the explicit discovery selection gate."""
    if not isinstance(selected_story, dict):
        raise ValueError("Production is blocked: an explicitly selected discovered story is required.")

    title = str(selected_story.get("title") or "").strip()
    story_key = str(selected_story.get("story_key") or "").strip()
    try:
        discovery_rank = int(selected_story.get("discovery_rank"))
    except (TypeError, ValueError):
        discovery_rank = None

    if not title:
        raise ValueError("Production is blocked: selected story title is missing.")
    if discovery_rank not in range(1, MAX_DISCOVERY_CANDIDATES + 1) or not story_key:
        raise ValueError(
            f"Production is blocked: story must be selected from the verified {MAX_DISCOVERY_CANDIDATES}-candidate discovery pool."
        )
    return dict(selected_story)
