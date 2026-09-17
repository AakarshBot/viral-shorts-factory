"""Load pipeline integrity and bind voiceover strictly to the validated script."""
import json
import pipeline_integrity_runtime

pipeline_integrity_runtime.json = json


def _bind_authoritative_audio(bot):
    current = getattr(bot, "generate_voiceover_and_timestamps", None)
    if not callable(current) or getattr(current, "_authoritative_audio_guard", False):
        return

    async def guarded_audio(script_data, language_cfg):
        if not isinstance(script_data, dict) or script_data.get("authoritative_narration") is not True:
            raise ValueError("Audio refused: narration must come from the validated generated script.")
        scenes = script_data.get("script", [])
        if not scenes:
            raise ValueError("Audio refused: authoritative script contains no scenes.")
        for index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict) or scene.get("narration_source") != "validated_script":
                raise ValueError(f"Audio refused: scene {index} is not sourced from the validated script.")
        return await current(script_data, language_cfg)

    guarded_audio._authoritative_audio_guard = True
    bot.generate_voiceover_and_timestamps = guarded_audio
    if callable(getattr(bot, "run_robot", None)) and hasattr(bot.run_robot, "__globals__"):
        bot.run_robot.__globals__["generate_voiceover_and_timestamps"] = guarded_audio


def patch_pipeline_integrity(bot):
    """Install the canonical integrity wrappers, then bind authoritative audio."""
    bot = pipeline_integrity_runtime.patch_pipeline_integrity(bot)
    _bind_authoritative_audio(bot)
    return bot
