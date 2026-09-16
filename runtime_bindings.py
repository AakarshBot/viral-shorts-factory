"""Bind runtime patches to the actual globals used by the legacy factory."""

import functools
import traceback


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


def _wrap_scored_candidates(bot):
    current = getattr(bot, "process_scored_candidates", None)
    if current is None or getattr(current, "_hard_reject_safe", False):
        return current

    def safe_process(scored_data, batch_stories, bonuses, last_genre, format_mode):
        result = current(scored_data, batch_stories, bonuses, last_genre, format_mode)
        if not result:
            return []

        # The dashboard scorer historically returned the entire batch when all
        # candidates were rejected. That silently bypassed hard-reject logic.
        # Filter the returned candidates against the original model decisions.
        allowed = []
        for index, story in enumerate(batch_stories):
            if index >= len(scored_data) or not isinstance(story, dict):
                continue
            scores = scored_data[index]
            if not isinstance(scores, dict):
                continue
            try:
                risk = float(scores.get("monetization_risk", 5))
            except (TypeError, ValueError):
                continue
            if scores.get("hard_reject", False) or risk >= 8:
                continue
            allowed.append(story)

        allowed_ids = {id(item) for item in allowed}
        filtered = [item for item in result if id(item) in allowed_ids]
        return filtered

    safe_process._hard_reject_safe = True
    bot.process_scored_candidates = safe_process
    return safe_process


def bind_dashboard_patches(bot):
    """Bind patched callables into ultimate_bot's compiled function globals."""
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
    _wrap_scored_candidates(bot)

    namespace = run_robot.__globals__
    names = (
        "gather_and_filter_stories",
        "editorial_gate_batch",
        "process_scored_candidates",
        "validate_script",
        "self_critique_pass",
        "generate_voiceover_and_timestamps",
        "process_visuals_async",
        "fetch_scene_asset",
        "get_trend_signal_bonus",
        "auto_pilot_selection",
        "run_analytics_sweep",
        "token_overlap_ratio",
    )
    bound = []
    for name in names:
        value = getattr(bot, name, None)
        if value is not None:
            namespace[name] = value
            bound.append(name)

    original_process_visuals = getattr(bot, "process_visuals_async", None)
    if original_process_visuals is not None and not getattr(original_process_visuals, "_traceback_bound", False):
        async def process_visuals_with_traceback(*args, **kwargs):
            try:
                return await original_process_visuals(*args, **kwargs)
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
            try:
                return original_compile_video(*args, **kwargs)
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

    print(
        "   [Bindings] Legacy factory globals bound to active runtime patches: "
        + ", ".join(dict.fromkeys(bound)),
        flush=True,
    )
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

    hype = bot.PERSONA_PROFILES.get("HYPE COMMENTATOR")
    if hype:
        hype["catchphrases"] = [
            "Here is the key development.",
            "The latest facts are worth a closer look.",
            "This development deserves attention.",
        ]
    return bot
