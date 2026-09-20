from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

from PIL import Image

from visual_storyboard_v2_runtime import (
    ALLOWED_MODES,
    PIPELINE_ID,
    _deterministic_mode,
    _normalise_modes,
    _render_scene,
    build_storyboard_visuals,
)


def test_normalise_modes_is_fail_closed():
    scenes = [{"voiceover": "England beat Australia 3-1.", "primary_entity": "England"}]
    assert _normalise_modes(["NOT_A_MODE"], scenes) == ["LEAD"]
    assert _normalise_modes(["SCORECARD"], [{"voiceover": "England won the match."}])[0] != "SCORECARD"


def test_deterministic_modes_cover_story_forms():
    assert _deterministic_mode({"voiceover": "The result was 3-1."}, 0) == "LEAD"
    assert _deterministic_mode({"voiceover": "The result was 3-1 and England won the final."}, 1) == "SCORECARD"
    assert _deterministic_mode({"voiceover": "From 2019 to 2024, his role changed."}, 1) == "TIMELINE"
    assert _deterministic_mode({"voiceover": "Compared with the previous approach, this is different."}, 1) == "COMPARE"


def test_every_renderer_produces_a_short_frame():
    scene = {
        "voiceover": "England beat Australia 3-1 after a tactical change.",
        "primary_entity": "England",
        "visual_intent": "show the tactical change",
        "sport_or_topic_category": "sports",
    }
    for mode in ALLOWED_MODES:
        image = _render_scene(scene, mode, None)
        assert image.size == (1080, 1920)
        assert image.mode == "RGB"


def test_build_storyboard_visuals_is_original_and_self_contained(tmp_path):
    bot = SimpleNamespace(ASSETS_DIR=str(tmp_path))
    script_data = {
        "title": "Test story",
        "research_evidence_text": "A verified research pack.",
        "script": [
            {
                "voiceover": "A new coach was appointed in 2026.",
                "primary_entity": "Test Coach",
                "visual_intent": "show the appointment",
            },
            {
                "voiceover": "The coach worked from 2019 to 2024.",
                "primary_entity": "Test Coach",
                "visual_intent": "show the timeline",
            },
        ],
    }
    packages = asyncio.run(
        build_storyboard_visuals(bot, script_data, {"font": ""}, "regular")
    )
    assert len(packages) == 2
    assert script_data["visual_pipeline"] == PIPELINE_ID
    assert script_data["ai_image_ratio"] == 0.0
    assert script_data["visual_manual_pool_size"] == 0
    assert all(item[0]["source_type"] == "editorial_graphic" for item in packages)
    assert all(item[0]["source_image_url"] == "" for item in packages)
    for package in packages:
        path = package[0]["image"]
        assert os.path.isfile(path)
        with Image.open(path) as image:
            assert image.size == (1080, 1920)
