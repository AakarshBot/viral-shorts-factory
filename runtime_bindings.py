"""Bind runtime patches to the actual globals used by the legacy factory.

The production bot keeps several functions as module-level globals inside
run_robot()/write_script(). Assigning only attributes on the imported module
object is not enough when those functions resolve names from __globals__.
This bridge makes the dashboard patches authoritative without rewriting the
large legacy pipeline.
"""


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

    # Some legacy render callables may have been imported into ultimate_bot's
    # globals. If so, make their vignette dependency point at the guarded
    # factory implementation as well as patching factory_runtime itself.
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
