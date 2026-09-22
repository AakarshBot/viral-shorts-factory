"""Newsroom-style staged workflow for the Viral Shorts Factory."""
from __future__ import annotations

import re
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from youtube_comment_runtime import _build_clean_metadata, build_pinned_comment
from db_runtime import run_robot_with_exact_identity
from db_architecture import migrate_vault, update_run_record

WORKFLOW_VERSION = "2026-09-16-newsroom-v2"
MAX_DISCOVERY_CANDIDATES = 28
_PROCESS_PRODUCTION_LOCK = threading.Lock()


def _run_production_runner(bot, web_config: Dict[str, Any]):
    """Execute the production runner exactly once with the identity bridge."""
    production_runner = getattr(bot, "_vsf_canonical_run_robot", None) or getattr(bot, "run_robot", None)
    if not callable(production_runner):
        raise RuntimeError("Legacy run_robot() is not available.")
    if getattr(production_runner, "_exact_identity_runner", False):
        return production_runner(web_config=web_config)
    return run_robot_with_exact_identity(bot, web_config=web_config)

FORMAT_OPTIONS = {
    "Deep Dive": "regular",
    "Top-5": "top5",
    "Cricket": "cricket",
}

CRICKET_CATEGORIES = {
    "India / Asia": {
        "key": "cricket_india_asia",
        "query": "(India OR Indian OR BCCI OR IPL OR WPL OR Pakistan OR Sri Lanka OR Bangladesh) (cricket OR Test OR ODI OR T20) (match OR result OR squad OR selection OR injury OR record OR series OR final OR win OR loss)",
        "rss": "https://news.google.com/rss/search?q=India+Cricket+OR+Pakistan+Cricket+OR+BCCI+OR+PCB&hl=en-IN&gl=IN&ceid=IN:en",
    },
    "Global": {
        "key": "cricket_global",
        "query": "(ICC OR Australia Cricket OR England Cricket OR New Zealand Cricket OR South Africa Cricket OR West Indies Cricket OR Test Cricket OR T20 Cricket) (match OR result OR squad OR series OR final OR record OR win OR loss OR tournament)",
        "rss": "https://news.google.com/rss/search?q=ICC+Cricket+OR+Australia+Cricket+OR+England+Cricket+OR+Test+Cricket&hl=en-IN&gl=IN&ceid=IN:en",
    },
    "AI-assisted top story in cricket": {
        "key": "cricket_ai_today",
        "query": "(Cricket OR ICC OR BCCI OR IPL OR WPL OR PSL OR Big Bash) (match OR result OR squad OR selection OR record OR series OR final OR win OR loss OR tournament)",
        "rss": "https://news.google.com/rss/search?q=Cricket+OR+ICC+OR+BCCI+OR+Test+Cricket+OR+T20+Cricket&hl=en-IN&gl=IN&ceid=IN:en",
    },
}



