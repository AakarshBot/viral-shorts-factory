"""Bind runtime patches to the authoritative production globals."""

import json
import os
from dashboard_theme import apply_dashboard_theme


def _install_visual_cache_safety():
    """Allow cache writes only when the caller explicitly marks the asset verified."""
    try:
        import visual_runtime
        if getattr(visual_runtime, "_verified_cache_safety_bound", False):
            return True
        original_save = getattr(visual_runtime, "save_to_cache", None)
        original_get = getattr(visual_runtime, "get_cached_asset", None)
        if not original_save or not original_get:
            raise RuntimeError(
                "Visual cache safety guard cannot install because the canonical cache API is incomplete."
            )

        def verified_only_save(
            bot,
            img_bytes,
            entity,
            visual_type,
            source_type,
            context="",
            verified=False,
        ):
            if not verified:
                print(
                    "   [Visual Cache] Skipping cache write: asset was not explicitly verified.",
                    flush=True,
                )
                return None
            path = original_save(
                bot,
                img_bytes,
                entity,
                visual_type,
                source_type,
                context,
            )
            if path:
                try:
                    meta_path = os.path.splitext(path)[0] + ".json"
                    with open(meta_path, "r", encoding="utf-8") as fh:
                        meta = json.load(fh)
                    meta["verified"] = True
                    meta["verification_version"] = 2
                    with open(meta_path, "w", encoding="utf-8") as fh:
                        json.dump(meta, fh, ensure_ascii=False, indent=2)
                except Exception as exc:
                    print(
                        f"   [Visual Cache] Verification metadata update failed: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
            return path

        def verified_only_get(bot, entity, visual_type, context=""):
            result = original_get(bot, entity, visual_type, context)
            if result == (None, None):
                return result
            image, path = result
            try:
                meta_path = os.path.splitext(path)[0] + ".json"
                with open(meta_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                if meta.get("verification_version") != 2 or meta.get("verified") is not True:
                    return None, None
            except Exception:
                return None, None
            return image, path

        visual_runtime.save_to_cache = verified_only_save
        visual_runtime.get_cached_asset = verified_only_get
        visual_runtime._verified_cache_safety_bound = True
        print("   [Bindings] Visual cache safety guard installed.", flush=True)
        return True
    except Exception as exc:
        raise RuntimeError(
            f"Visual cache safety guard could not be installed: {type(exc).__name__}: {exc}"
        ) from exc


def _wrap_content_first_visuals(bot):
    from visual_content_runtime import patch_content_first_visuals
    return patch_content_first_visuals(bot)


def _patch_audio_direction(bot):
    from audio_direction_runtime import patch_audio_direction
    return patch_audio_direction(bot)


def _patch_subtitles(bot):
    from subtitle_runtime import patch_subtitle_pipeline
    return patch_subtitle_pipeline(bot)




def _install_script_pipeline(bot):
    from script_router_runtime import install_script_pipeline
    return install_script_pipeline(bot)


def bind_dashboard_patches(bot):
    """Bind runtime patches to the authoritative production globals."""
    try:
        apply_dashboard_theme()
    except Exception as exc:
        print(f"   [Bindings] Dashboard theme unavailable: {type(exc).__name__}: {exc}", flush=True)

    run_robot = getattr(bot, "run_robot", None)
    if run_robot is None or not hasattr(run_robot, "__globals__"):
        print("   [Bindings] WARNING: run_robot globals unavailable.", flush=True)
        return bot

    current_validate = getattr(bot, "validate_script", None)
    if current_validate is not None and not getattr(current_validate, "_index_normalized", False):
        def validate(script_data, source_text, format_mode):
            if isinstance(script_data, dict) and script_data.get("recommended_title_index") == 0:
                script_data["recommended_title_index"] = 1
            return current_validate(script_data, source_text, format_mode)

        validate._index_normalized = True
        bot.validate_script = validate

    try:
        from editorial_runtime import patch_editorial_scoring
        patch_editorial_scoring(bot)
    except Exception as exc:
        print(f"   [Bindings] Corrected editorial scoring unavailable: {exc}", flush=True)
    _install_script_pipeline(bot)
    _wrap_content_first_visuals(bot)
    _patch_audio_direction(bot)
    try:
        from channel_intelligence_runtime import install_channel_intelligence_dialog
        install_channel_intelligence_dialog()
    except Exception as exc:
        print(f"   [Bindings] Channel intelligence runtime unavailable: {type(exc).__name__}: {exc}", flush=True)
    from production_hardening_runtime import install_production_hardening
    install_production_hardening(bot)
    _patch_subtitles(bot)

    # Keep run_robot's production globals aligned with the live bot bindings.
    # Several pipeline stages are invoked by functions defined in ultimate_bot.py,
    # so rebinding bot attributes alone is not sufficient for the module namespace.
    namespace = run_robot.__globals__
    names = (
        "gather_and_filter_stories", "editorial_gate_batch", "process_scored_candidates", "validate_script",
        "self_critique_pass", "write_script", "generate_voiceover_and_timestamps", "process_visuals_async", "compile_video",
        "auto_pilot_selection", "run_analytics_sweep",
        "upload_to_youtube", "generate_karaoke_clip",
    )

    # Capture the first fully-patched runtime callable set before production
    # controllers add per-run dashboard wrappers. Subsequent Streamlit reruns
    # must rebind these canonical callables, never yesterday's wrapper chain.
    canonical = getattr(bot, "_vsf_canonical_runtime_bindings", None)
    if not isinstance(canonical, dict):
        canonical = {}
    for name in names:
        current = getattr(bot, name, None)
        if name not in canonical and callable(current):
            canonical[name] = current
    bot._vsf_canonical_runtime_bindings = canonical

    bound = []
    for name in names:
        value = canonical.get(name) or getattr(bot, name, None)
        if value is not None:
            namespace[name] = value
            bound.append(name)
    print("   [Bindings] Production runtime globals bound: " + ", ".join(bound), flush=True)

    _install_visual_cache_safety()
