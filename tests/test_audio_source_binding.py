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


def test_audio_accepts_production_scene_count_repair_handoff():
    bot = _patched_bot()

    repaired = {
        "fallback_reason": "scene_count_contract",
        "script": [
            {
                "voiceover": "Pakistan received a sanction related to the World Test Championship.",
            },
            {
                "voiceover": "The sanction affects the team's championship standing after the latest decision.",
            },
            {
                "voiceover": "The governing body recorded the penalty in its official competition process.",
            },
            {
                "voiceover": "The result changes the points position and the consequences for the campaign.",
            },
            {
                "voiceover": "The final impact will depend on the published competition records and updates.",
            },
        ],
    }

    result = asyncio.run(bot.generate_voiceover_and_timestamps(repaired, {}))

    assert result["ok"] is True
    handed_off = bot.calls[0]
    assert handed_off["authoritative_narration"] is True
    assert handed_off["integrity_version"]
    assert all(scene["narration_source"] == "validated_script" for scene in handed_off["script"])
    assert [scene["scene_id"] for scene in handed_off["script"]] == [1, 2, 3, 4, 5]



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
