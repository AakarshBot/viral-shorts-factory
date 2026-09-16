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
    )
    bound = []
    for name in names:
        value = getattr(bot, name, None)
        if value is not None:
            namespace[name] = value
            bound.append(name)

    # write_script() is a separate compiled function but uses the same module
    # globals dictionary, so the assignment above also fixes its validator.
    print(
        "   [Bindings] Legacy factory globals bound to active runtime patches: "
        + ", ".join(bound),
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
