import asyncio

import pytest

from pipeline_integrity_loader import patch_pipeline_integrity


class _Bot:
    def __init__(self):
        def run_robot():
            return None

        self.run_robot = run_robot
        self.calls = []

        async def generate_voiceover_and_timestamps(script_data, language_cfg):
            self.calls.append(script_data)
            return {"ok": True, "script": script_data["script"]}

        self.generate_voiceover_and_timestamps = generate_voiceover_and_timestamps


def _patched_bot():
    bot = _Bot()
    patch_pipeline_integrity(bot)
    return bot


def test_audio_accepts_only_authoritative_validated_script():
    bot = _patched_bot()

    script = {
        "authoritative_narration": True,
        "script": [
            {
                "voiceover": "India announced a new policy today.",
                "narration_source": "validated_script",
            }
        ],
    }

    result = asyncio.run(bot.generate_voiceover_and_timestamps(script, {}))

    assert result["ok"] is True
    assert bot.calls[0]["script"][0]["narration_source"] == "validated_script"
    assert bot.calls[0]["script"][0]["voiceover"] == "India announced a new policy today."


def test_audio_rejects_obsolete_scene_count_repair_handoff():
    bot = _patched_bot()

    obsolete = {
        "fallback_reason": "scene_count_contract",
        "script": [
            {"voiceover": "A stale fallback payload must not bypass the authoritative narration gate."},
        ],
    }

    with pytest.raises(ValueError, match="validated generated script"):
        asyncio.run(bot.generate_voiceover_and_timestamps(obsolete, {}))


def test_audio_rejects_unvalidated_slide_or_visual_text():
    bot = _patched_bot()

    unvalidated = {
        "authoritative_narration": True,
        "script": [
            {
                "voiceover": "This text came from a slide and is not the generated script.",
                "narration_source": "visual_text",
            }
        ],
    }

    with pytest.raises(ValueError, match="validated script"):
        asyncio.run(bot.generate_voiceover_and_timestamps(unvalidated, {}))


def test_audio_rejects_missing_authoritative_marker_even_with_scene_text():
    bot = _patched_bot()

    unmarked = {
        "script": [
            {
                "voiceover": "A valid-looking sentence that lacks the authoritative marker.",
                "narration_source": "validated_script",
            }
        ]
    }

    with pytest.raises(ValueError, match="validated generated script"):
        asyncio.run(bot.generate_voiceover_and_timestamps(unmarked, {}))
