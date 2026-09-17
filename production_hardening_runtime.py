"""Production hardening for scene-count integrity and live workflow progress."""
from __future__ import annotations

import inspect
import os
import sqlite3
import threading
from typing import Any


_MIN_SCENES = {"regular": 5, "trending": 5, "tech_reviews": 5, "top5": 7, "cricket": 5}
_MAX_SCENES = {"regular": 8, "trending": 8, "tech_reviews": 8, "top5": 7, "cricket": 8}


def _scene_count(format_mode: str) -> tuple[int, int]:
    mode = str(format_mode or "regular").lower()
    return _MIN_SCENES.get(mode, 5), _MAX_SCENES.get(mode, 8)


def _repair_scene_count(bot, result: dict[str, Any], story_data: dict[str, Any], language_cfg: dict[str, Any], genre_key: str, format_mode: str) -> dict[str, Any]:
    """Never let a cleaned script reach rendering below the production minimum."""
    minimum, maximum = _scene_count(format_mode)
    scenes = result.get("script") if isinstance(result, dict) else None
    if isinstance(scenes, list) and minimum <= len(scenes) <= maximum:
        return result

    print(
        f"   [Script Hardening] Scene contract failed: got {len(scenes) if isinstance(scenes, list) else 0}; "
        f"required {minimum}-{maximum}. Rebuilding from source-grounded fallback.",
        flush=True,
    )
    try:
        from script_runtime import _extractive_script_fallback, clean_script_data, validate_content_density
        fallback = _extractive_script_fallback(story_data, language_cfg, genre_key, format_mode)
        fallback, _diag = clean_script_data(fallback, story_data, format_mode)
        ok, reason = validate_content_density(fallback, story_data, format_mode)
        if not ok:
            raise ValueError(reason)
        fallback_scenes = list(fallback.get("script") or [])
        if len(fallback_scenes) >= minimum:
            if len(fallback_scenes) > maximum:
                fallback_scenes = fallback_scenes[:maximum]
            fallback["script"] = fallback_scenes
            fallback["fallback_reason"] = "scene_count_contract"
            return fallback
    except Exception as exc:
        print(f"   [Script Hardening] Source-grounded repair failed: {type(exc).__name__}: {exc}", flush=True)

    raise ValueError(
        f"Production script rejected: {len(scenes) if isinstance(scenes, list) else 0} scenes after cleanup; "
        f"required {minimum}-{maximum}. Refusing to render a one-scene Short."
    )


def _patch_script_pipeline(bot) -> None:
    current = getattr(bot, "write_script", None)
    if not callable(current) or getattr(current, "_scene_contract_bound", False):
        return
    run_robot = getattr(bot, "run_robot", None)
    globals_dict = getattr(run_robot, "__globals__", {}) if run_robot is not None else {}

    def guarded_write(story_data, language_cfg, genre_key, conn, format_mode):
        result = current(story_data, language_cfg, genre_key, conn, format_mode)
        if not isinstance(result, dict):
            raise ValueError("Script generation returned no usable dictionary.")
        repaired = _repair_scene_count(bot, result, story_data, language_cfg, genre_key, format_mode)
        print(f"   [Script Hardening] Final scene count: {len(repaired.get('script') or [])}", flush=True)
        return repaired

    guarded_write._scene_contract_bound = True
    bot.write_script = guarded_write
    if globals_dict:
        globals_dict["write_script"] = guarded_write


def _patch_progress_wrappers(bot) -> None:
    """Replace the legacy dashboard wrappers with async-correct production wrappers."""
    try:
        from workflow_runtime import WorkflowController
    except Exception as exc:
        print(f"   [Progress Hardening] WorkflowController unavailable: {type(exc).__name__}: {exc}", flush=True)
        return

    if getattr(WorkflowController, "_async_progress_bound", False):
        return

    def _install(self):
        if self._patched:
            return
        run_robot = getattr(self.bot, "run_robot", None)
        if run_robot is None:
            raise RuntimeError("Legacy run_robot() is not available.")
        globals_dict = getattr(run_robot, "__globals__", {})

        original_write = globals_dict.get("write_script")
        if callable(original_write):
            def write_wrapper(*args, **kwargs):
                self._reporter("script", 28, "Writing and checking the selected story…")
                result = original_write(*args, **kwargs)
                if isinstance(result, dict):
                    with self._lock:
                        self.state.script_data = result
                    count = len(result.get("script") or [])
                    self._reporter("script", 38, f"Script complete — {count} scenes passed the production contract.")
                return result
            globals_dict["write_script"] = write_wrapper

        original_audio = globals_dict.get("generate_voiceover_and_timestamps")
        if callable(original_audio):
            async def audio_wrapper(*args, **kwargs):
                self._reporter("audio", 42, "Generating narration and word timings…")
                result = await original_audio(*args, **kwargs) if inspect.iscoroutinefunction(original_audio) else original_audio(*args, **kwargs)
                audio_paths = result[0] if isinstance(result, tuple) and result else []
                self._reporter("audio", 53, f"Narration complete — {len(audio_paths) if isinstance(audio_paths, list) else 0} scene audio files ready.")
                return result
            globals_dict["generate_voiceover_and_timestamps"] = audio_wrapper

        original_visuals = globals_dict.get("process_visuals_async")
        if callable(original_visuals):
            async def visuals_wrapper(*args, **kwargs):
                script_data = args[0] if args else kwargs.get("script_data") or {}
                total = len(script_data.get("script") or []) if isinstance(script_data, dict) else 0
                self._reporter("visuals", 56, f"Sourcing and verifying visuals for {total} scenes…")
                result = await original_visuals(*args, **kwargs) if inspect.iscoroutinefunction(original_visuals) else original_visuals(*args, **kwargs)
                self._reporter("visuals", 75, f"Visual package complete — {len(result) if isinstance(result, list) else 0} scene packages ready.")
                return result
            globals_dict["process_visuals_async"] = visuals_wrapper

        original_compile = globals_dict.get("compile_video")
        if callable(original_compile):
            def compile_wrapper(*args, **kwargs):
                self._reporter("render", 78, "Rendering scenes, subtitles and branding…")
                result = original_compile(*args, **kwargs)
                self._reporter("render", 94, "Final video rendered. Preparing final QC…")
                if isinstance(result, str) and os.path.isfile(result):
                    with self._lock:
                        self.state.video_path = result
                return result
            globals_dict["compile_video"] = compile_wrapper

        real_upload = getattr(self.bot, "upload_to_youtube", None)
        if callable(real_upload):
            self._real_uploader = real_upload
            def production_blocked_upload(*args, **kwargs):
                self._reporter("qc", 98, "Video ready. Waiting for your final QC and upload decision.")
                print("   [Workflow] Automatic upload blocked. Manual QC is required.", flush=True)
                return "PENDING_MANUAL_UPLOAD"
            globals_dict["upload_to_youtube"] = production_blocked_upload
        self._patched = True

    WorkflowController._install_production_wrappers = _install
    WorkflowController._async_progress_bound = True
    print("   [Progress Hardening] Async-aware production progress wrappers installed.", flush=True)


def install_production_hardening(bot) -> None:
    _patch_script_pipeline(bot)
    _patch_progress_wrappers(bot)
