import asyncio
from pathlib import Path

from PIL import Image

import visual_content_runtime as content_runtime
import visual_quality_runtime
import visual_query_entities_runtime
import visual_retrieval_runtime
import visual_runtime
import visual_strategy_runtime


def _fake_bot(tmp_path):
    class FakeBot:
        ASSETS_DIR = str(tmp_path)
        PALETTE = {
            "accent_primary": (0, 191, 255),
            "accent_secondary": (255, 140, 0),
        }

        def render_top5_card(self, image, *args, **kwargs):
            return image

    return FakeBot()


def _run_process(bot, script_data, format_mode):
    content_runtime.patch_content_first_visuals(bot)
    return asyncio.run(bot.process_visuals_async(script_data, {"font": ""}, format_mode=format_mode))


def test_deep_dive_first_slide_skips_hook_card(monkeypatch, tmp_path):
    calls = []
    bg = Image.new("RGBA", (1080, 1920), (40, 50, 60, 255))

    monkeypatch.setattr(
        visual_query_entities_runtime,
        "search_slide_visual",
        lambda *args, **kwargs: (bg.copy(), False, "wikipedia"),
    )
    monkeypatch.setattr(visual_quality_runtime, "install", lambda *args, **kwargs: None)
    monkeypatch.setattr(visual_quality_runtime, "cover_crop", lambda image, size: image.resize(size))
    monkeypatch.setattr(visual_strategy_runtime, "classify_scene", lambda *args, **kwargs: "PERSON")
    monkeypatch.setattr(visual_retrieval_runtime, "make_visual_rescue", lambda *args, **kwargs: bg.copy())

    def fail_hook(*args, **kwargs):
        raise AssertionError("Deep Dive must never use the first-slide hook-card renderer")

    def standard_overlay(*args, **kwargs):
        calls.append("scene_overlay")
        return args[1]

    monkeypatch.setattr(content_runtime, "_render_hook_card", fail_hook)

    bot = _fake_bot(tmp_path)
    script_data = {
        "title": "Deep Dive story",
        "script": [
            {
                "primary_entity": "Amina Rahman",
                "voiceover": "Amina Rahman explains the development.",
                "visual_intent": "person portrait",
                "specific_search_prompt": "Amina Rahman",
            }
        ],
    }

    packages = _run_process(bot, script_data, "regular")

    assert packages[0][0]["text"] == "Amina Rahman explains the development."
    assert Path(packages[0][0]["image"]).exists()


def test_top5_first_slide_keeps_dedicated_design(monkeypatch, tmp_path):
    calls = []
    bg = Image.new("RGBA", (1080, 1920), (40, 50, 60, 255))

    monkeypatch.setattr(
        visual_query_entities_runtime,
        "search_slide_visual",
        lambda *args, **kwargs: (bg.copy(), False, "commons"),
    )
    monkeypatch.setattr(visual_quality_runtime, "install", lambda *args, **kwargs: None)
    monkeypatch.setattr(visual_quality_runtime, "cover_crop", lambda image, size: image.resize(size))
    monkeypatch.setattr(visual_strategy_runtime, "classify_scene", lambda *args, **kwargs: "EVENT")
    monkeypatch.setattr(visual_retrieval_runtime, "make_visual_rescue", lambda *args, **kwargs: bg.copy())

    monkeypatch.setattr(
        visual_runtime,
        "_render_image_slide",
        lambda *args, **kwargs: calls.append("top5_intro") or args[1],
    )
    monkeypatch.setattr(content_runtime, "_render_hook_card", lambda *args, **kwargs: calls.append("hook") or args[1])

    bot = _fake_bot(tmp_path)
    script_data = {
        "title": "Top 5 story",
        "script": [
            {"primary_entity": "Story one", "voiceover": "Story one."},
            {"primary_entity": "Story two", "voiceover": "Story two."},
        ],
    }

    packages = _run_process(bot, script_data, "top5")

    assert calls[0] == "top5_intro"
    assert "hook" not in calls
    assert packages[0][0]["text"] == ""
    assert packages[1][0]["text"] == ""


def test_renderer_rescue_count_is_not_double_incremented(monkeypatch, tmp_path):
    bg = Image.new("RGBA", (1080, 1920), (40, 50, 60, 255))

    monkeypatch.setattr(
        visual_query_entities_runtime,
        "search_slide_visual",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic retrieval failure")),
    )
    monkeypatch.setattr(visual_quality_runtime, "install", lambda *args, **kwargs: None)
    monkeypatch.setattr(visual_quality_runtime, "cover_crop", lambda image, size: image.resize(size))
    monkeypatch.setattr(visual_strategy_runtime, "classify_scene", lambda *args, **kwargs: "GENERAL_CONTEXT")
    monkeypatch.setattr(visual_retrieval_runtime, "make_visual_rescue", lambda *args, **kwargs: bg.copy())

    bot = _fake_bot(tmp_path)
    script_data = {
        "title": "Rescue telemetry test",
        "script": [
            {"primary_entity": "Scene one", "voiceover": "One."},
            {"primary_entity": "Scene two", "voiceover": "Two."},
        ],
    }

    _run_process(bot, script_data, "regular")

    assert script_data["visual_rescue_count"] == 2
    assert script_data["visual_fallback_count"] == 2
