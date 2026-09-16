"""Bind runtime patches to the actual globals used by the legacy factory."""

import functools
import json
import os
import threading
import traceback

from dashboard_theme import apply_dashboard_theme

_VISUAL_CACHE_STATE = threading.local()


def _install_moviepy_compatibility():
    """Expose MoviePy v2 classes at the root package for the legacy renderer."""
    try:
        import moviepy
        from moviepy.video.VideoClip import ImageClip
        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip, concatenate_videoclips
        from moviepy.audio.io.AudioFileClip import AudioFileClip
        from moviepy.audio.AudioClip import CompositeAudioClip
        exports = {
            "ImageClip": ImageClip, "VideoFileClip": VideoFileClip,
            "CompositeVideoClip": CompositeVideoClip,
            "concatenate_videoclips": concatenate_videoclips,
            "AudioFileClip": AudioFileClip, "CompositeAudioClip": CompositeAudioClip,
        }
        installed = []
        for name, value in exports.items():
            if not hasattr(moviepy, name):
                setattr(moviepy, name, value)
                installed.append(name)
        if installed:
            print("   [Bindings] MoviePy compatibility exports installed: " + ", ".join(installed), flush=True)
        else:
            print("   [Bindings] MoviePy compatibility exports already available.", flush=True)
        return True
    except Exception as exc:
        print(f"   [Bindings] MoviePy compatibility bridge unavailable: {type(exc).__name__}: {exc}", flush=True)
        return False


def _install_authoritative_person_sources(bot):
    """Attach direct Wikipedia/Commons image fetchers used by PERSON sourcing."""
    try:
        from person_source_runtime import fetch_wikipedia_person_image, fetch_wikimedia_commons_image
        if not callable(getattr(bot, "fetch_wiki_person_image", None)):
            bot.fetch_wiki_person_image = fetch_wikipedia_person_image
        if not callable(getattr(bot, "fetch_wikimedia_commons", None)):
            bot.fetch_wikimedia_commons = fetch_wikimedia_commons_image
        print("   [Bindings] Authoritative person image sources installed: Wikipedia + Wikimedia Commons.", flush=True)
        return True
    except Exception as exc:
        print(f"   [Bindings] Authoritative person image sources unavailable: {type(exc).__name__}: {exc}", flush=True)
        return False


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
    if current is None or getattr(current, "_cached_trend_signal", False): return current
    @functools.lru_cache(maxsize=128)
    def cached(keyword): return current(keyword)
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
    if current is None or getattr(current, "_hard_reject_safe", False): return current
    def safe_process(scored_data, batch_stories, bonuses, last_genre, format_mode):
        scored_count = len(scored_data) if isinstance(scored_data, list) else 0
        story_count = len(batch_stories) if isinstance(batch_stories, list) else 0
        print(f"   [Editorial Diagnostics] Received {story_count} stories and {scored_count} Groq score records.", flush=True)
        if not isinstance(scored_data, list) or not scored_data:
            print("   [Editorial Diagnostics] STOP: Groq returned no usable score records.", flush=True)
            return []
        hard_reject_count = 0
        risk_reject_count = 0
        malformed_count = 0
        risk_values = []
        for index, story in enumerate(batch_stories if isinstance(batch_stories, list) else []):
            if index >= len(scored_data) or not isinstance(scored_data[index], dict):
                malformed_count += 1
                continue
            scores = scored_data[index]
            try: risk = float(scores.get("monetization_risk", 5))
            except (TypeError, ValueError): risk = 5.0
            risk_values.append(risk)
            if scores.get("hard_reject", False): hard_reject_count += 1
            if risk >= 8: risk_reject_count += 1
        print("   [Editorial Diagnostics] Rejection inputs: " + f"hard_reject={hard_reject_count}, monetization_risk>=8={risk_reject_count}, malformed/missing={malformed_count}, risks={risk_values}", flush=True)
        result = current(scored_data, batch_stories, bonuses, last_genre, format_mode)
        if not result:
            print("   [Editorial Diagnostics] Corrected scorer returned 0 candidates.", flush=True)
            return []
        allowed = []
        for index, story in enumerate(batch_stories):
            if index >= len(scored_data) or not isinstance(story, dict): continue
            scores = scored_data[index]
            if not isinstance(scores, dict): continue
            try: risk = float(scores.get("monetization_risk", 5))
            except (TypeError, ValueError): continue
            if scores.get("hard_reject", False) or risk >= 8: continue
            allowed.append(story)
        allowed_ids = {id(item) for item in allowed}
        filtered = [item for item in result if id(item) in allowed_ids]
        print(f"   [Editorial Diagnostics] Corrected scorer candidates={len(result)}; after hard/risk gate={len(filtered)}.", flush=True)
        if not filtered and result:
            print("   [Editorial Diagnostics] WARNING: all scored candidates were removed by the final safety gate.", flush=True)
        return filtered
    safe_process._hard_reject_safe = True
    bot.process_scored_candidates = safe_process
    return safe_process


