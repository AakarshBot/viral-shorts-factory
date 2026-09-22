"""Production hardening for scene-count integrity, visual entity integrity and live workflow progress."""
from __future__ import annotations

import inspect
import os



def _install_authoritative_visual_query_planner() -> None:
    """Verify that the canonical visual planner is still installed."""
    try:
        import visual_strategy_runtime
        current = getattr(visual_strategy_runtime, "build_deep_queries", None)
        if not getattr(current, "_authoritative_locked_subject_planner", False):
            raise RuntimeError("authoritative visual query planner is not installed")
        print(
            "   [Visual Strategy Hardening] Strict single-query visual planner preserved.",
            flush=True,
        )
    except Exception as exc:
        print(
            f"   [Visual Strategy Hardening] Strict planner check failed: {type(exc).__name__}: {exc}",
            flush=True,
        )


def _patch_script_pipeline(bot) -> None:
    """Verify the canonical script router instead of adding another wrapper."""
    current = getattr(bot, "write_script", None)
    if callable(current) and getattr(current, "_canonical_script_pipeline", False):
        return
    raise RuntimeError(
        "Canonical script router is not installed; refusing to add a legacy script wrapper."
    )

def install_production_wrappers(controller) -> None:
    """Install the canonical live workflow wrappers for one controller."""
    if controller._patched:
        return
    run_robot = getattr(controller.bot, "run_robot", None)
    if run_robot is None:
        raise RuntimeError("Legacy run_robot() is not available.")
    globals_dict = getattr(run_robot, "__globals__", {})
    canonical = getattr(controller.bot, "_vsf_canonical_runtime_bindings", {})
    if not isinstance(canonical, dict):
        canonical = {}

    def _same_controller_wrapper(current):
        return (
            callable(current)
            and getattr(current, "_workflow_progress_wrapper", False)
            and getattr(current, "_workflow_controller", None) is controller
        )

    original_write = canonical.get("write_script") or globals_dict.get("write_script")
    if callable(original_write):
        if not _same_controller_wrapper(original_write):
            inner_write = original_write

            def write_wrapper(*args, **kwargs):
                controller._reporter("script", 28, "Writing and checking the selected story…")
                result = inner_write(*args, **kwargs)
                if isinstance(result, dict):
                    with controller._lock:
                        controller.state.script_data = result
                    count = len(result.get("script") or [])
                    controller._reporter("script", 38, f"Script complete — {count} scenes passed the production contract.")
                return result

            write_wrapper._workflow_progress_wrapper = True
            write_wrapper._workflow_controller = controller
            globals_dict["write_script"] = write_wrapper

    original_audio = canonical.get("generate_voiceover_and_timestamps") or globals_dict.get("generate_voiceover_and_timestamps")
    if callable(original_audio):
        if not _same_controller_wrapper(original_audio):
            inner_audio = original_audio

            async def audio_wrapper(*args, **kwargs):
                controller._reporter("audio", 42, "Generating narration and word timings…")
                result = (
                    await inner_audio(*args, **kwargs)
                    if inspect.iscoroutinefunction(inner_audio)
                    else inner_audio(*args, **kwargs)
                )
                audio_paths = result[0] if isinstance(result, tuple) and result else []
                word_timings = result[1] if isinstance(result, tuple) and len(result) > 1 else []
                with controller._lock:
                    controller.state.audio_paths = [
                        str(path) for path in audio_paths
                        if str(path or "").strip()
                    ] if isinstance(audio_paths, list) else []
                    controller.state.word_timings = [
                        list(items) if isinstance(items, list) else []
                        for items in (word_timings if isinstance(word_timings, list) else [])
                    ]
                controller._reporter(
                    "audio",
                    53,
                    f"Narration complete — {len(controller.state.audio_paths)} scene audio files ready.",
                )
                return result

            audio_wrapper._workflow_progress_wrapper = True
            audio_wrapper._workflow_controller = controller
            globals_dict["generate_voiceover_and_timestamps"] = audio_wrapper

    original_visuals = canonical.get("process_visuals_async") or globals_dict.get("process_visuals_async")
    if callable(original_visuals):
        if not _same_controller_wrapper(original_visuals):
            inner_visuals = original_visuals

            async def visuals_wrapper(*args, **kwargs):
                script_data = args[0] if args else kwargs.get("script_data") or {}
                total = len(script_data.get("script") or []) if isinstance(script_data, dict) else 0
                controller._reporter("visuals", 56, f"Sourcing and verifying visuals for {total} scenes…")
                result = (
                    await inner_visuals(*args, **kwargs)
                    if inspect.iscoroutinefunction(inner_visuals)
                    else inner_visuals(*args, **kwargs)
                )
                controller._reporter("visuals", 75, f"Visual package complete — {len(result) if isinstance(result, list) else 0} scene packages ready.")
                return result

            visuals_wrapper._workflow_progress_wrapper = True
            visuals_wrapper._workflow_controller = controller
            globals_dict["process_visuals_async"] = visuals_wrapper

    original_compile = canonical.get("compile_video") or globals_dict.get("compile_video")
    if callable(original_compile):
        if not _same_controller_wrapper(original_compile):
            inner_compile = original_compile

            def compile_wrapper(*args, **kwargs):
                controller._reporter("render", 78, "Rendering motion, word-highlight captions and branding…")
                result = inner_compile(*args, **kwargs)
                controller._reporter("render", 94, "Video rendered and loudness normalized. Preparing final QC…")
                if isinstance(result, str) and os.path.isfile(result):
                    with controller._lock:
                        controller.state.video_path = result
                return result

            compile_wrapper._workflow_progress_wrapper = True
            compile_wrapper._workflow_controller = controller
            globals_dict["compile_video"] = compile_wrapper

    real_upload = canonical.get("upload_to_youtube") or getattr(controller.bot, "upload_to_youtube", None)
    if callable(real_upload):
        if getattr(real_upload, "_workflow_upload_blocker", False):
            controller._real_uploader = getattr(
                real_upload, "_workflow_real_uploader", None
            )
        else:
            controller._real_uploader = real_upload

        if not getattr(real_upload, "_workflow_upload_blocker", False):
            inner_upload = real_upload

            def production_blocked_upload(*args, **kwargs):
                controller._reporter("qc", 98, "Video ready. Waiting for your final QC and upload decision.")
                print("   [Workflow] Automatic upload blocked. Manual QC is required.", flush=True)
                return "PENDING_MANUAL_UPLOAD"

            production_blocked_upload._workflow_upload_blocker = True
            production_blocked_upload._workflow_controller = controller
            production_blocked_upload._workflow_real_uploader = inner_upload
            globals_dict["upload_to_youtube"] = production_blocked_upload

    controller._patched = True

def install_production_hardening(bot) -> None:
    _install_authoritative_visual_query_planner()
    _patch_script_pipeline(bot)
    # Progress wrappers are installed by WorkflowController when production starts.
