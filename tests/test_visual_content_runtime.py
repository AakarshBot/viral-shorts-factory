import asyncio
import io
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


def test_news_source_ranking_does_not_force_first_slide_for_manual_query():
    scenes = [
        {
            "primary_entity": "Unrelated Presenter",
            "manual_visual_query": "Unrelated Presenter",
            "voiceover": "A presenter comments on the story.",
        },
        {
            "primary_entity": "Target Story",
            "manual_visual_query": "Target Story",
            "voiceover": "The Target Story is the main development.",
        },
    ]

    ranked = content_runtime._rank_news_source_scene_indices(
        scenes,
        "Target Story",
    )

    assert ranked[0] == 1


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


def test_failed_scene_reuses_verified_same_subject_asset_once(monkeypatch, tmp_path):
    primary = Image.new("RGBA", (1080, 1920), (40, 50, 60, 255))
    related = Image.new("RGB", (900, 1200), (90, 100, 110))
    related_buffer = io.BytesIO()
    related.save(related_buffer, format="JPEG", quality=95)
    related_bytes = related_buffer.getvalue()

    calls = {"count": 0}

    def fake_search(_runtime, _bot, scene, *_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            scene["visual_verified"] = True
            scene["visual_genre"] = "PERSON_ACTION"
            scene["visual_type"] = "PERSON"
            scene["_verified_subject_assets"] = [
                {
                    "subject": "Rishabh Pant",
                    "bytes": related_bytes,
                    "hash": "related-hash",
                    "source": "Commons",
                    "query": "Rishabh Pant press conference",
                    "visual_type": "PERSON",
                    "visual_genre": "PERSON_ACTION",
                }
            ]
            return primary.copy(), False, "commons"

        scene["visual_verified"] = False
        scene["visual_rescue_reason"] = "real-and-ai-sources-exhausted"
        scene["visual_genre"] = "PERSON_ACTION"
        scene["visual_type"] = "PERSON"
        return primary.copy(), False, "visual-rescue"

    monkeypatch.setattr(visual_query_entities_runtime, "search_slide_visual", fake_search)
    monkeypatch.setattr(visual_quality_runtime, "install", lambda *args, **kwargs: None)
    monkeypatch.setattr(visual_quality_runtime, "cover_crop", lambda image, size: image.resize(size))
    monkeypatch.setattr(visual_strategy_runtime, "classify_scene", lambda *args, **kwargs: "PERSON")
    monkeypatch.setattr(visual_retrieval_runtime, "make_visual_rescue", lambda *args, **kwargs: primary.copy())

    bot = _fake_bot(tmp_path)
    script_data = {
        "title": "Rishabh Pant related asset rescue test",
        "script": [
            {
                "primary_entity": "Rishabh Pant",
                "voiceover": "Primary scene.",
                "visual_intent": "press conference person",
            },
            {
                "primary_entity": "Rishabh Pant",
                "voiceover": "Failed scene.",
                "visual_intent": "press conference person",
            },
        ],
    }

    packages = _run_process(bot, script_data, "regular")

    assert packages[0][0]["visual_verified"] is True
    assert packages[1][0]["source_type"] == "related-verified"
    assert packages[1][0]["visual_verified"] is True
    assert packages[1][0]["related_reuse"] is True
    assert script_data["visual_coverage"] == 1.0
    assert script_data["visual_related_reuse_count"] == 1
    assert script_data["visual_rescue_count"] == 0
    assert script_data["visuals_verified"] is True


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