def _wrap_editorial_provider_usage(bot):
    current = getattr(bot, "editorial_gate_batch", None)
    if current is None or getattr(current, "_gemini_editorial_guarded", False): return current
    def guarded(stories, bonuses, last_genre, format_mode):
        original_key = getattr(bot, "GEMINI_API_KEY", None)
        bot.GEMINI_API_KEY = None
        try: return current(stories, bonuses, last_genre, format_mode)
        finally: bot.GEMINI_API_KEY = original_key
    guarded._gemini_editorial_guarded = True
    bot.editorial_gate_batch = guarded
    return guarded


def _patch_research_pipeline(bot):
    """Activate the multi-source evidence pass before content-density script wrapping."""
    try:
        from research_runtime import patch_research_pipeline
        return patch_research_pipeline(bot)
    except Exception as exc:
        print(f"   [Bindings] Multi-source research runtime unavailable: {exc}", flush=True)
        return bot


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
    """Bind patched callables into the actual globals used by the legacy factory."""
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
    _patch_subtitles(bot)
    _patch_youtube_creator_comments(bot)
    _install_authoritative_person_sources(bot)
    _install_visual_cache_safety()

    namespace = run_robot.__globals__
    names = ("gather_and_filter_stories", "editorial_gate_batch", "process_scored_candidates", "validate_script", "self_critique_pass", "write_script", "generate_voiceover_and_timestamps", "process_visuals_async", "fetch_scene_asset", "get_trend_signal_bonus", "auto_pilot_selection", "run_analytics_sweep", "token_overlap_ratio", "upload_to_youtube", "generate_karaoke_clip")
    bound = []
    for name in names:
        value = getattr(bot, name, None)
        if value is not None:
            namespace[name] = value
            bound.append(name)
    original_process_visuals = getattr(bot, "process_visuals_async", None)
    if original_process_visuals is not None and not getattr(original_process_visuals, "_traceback_bound", False):
        async def process_visuals_with_traceback(*args, **kwargs):
            try: return await original_process_visuals(*args, **kwargs)
            except BaseException:
                print("   [Bindings] FULL TRACEBACK FROM process_visuals_async:", flush=True)
                traceback.print_exc()
                raise
        process_visuals_with_traceback._traceback_bound = True
        bot.process_visuals_async = process_visuals_with_traceback
        namespace["process_visuals_async"] = process_visuals_with_traceback
        bound.append("process_visuals_async(traceback)")
    original_compile_video = getattr(bot, "compile_video", None)
    if original_compile_video is not None and not getattr(original_compile_video, "_traceback_bound", False):
        def compile_video_with_traceback(*args, **kwargs):
            _install_moviepy_compatibility()
            try: return original_compile_video(*args, **kwargs)
            except BaseException:
                print("   [Bindings] FULL TRACEBACK FROM compile_video:", flush=True)
                traceback.print_exc()
                raise
        compile_video_with_traceback._traceback_bound = True
        bot.compile_video = compile_video_with_traceback
        namespace["compile_video"] = compile_video_with_traceback
        bound.append("compile_video(traceback)")
    try:
        import factory_runtime
        if hasattr(factory_runtime, "_vignette"):
            namespace["_vignette"] = factory_runtime._vignette
            bound.append("_vignette")
    except Exception as exc:
        print(f"   [Bindings] Render dependency binding skipped: {exc}", flush=True)
    print("   [Bindings] Legacy factory globals bound to active runtime patches: " + ", ".join(dict.fromkeys(bound)), flush=True)
    return bot


def harden_editorial_defaults(bot):
    """Remove deterministic contradictions between prompts, hooks and personas."""
    bot.HOOK_STYLES_REGISTRY["Urgent Warning"] = [
        "A new development just changed the situation in a measurable way.",
        "Here is the documented detail that changes this update.",
        "The latest facts show a change worth understanding.",
    ]
    bot.HOOK_STYLES_REGISTRY["Absurd Reality"] = [
        "The facts behind this development are stranger than they first appear.",
        "This sounds unlikely, but the documented sequence is real.",
        "One overlooked detail makes this story more surprising.",
    ]
    for persona in getattr(bot, "PERSONA_PROFILES", {}).values():
        persona["catchphrases"] = []
    return bot
