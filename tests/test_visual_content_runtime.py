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


def test_content_first_visuals_installer_is_idempotent(tmp_path):
    bot = _fake_bot(tmp_path)

    content_runtime.patch_content_first_visuals(bot)
    first = bot.process_visuals_async
    content_runtime.patch_content_first_visuals(bot)

    assert bot.process_visuals_async is first
    assert bot._content_first_visuals_patch_installed is True


def test_article_source_images_are_added_only_to_manual_qc_pool(monkeypatch, tmp_path):
    import news_source_image_runtime

    image = Image.new("RGB", (900, 1200), (80, 90, 100))
    raw = io.BytesIO()
    image.save(raw, format="JPEG")

    async def fake_article_pool(*_args, **_kwargs):
        return [
            {
                "bytes": raw.getvalue(),
                "hash": "article-hash",
                "source": "news_source",
                "source_type": "news_source",
                "credit": "Source: Example News",
                "provenance": {
                    "provider": "Example News",
                    "url": "https://example.com/image.jpg",
                    "author": "Example News",
                },
            }
        ]
    monkeypatch.setattr(content_runtime, "_load_news_source_image_pool", fake_article_pool)
    monkeypatch.setattr(
        visual_retrieval_runtime,
        "materialize_manual_visual_pool",
        lambda _bot, assets, pool_id: [
            {
                **dict(asset),
                "path": str(tmp_path / "article.jpg"),
                "original_path": str(tmp_path / "article.jpg"),
                "status": "article-source",
                "used": False,
            }
            for asset in assets
        ],
    )
    monkeypatch.setattr(
        visual_query_entities_runtime,
        "search_slide_visual",
        lambda *args, **kwargs: (image.copy(), False, "commons"),
    )
    monkeypatch.setattr(visual_quality_runtime, "install", lambda *args, **kwargs: None)
    monkeypatch.setattr(visual_quality_runtime, "cover_crop", lambda image, size: image.resize(size))
    monkeypatch.setattr(visual_strategy_runtime, "classify_scene", lambda *args, **kwargs: "GENERAL_CONTEXT")

    bot = _fake_bot(tmp_path)
    script_data = {
        "title": "Direct article source",
        "script": [{"primary_entity": "Story subject", "voiceover": "A current story."}],
    }

    packages = _run_process(bot, script_data, "regular")

    assert len(packages) == 1
    assert script_data["visual_manual_pool"][0]["source_type"] == "news_source"
    assert script_data["visual_manual_pool"][0]["status"] == "article-source"
    assert script_data["visual_manual_pool"][0]["provenance"]["provider"] == "Example News"
    assert packages[0][0]["source_type"] == "commons"



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

def test_manual_rights_review_assets_are_not_written_to_verified_cache():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("visual_content_runtime.py").read_text(encoding="utf-8")
    cache_block_start = source.index("for asset in manual_pool_result.get(\"assets\") or []:")
    cache_block_end = source.index("manual_pool_materialized =", cache_block_start)
    block = source[cache_block_start:cache_block_end]

    assert 'if str(asset.get("provenance_status") or "").strip() != "commercial-verified":' in block
    assert "Rights-review images remain available to the human QC pool" in block


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
