"""Bind runtime patches to the authoritative production globals."""

import functools
import json
import os
import threading

from dashboard_theme import apply_dashboard_theme

_VISUAL_CACHE_STATE = threading.local()


def _coerce_bool(value, default=False):
    """Safely coerce AI-returned booleans without treating 'false' as truthy."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off", ""}:
        return False
    return default


def _fallback_editorial_scores(story):
    """Neutral local scores for a missing/malformed provider record."""
    text_len = len(str(story.get("text", "") or "").split())
    narrative = 6.5 if text_len >= 45 else 6.0 if text_len >= 20 else 5.5
    velocity = float(story.get("velocity_score", 0.0) or 0.0)
    hook = min(8.0, 5.5 + velocity * 0.35)
    shelf_life = min(7.0, 5.0 + float(story.get("trend_bonus", 0.0) or 0.0) * 0.25)
    return {
        "hook_strength": round(hook, 2),
        "narrative_completeness": round(narrative, 2),
        "audience_fit": 6.0,
        "monetization_risk": 5.0,
        "shelf_life": round(shelf_life, 2),
        "hard_reject": False,
        "one_line_reasoning": "Local neutral fallback used because the editorial score record was missing or malformed.",
    }


def _normalise_editorial_records(scored_data, batch_stories):
    """Return one safe score record per story while preserving real provider scores."""
    records = list(scored_data) if isinstance(scored_data, list) else []
    normalised = []
    for index, story in enumerate(batch_stories or []):
        raw = records[index] if index < len(records) and isinstance(records[index], dict) else None
        if raw is None:
            normalised.append(_fallback_editorial_scores(story))
            continue
        clean = dict(raw)
        clean["hard_reject"] = _coerce_bool(clean.get("hard_reject"), False)
        for field, default in (
            ("hook_strength", 5.0),
            ("narrative_completeness", 5.0),
            ("audience_fit", 5.0),
            ("monetization_risk", 5.0),
            ("shelf_life", 5.0),
        ):
            try:
                clean[field] = float(clean.get(field, default))
            except (TypeError, ValueError):
                clean[field] = default
        normalised.append(clean)
    return normalised


def _install_visual_cache_safety():
    """Cache only assets that actually passed the visual verification gate."""
    try:
        import visual_runtime
        if getattr(visual_runtime, "_verified_cache_safety_bound", False):
            return True
        original_gate = getattr(visual_runtime, "_strict_gate", None)
        original_save = getattr(visual_runtime, "save_to_cache", None)
        original_get = getattr(visual_runtime, "get_cached_asset", None)
        if not original_gate or not original_save or not original_get:
            return False

        def strict_gate_with_cache_state(*args, **kwargs):
            result = original_gate(*args, **kwargs)
            try:
                _VISUAL_CACHE_STATE.allow_write = bool(result[0])
            except Exception:
                _VISUAL_CACHE_STATE.allow_write = False
            return result

        def verified_only_save(bot, img_bytes, entity, visual_type, source_type, context=""):
            allowed = bool(getattr(_VISUAL_CACHE_STATE, "allow_write", False))
            _VISUAL_CACHE_STATE.allow_write = False
            if not allowed:
                print("   [Visual Cache] Skipping cache write: asset was not semantically verified.", flush=True)
                return None
            path = original_save(bot, img_bytes, entity, visual_type, source_type, context)
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
                    print(f"   [Visual Cache] Verification metadata update failed: {type(exc).__name__}: {exc}", flush=True)
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
                    print("   [Visual Cache] Ignoring legacy/unverified cache entry.", flush=True)
                    return None, None
            except Exception:
                return None, None
            return image, path

        strict_gate_with_cache_state._verified_cache_safety_bound = True
        verified_only_save._verified_cache_safety_bound = True
        verified_only_get._verified_cache_safety_bound = True
        visual_runtime._strict_gate = strict_gate_with_cache_state
        visual_runtime.save_to_cache = verified_only_save
        visual_runtime.get_cached_asset = verified_only_get
        visual_runtime._verified_cache_safety_bound = True
        print("   [Bindings] Visual cache safety guard installed.", flush=True)
        return True
    except Exception as exc:
        print(f"   [Bindings] Visual cache safety guard unavailable: {type(exc).__name__}: {exc}", flush=True)
        return False


def _wrap_trend_signal(bot):
    current = getattr(bot, "get_trend_signal_bonus", None)
    if current is None or getattr(current, "_cached_trend_signal", False):
        return current

    @functools.lru_cache(maxsize=128)
    def cached(keyword):
        return current(keyword)

    cached._cached_trend_signal = True
    bot.get_trend_signal_bonus = cached
    return cached


def _patch_editorial_scoring(bot):
    try:
        from editorial_runtime import patch_editorial_scoring
        return patch_editorial_scoring(bot)
    except Exception as exc:
        print(f"   [Bindings] Corrected editorial scoring unavailable: {exc}", flush=True)
        return bot


def _wrap_scored_candidates(bot):
    current = getattr(bot, "process_scored_candidates", None)
    if current is None or getattr(current, "_hard_reject_safe", False):
        return current

    def safe_process(scored_data, batch_stories, bonuses, last_genre, format_mode):
        scored_count = len(scored_data) if isinstance(scored_data, list) else 0
        story_count = len(batch_stories) if isinstance(batch_stories, list) else 0
        print(f"   [Editorial Diagnostics] Received {story_count} stories and {scored_count} Groq score records.", flush=True)
        if not isinstance(batch_stories, list) or not batch_stories:
            print("   [Editorial Diagnostics] STOP: no stories were available for editorial scoring.", flush=True)
            return []

        normalised = _normalise_editorial_records(scored_data, batch_stories)
        missing_count = max(0, story_count - scored_count)
        if missing_count:
            print(f"   [Editorial Diagnostics] Filled {missing_count} missing/malformed score record(s) with neutral local scores.", flush=True)

        hard_reject_count = sum(1 for scores in normalised if _coerce_bool(scores.get("hard_reject"), False))
        risk_values = []
        risk_reject_count = 0
        for scores in normalised:
            try:
                risk = float(scores.get("monetization_risk", 5.0))
            except (TypeError, ValueError):
                risk = 5.0
            risk_values.append(risk)
            if risk >= 8:
                risk_reject_count += 1
        print("   [Editorial Diagnostics] Rejection inputs: " + f"hard_reject={hard_reject_count}, monetization_risk>=8={risk_reject_count}, malformed/missing={missing_count}, risks={risk_values}", flush=True)

        result = current(normalised, batch_stories, bonuses, last_genre, format_mode)
        if not result:
            print("   [Editorial Diagnostics] Corrected scorer returned 0 candidates.", flush=True)
            return []

        rejected_ids = {
            id(batch_stories[index])
            for index, scores in enumerate(normalised)
            if index < len(batch_stories) and _coerce_bool(scores.get("hard_reject"), False)
        }
        filtered = [item for item in result if id(item) not in rejected_ids]
        print(f"   [Editorial Diagnostics] Corrected scorer candidates={len(result)}; after explicit hard-reject gate={len(filtered)}.", flush=True)
        return filtered

    safe_process._hard_reject_safe = True
    bot.process_scored_candidates = safe_process
    return safe_process


def _wrap_editorial_provider_usage(bot):
    current = getattr(bot, "editorial_gate_batch", None)
    if current is None or getattr(current, "_gemini_editorial_guarded", False):
        return current

    def guarded(stories, bonuses, last_genre, format_mode):
        original_key = getattr(bot, "GEMINI_API_KEY", None)
        bot.GEMINI_API_KEY = None
        try:
            return current(stories, bonuses, last_genre, format_mode)
        finally:
            bot.GEMINI_API_KEY = original_key

    guarded._gemini_editorial_guarded = True
    bot.editorial_gate_batch = guarded
    return guarded


def _patch_research_pipeline(bot):
    from research_runtime import patch_research_pipeline
    return patch_research_pipeline(bot)


def _wrap_content_dense_script(bot):
    try:
        from script_runtime import wrap_write_script
        return wrap_write_script(bot)
    except Exception as exc:
        print(f"   [Bindings] Script runtime unavailable: {exc}", flush=True)
        return getattr(bot, "write_script", None)


def _wrap_content_first_visuals(bot):
    try:
        from visual_content_runtime import patch_content_first_visuals
        return patch_content_first_visuals(bot)
    except Exception as exc:
        print(f"   [Bindings] Content-first visual runtime unavailable: {exc}", flush=True)
        return getattr(bot, "process_visuals_async", None)


def _patch_audio_direction(bot):
    try:
        from audio_direction_runtime import patch_audio_direction
        return patch_audio_direction(bot)
    except Exception as exc:
        print(f"   [Bindings] Audio direction patch unavailable: {exc}", flush=True)
        return getattr(bot, "generate_voiceover_and_timestamps", None)


def _patch_subtitles(bot):
    try:
        from subtitle_runtime import patch_subtitle_pipeline
        return patch_subtitle_pipeline(bot)
    except Exception as exc:
        print(f"   [Bindings] Subtitle runtime unavailable: {exc}", flush=True)
        return getattr(bot, "generate_karaoke_clip", None)


def _patch_youtube_creator_comments(bot):
    try:
        from youtube_comment_runtime import patch_youtube_upload
        return patch_youtube_upload(bot)
    except Exception as exc:
        print(f"   [Bindings] YouTube creator comment patch unavailable: {exc}", flush=True)
        return getattr(bot, "upload_to_youtube", None)


def bind_dashboard_patches(bot):
    """Bind runtime patch surfaces into the production call graph."""
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

    _wrap_trend_signal(bot)
    _patch_editorial_scoring(bot)
    _wrap_scored_candidates(bot)
    _wrap_editorial_provider_usage(bot)
    _patch_research_pipeline(bot)
    _wrap_content_dense_script(bot)
    _wrap_content_first_visuals(bot)
    _patch_audio_direction(bot)
    try:
        from final_qc_runtime import patch_workflow_qc
        patch_workflow_qc(bot)
    except Exception as exc:
        print(f"   [Bindings] Final QC runtime unavailable: {type(exc).__name__}: {exc}", flush=True)
    try:
        from channel_intelligence_runtime import install_channel_intelligence_dialog
        install_channel_intelligence_dialog()
    except Exception as exc:
        print(f"   [Bindings] Channel intelligence runtime unavailable: {type(exc).__name__}: {exc}", flush=True)
    try:
        from production_hardening_runtime import install_production_hardening
        install_production_hardening(bot)
    except Exception as exc:
        print(f"   [Bindings] Production hardening unavailable: {type(exc).__name__}: {exc}", flush=True)
    _patch_subtitles(bot)
    _patch_youtube_creator_comments(bot)

    # Keep run_robot's production globals aligned with the live bot bindings.
    # Several pipeline stages are invoked by functions defined in ultimate_bot.py,
    # so rebinding bot attributes alone is not sufficient for the module namespace.
    namespace = run_robot.__globals__
    names = (
        "gather_and_filter_stories", "editorial_gate_batch", "process_scored_candidates", "validate_script",
        "self_critique_pass", "write_script", "generate_voiceover_and_timestamps", "process_visuals_async", "compile_video",
        "get_trend_signal_bonus", "auto_pilot_selection", "run_analytics_sweep",
        "token_overlap_ratio", "upload_to_youtube", "generate_karaoke_clip",
    )
    bound = []
    for name in names:
        value = getattr(bot, name, None)
        if value is not None:
            namespace[name] = value
            bound.append(name)
    print("   [Bindings] Production runtime globals bound: " + ", ".join(bound), flush=True)

    _install_visual_cache_safety()