@dataclass
class WorkflowState:
    stage: str = "idle"
    percent: int = 0
    message: str = "Ready."
    run_id: str = ""
    selected_story: Optional[Dict[str, Any]] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    script_data: Optional[Dict[str, Any]] = None
    audio_paths: List[str] = field(default_factory=list)
    word_timings: List[List[Dict[str, Any]]] = field(default_factory=list)
    format_mode: str = ""
    video_path: str = ""
    final_metadata: Dict[str, str] = field(default_factory=dict)
    uploaded_video_id: str = ""
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
        # Production wrappers bind into run_robot's module globals. A new run
        # must reinstall them because a Streamlit full rerun can legitimately
        # rebind those globals while the previous run is idle.
        self._patched = False

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
                "audio_paths": list(self.state.audio_paths),
                "word_timings": [list(items) for items in self.state.word_timings],
                "format_mode": self.state.format_mode,
                "video_path": self.state.video_path,
                "final_metadata": dict(self.state.final_metadata),
                "uploaded_video_id": self.state.uploaded_video_id,
                "error": self.state.error,
                "thread_alive": self.state.thread_alive,
                "completed": self.state.completed,
            }

    def _reporter(self, stage: str, percent: int, message: str):
        self.update(stage, percent, message)

    def _prepare_production_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Hook for dashboard/host controllers that need a core workflow checkpoint."""
        return config

    def _install_production_wrappers(self):
        """Install the single canonical production wrapper set for this controller."""
        from production_hardening_runtime import install_production_wrappers
        install_production_wrappers(self)

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

    def _record_uploaded_run(self, video_id: str, title: str = "", status: str = "UPLOADED") -> None:
        """Record an upload against the exact production run row."""
        import ultimate_bot

        row_id = getattr(self.bot, "_last_run_row_id", None)
        run_id = getattr(self.bot, "_last_run_run_id", None)
        if row_id is None or not run_id:
            return

        conn = sqlite3.connect(ultimate_bot.DB_PATH)
        try:
            migrate_vault(conn)
            row = conn.execute(
                "SELECT run_id FROM vault WHERE id = ?",
                (row_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Exact production row {row_id} was not found after upload.")
            if str(row[0] or "") != str(run_id):
                raise RuntimeError("Exact production row identity changed before upload was recorded.")
            fields = {
                "video_id": str(video_id),
                "status": str(status or "UPLOADED"),
            }
            if str(title or "").strip():
                fields["title_used"] = str(title).strip()
            update_run_record(conn, row_id, **fields)
        finally:
            conn.close()

    def _worker_started(self) -> None:
        """Hook for host controllers that need worker-thread setup."""

    def _worker_finished(self) -> None:
        """Hook for host controllers that need worker-thread cleanup."""

    def start_production(self, web_config: Dict[str, Any], selected_story: Dict[str, Any]):
        selected_story = _validate_selected_story(selected_story)
        if self.state.thread_alive:
            return
        if not _PROCESS_PRODUCTION_LOCK.acquire(blocking=False):
            raise RuntimeError(
                "Another production run is already active in this host process. "
                "Wait for it to finish before starting another run."
            )

        try:
            self.reset()
            config = dict(web_config)
            config = self._prepare_production_config(config)
            self._install_production_wrappers()
            with self._lock:
                self.state.selected_story = dict(selected_story)
                self.state.run_id = datetime.now(timezone.utc).strftime(
                    "run-%Y%m%d-%H%M%S-%f"
                )
                self.state.stage = "research"
                self.state.percent = 16
                self.state.message = "Researching multiple sources for the selected story…"
                self.state.thread_alive = True
                self.state.completed = False
                self.state.error = ""

            if config.get("cricket_pipeline") or config.get("display_format") == "Cricket":
                config["format_mode"] = "cricket"
            is_top5 = str(config.get("format_mode", "")).strip().lower() == "top5"
            config["publish_mode"] = "private"
            config["manual_qc_required"] = True
            self.bot._active_web_config = dict(config)
            with self._lock:
                self.state.format_mode = str(config.get("format_mode") or "").strip().lower()

        except Exception:
            _PROCESS_PRODUCTION_LOCK.release()
            raise

        def worker():
            try:
                self._worker_started()
                run_robot = getattr(self.bot, "_vsf_canonical_run_robot", None) or self.bot.run_robot
                globals_dict = getattr(run_robot, "__globals__", {})
                original_gather = globals_dict.get("gather_and_filter_stories")
                selected = dict(selected_story)

                def selected_gather(*args, **kwargs):
                    return [dict(selected)]

                def top5_gather(*args, **kwargs):
                    if not callable(original_gather):
                        return [dict(selected)]
                    pool = original_gather(*args, **kwargs) or []
                    selected_title = str(selected.get("title") or "").strip().casefold()
                    merged = [dict(selected)]
                    for story in pool:
                        if not isinstance(story, dict):
                            continue
                        if str(story.get("title") or "").strip().casefold() == selected_title:
                            continue
                        merged.append(dict(story))
                    return merged

                if original_gather is not None:
                    globals_dict["gather_and_filter_stories"] = top5_gather if is_top5 else selected_gather
                if not is_top5:
                    config["selected_story"] = dict(selected_story)
                try:
                    self._reporter("research", 18, "Selected story locked. Preparing the production pipeline…")
                    _run_production_runner(self.bot, config)
                finally:
                    if original_gather is not None:
                        globals_dict["gather_and_filter_stories"] = original_gather

                script = self.state.script_data or {}
                video_path = str(self.state.video_path or "").strip()
                if not isinstance(script, dict) or not script.get("script"):
                    raise RuntimeError(
                        "Production stopped before a usable script reached the dashboard."
                    )
                if not video_path or not os.path.isfile(video_path):
                    raise RuntimeError(
                        "Production stopped before a final video artifact was produced."
                    )
                scene_count = len(script.get("script") or [])
                if len(self.state.audio_paths) != scene_count:
                    raise RuntimeError(
                        "Production stopped with incomplete narration artifacts "
                        f"({len(self.state.audio_paths)}/{scene_count} audio tracks)."
                    )
                if len(self.state.word_timings) != scene_count or any(
                    not isinstance(items, list) or not items
                    for items in self.state.word_timings
                ):
                    raise RuntimeError(
                        "Production stopped with incomplete word-level narration timings."
                    )
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
                    last_percent = self.state.percent
                    self.state.error = f"{type(exc).__name__}: {exc}"
                    self.state.stage = "error"
                    self.state.percent = last_percent
                    self.state.message = "Run stopped with an error."
            finally:
                try:
                    self._worker_finished()
                except Exception:
                    pass
                _PROCESS_PRODUCTION_LOCK.release()
                with self._lock:
                    self.state.thread_alive = False

        try:
            threading.Thread(
                target=worker,
                name="viral-shorts-production",
                daemon=True,
            ).start()
        except Exception:
            _PROCESS_PRODUCTION_LOCK.release()
            raise

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
        if self.state.uploaded_video_id:
            raise RuntimeError(
                f"This production run has already been uploaded as {self.state.uploaded_video_id}. "
                "Start a new run before uploading again."
            )
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
        try:
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
        except Exception as exc:
            if type(exc).__name__ == "YouTubePublicVisibilityError":
                message = str(exc)
                match = re.search(r"accepted video\s+([A-Za-z0-9_-]+)", message)
                video_id = str(getattr(exc, "video_id", "") or (match.group(1) if match else "")).strip()
                if video_id:
                    with self._lock:
                        self.state.uploaded_video_id = video_id
                    try:
                        self._record_uploaded_run(
                            video_id,
                            title=final_title,
                            status="UPLOADED_PRIVATE",
                        )
                    except Exception as record_exc:
                        print(
                            f"   [Workflow] YouTube created {video_id} as private, but exact run history "
                            f"could not be updated: {type(record_exc).__name__}: {record_exc}",
                            flush=True,
                        )
                    raise RuntimeError(
                        f"YouTube accepted video {video_id}, but kept it private instead of public. "
                        f"Visibility detail: {message}. "
                        "The video already exists; do not retry this production run. "
                        "The current Google API project must be eligible/audited for public YouTube API uploads."
                    ) from exc
            raise

        if not result:
            raise RuntimeError("YouTube uploader returned no video ID.")

        with self._lock:
            self.state.uploaded_video_id = str(result)

        try:
            self._record_uploaded_run(str(result), title=final_title)
        except Exception as exc:
            # The video is already on YouTube; never report a successful upload as
            # failed solely because the local/ephemeral analytics ledger could not update.
            print(
                f"   [Workflow] Upload succeeded, but exact run history could not be updated: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
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
    max_rank = MAX_DISCOVERY_CANDIDATES
    if discovery_rank not in range(1, max_rank + 1) or not story_key:
        raise ValueError(
            f"Production is blocked: story must be selected from the verified {max_rank}-candidate discovery pool."
        )
    return dict(selected_story)
