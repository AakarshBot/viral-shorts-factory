import asyncio
import io
from pathlib import Path
import pytest

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



@pytest.fixture(autouse=True)
def _disable_live_web_crawler(monkeypatch):
    async def empty_crawler(*_args, **_kwargs):
        return {
            "assets": [],
            "target": 15,
            "success_threshold": 10,
            "articles": 0,
            "high_confidence": 0,
            "ai_checked": 0,
            "queries": [],
            "rejection_counts": {},
        }
    monkeypatch.setattr(content_runtime, "_load_web_fresh_image_pool", empty_crawler)


def _run_process(bot, script_data, format_mode):
    content_runtime.patch_content_first_visuals(bot)
    return asyncio.run(bot.process_visuals_async(script_data, {"font": ""}, format_mode=format_mode))


def test_content_first_visuals_installer_is_idempotent(tmp_path):
    bot = _fake_bot(tmp_path)

    content_runtime.patch_content_first_visuals(bot)
    first = bot.process_visuals_async
    content_runtime.patch_content_first_visuals(bot)

    assert bot.process_visuals_async is first
    assert bot._content_first_visuals_patch_installed is True



def test_manual_visual_pool_works_when_article_source_pool_is_empty(monkeypatch, tmp_path):
    image = Image.new("RGB", (900, 1200), (80, 90, 100))
    raw = io.BytesIO()
    image.save(raw, format="JPEG")
    materialized_path = tmp_path / "manual.jpg"
    image.save(materialized_path, format="JPEG")


    def fake_collect(*_args, **_kwargs):
        return {
            "assets": [{
                "bytes": raw.getvalue(),
                "hash": "manual-hash",
                "subject": "Virender Sehwag",
                "query": "Virender Sehwag",
                "visual_type": "PERSON",
                "source": "Commons",
                "provenance_status": "commercial-verified",
            }],
            "query_stats": [],
            "rejection_counts": {},
        }

    def fake_materialize(_bot, assets, pool_id):
        return [
            {
                **dict(assets[0]),
                "path": str(materialized_path),
                "original_path": str(materialized_path),
                "used": False,
                "status": "manual-review-ready",
            }
        ]

    monkeypatch.setattr(visual_retrieval_runtime, "collect_manual_visual_pool", fake_collect)
    monkeypatch.setattr(
        visual_retrieval_runtime,
        "materialize_manual_visual_pool",
        fake_materialize,
    )
    monkeypatch.setattr(visual_quality_runtime, "install", lambda *args, **kwargs: None)
    monkeypatch.setattr(visual_quality_runtime, "cover_crop", lambda image, size: image.resize(size))
    monkeypatch.setattr(visual_strategy_runtime, "classify_scene", lambda *args, **kwargs: "PERSON")
    monkeypatch.setattr(
        visual_query_entities_runtime,
        "search_slide_visual",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("automatic visual search must not run before the manual pool is consumed")
        ),
    )

    bot = _fake_bot(tmp_path)
    bot._active_web_config = {"visual_search_queries": "Virender Sehwag"}
    script_data = {
        "title": "Manual visual pool scope test",
        "script": [{
            "primary_entity": "Virender Sehwag",
            "voiceover": "Sehwag is the subject.",
            "visual_intent": "person portrait",
        }],
    }

    packages = _run_process(bot, script_data, "regular")

    assert len(packages) == 1
    assert script_data["visual_manual_pool_size"] == 1
    assert packages[0][0]["source_type"] == "Commons"


