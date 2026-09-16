"""Story-aware free audio direction using the existing Edge-TTS voices."""
from __future__ import annotations


def choose_delivery_profile(bot, script_data):
    title = str(script_data.get("title", "")).lower()
    genre = str(getattr(bot, "_active_web_config", {}).get("category", "")).lower()
    cricket = bool(getattr(bot, "_active_web_config", {}).get("cricket_pipeline"))

    if cricket or genre in {"sports", "sports_stories_of_day"}:
        return "HYPE COMMENTATOR"
    if genre in {"business_finance", "technology", "tech_reviews", "national_global_affairs", "health_lifestyle"}:
        return "ANALYTICAL INSIDER"
    if genre == "entertainment" or any(x in title for x in ("film", "movie", "actor", "actress", "box office", "celebrity")):
        return "LISTICLE HOST"
    if any(x in title for x in ("announced", "decision", "deal", "launch", "report", "official", "revealed")):
        return "ANALYTICAL INSIDER"
    return "HYPE COMMENTATOR"


def patch_audio_direction(bot):
    # Branding and final artifact QC are bound here because this patch is part
    # of the live runtime binding stack; both are explicit and idempotent.
    try:
        from branding_runtime import patch_branding_pipeline
        patch_branding_pipeline(bot)
    except Exception as exc:
        print(f"   [Bindings] Branding runtime unavailable: {type(exc).__name__}: {exc}", flush=True)
    try:
        from final_qc_runtime import patch_workflow_qc
        patch_workflow_qc(bot)
    except Exception as exc:
        print(f"   [Bindings] Final QC runtime unavailable: {type(exc).__name__}: {exc}", flush=True)

    if getattr(bot, "_audio_direction_patch_installed", False):
        return bot
    run_robot = getattr(bot, "run_robot", None)
    if run_robot is None or not hasattr(run_robot, "__globals__"):
        return bot

    globals_dict = run_robot.__globals__
    original = globals_dict.get("generate_voiceover_and_timestamps")
    if not callable(original) or getattr(original, "_audio_direction_wrapped", False):
        bot._audio_direction_patch_installed = True
        return bot

    async def directed_audio(script_data, language_cfg):
        profile = choose_delivery_profile(bot, script_data)
        script_data["persona_used"] = profile
        script_data["delivery_profile"] = profile
        print(f"   [Audio Direction] Delivery profile: {profile}", flush=True)
        return await original(script_data, language_cfg)

    directed_audio._audio_direction_wrapped = True
    globals_dict["generate_voiceover_and_timestamps"] = directed_audio
    bot.generate_voiceover_and_timestamps = directed_audio
    bot._audio_direction_patch_installed = True
    return bot
