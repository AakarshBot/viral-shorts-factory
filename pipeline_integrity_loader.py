"""Load pipeline integrity and bind voiceover strictly to the validated script."""
import json
import pipeline_integrity_runtime

pipeline_integrity_runtime.json = json


def _prepare_audio_handoff(script_data):
    """Normalize production-hardened scripts for the authoritative audio guard.

    The production scene-count repair path validates a source-grounded script via
    ``script_runtime.clean_script_data`` but that legacy helper does not attach
    the pipeline-integrity provenance markers.  Its explicit ``scene_count_contract``
    marker is the provenance boundary: normalize only that validated repair output,
    never arbitrary unmarked caller data.
    """
    if not isinstance(script_data, dict):
        return script_data
    if script_data.get("authoritative_narration") is True:
        return script_data
    if script_data.get("fallback_reason") != "scene_count_contract":
        return script_data

    candidate = dict(script_data)
    scenes = candidate.get("script")
    if not isinstance(scenes, list) or not scenes:
        return script_data

    normalized = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            return script_data
        voiceover = pipeline_integrity_runtime.clean_narration(scene.get("voiceover", ""))
        if not voiceover or pipeline_integrity_runtime.is_noise(voiceover):
            return script_data
        copy = dict(scene)
        copy["voiceover"] = voiceover
        copy["scene_id"] = index
        copy["narration_source"] = "validated_script"
        normalized.append(copy)

    candidate["script"] = normalized
    candidate["authoritative_narration"] = True
    candidate["integrity_version"] = pipeline_integrity_runtime.VERSION
    return candidate


def _bind_authoritative_audio(bot):
    current = getattr(bot, "generate_voiceover_and_timestamps", None)
    if not callable(current) or getattr(current, "_authoritative_audio_guard", False):
        return

    async def guarded_audio(script_data, language_cfg):
        script_data = _prepare_audio_handoff(script_data)
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
    pipeline_integrity_runtime.patch_pipeline_integrity(bot)
    _bind_authoritative_audio(bot)
    return bot