def test_fresh_web_crawler_pool_is_used_before_any_provider_fallback(monkeypatch, tmp_path):
    image = Image.new("RGB", (1200, 1600), (40, 50, 60))
    raw = io.BytesIO()
    image.save(raw, format="JPEG")

    async def crawler(*_args, **_kwargs):
        return {
            "assets": [
                {
                    "bytes": raw.getvalue(),
                    "hash": f"crawler-hash-{index}",
                    "subject": "Virat Kohli",
                    "source": "web_crawler",
                    "source_type": "web_crawler",
                    "source_name": "Example Cricket",
                    "credit": "Source: Example Cricket",
                    "query": "Virat Kohli latest",
                    "source_page_url": "https://example.com/story",
                    "source_image_url": f"https://example.com/{index}.jpg",
                    "visual_type": "PERSON",
                    "visual_genre": "PERSON_ACTION",
                    "status": "crawler-high-confidence",
                    "provenance_status": "provenance-review",
                    "provenance": {
                        "provider": "Example Cricket",
                        "url": "https://example.com/story",
                        "license": "Unverified web source",
                        "license_url": "https://example.com/story",
                    },
                }
                for index in range(10)
            ],
            "target": 15,
            "success_threshold": 10,
            "articles": 4,
            "high_confidence": 10,
            "ai_checked": 0,
            "queries": ["Virat Kohli latest"],
            "rejection_counts": {"final_images": 10},
        }

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("provider fallback must not run when crawler reaches 10 images")

    monkeypatch.setattr(content_runtime, "_load_web_fresh_image_pool", crawler)
    monkeypatch.setattr(
        visual_query_entities_runtime,
        "search_slide_visual",
        fail_provider,
    )
    monkeypatch.setattr(visual_quality_runtime, "install", lambda *args, **kwargs: None)
    monkeypatch.setattr(visual_quality_runtime, "cover_crop", lambda image, size: image.resize(size))
    monkeypatch.setattr(visual_strategy_runtime, "classify_scene", lambda *args, **kwargs: "PERSON")

    bot = _fake_bot(tmp_path)
    bot._active_web_config = {
        "selected_story": {
            "story_url": "https://example.com/story",
            "title": "Virat Kohli latest story",
        },
        "visual_search_queries": "ignored manual query",
    }
    script_data = {
        "title": "Virat Kohli latest story",
        "script": [
            {
                "primary_entity": "Virat Kohli",
                "voiceover": "Virat Kohli is the subject.",
                "visual_intent": "person portrait",
            },
            {
                "primary_entity": "Virat Kohli",
                "voiceover": "Virat Kohli is featured again.",
                "visual_intent": "person portrait",
            },
        ],
    }

    packages = _run_process(bot, script_data, "regular")

    assert len(packages) == 2
    assert script_data["visual_web_crawler_pool_size"] == 10
    assert script_data["visual_manual_pool_size"] == 10
    assert packages[0][0]["source_type"] == "web_crawler"
    assert packages[0][0]["source_credit"] == "Source: Example Cricket"
    assert packages[0][0]["visual_verified"] is True



def test_visual_process_resets_qa_state_for_run_and_each_scene(monkeypatch, tmp_path):
    calls = {"run": 0, "scene": 0}
    monkeypatch.setattr(
        content_runtime,
        "reset_visual_qa_video_budget",
        lambda: calls.__setitem__("run", calls["run"] + 1),
    )
    monkeypatch.setattr(
        content_runtime,
        "start_visual_qa_scene",
        lambda: calls.__setitem__("scene", calls["scene"] + 1),
    )

    bg = Image.new("RGBA", (1080, 1920), (40, 50, 60, 255))
    monkeypatch.setattr(
        visual_query_entities_runtime,
        "search_slide_visual",
        lambda *args, **kwargs: (bg.copy(), False, "commons"),
    )
    monkeypatch.setattr(visual_quality_runtime, "install", lambda *args, **kwargs: None)
    monkeypatch.setattr(visual_quality_runtime, "cover_crop", lambda image, size: image.resize(size))
    monkeypatch.setattr(visual_strategy_runtime, "classify_scene", lambda *args, **kwargs: "GENERAL_CONTEXT")

    bot = _fake_bot(tmp_path)
    script_data = {
        "title": "QA reset test",
        "script": [
            {"primary_entity": "Scene One", "voiceover": "One scene."},
            {"primary_entity": "Scene Two", "voiceover": "Two scene."},
            {"primary_entity": "Scene Three", "voiceover": "Three scene."},
        ],
    }

    packages = _run_process(bot, script_data, "regular")

    assert len(packages) == 3
    assert calls["run"] == 1
    assert calls["scene"] == 3

def test_deep_dive_first_slide_uses_content_first_visual(monkeypatch, tmp_path):
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


def test_global_manual_queries_remain_available_as_fallback():
    from manual_visual_query_runtime import assign_manual_queries

    scenes = [
        {"primary_entity": "India Afghanistan cricket match", "voiceover": "The final match is underway."},
        {"primary_entity": "Shubman Gill", "voiceover": "Gill is leading the batting."},
    ]
    assignments = assign_manual_queries(
        scenes,
        "India Afghanistan cricket match; Shubman Gill batting",
    )

    assert assignments[0]["query"] == "India Afghanistan cricket match"
    assert assignments[1]["query"] == "Shubman Gill batting"


def test_manual_production_pool_keeps_gemini_entity_qa_enabled():
    source = Path(__file__).resolve().parents[1].joinpath("visual_content_runtime.py").read_text(encoding="utf-8")
    start = source.index("manual_pool_result = collect_manual_visual_pool(")
    end = source.index("manual_pool_materialized =", start)
    block = source[start:end]
    assert "verify_with_ai=True" in block
