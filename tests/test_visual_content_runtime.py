import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image

import visual_content_runtime as content_runtime
import visual_retrieval_runtime
import visual_quality_runtime


def _fake_bot(tmp_path):
    class FakeBot:
        ASSETS_DIR = str(tmp_path)

    return FakeBot()


def _crawler_result(raw_bytes, count=10):
    return {
        "assets": [
            {
                "bytes": raw_bytes,
                "hash": f"crawler-hash-{index}",
                "subject": "Virat Kohli",
                "source": "web_crawler",
                "source_type": "web_crawler",
                "source_name": "Example Cricket",
                "credit": "Source: Example Cricket",
                "query": "Virat Kohli batting",
                "source_page_url": f"https://example.com/story/{index}",
                "source_image_url": f"https://example.com/{index}.jpg",
                "visual_type": "PERSON",
                "visual_genre": "PERSON_ACTION",
                "status": "crawler-high-confidence",
                "provenance_status": "provenance-review",
                "provenance": {
                    "provider": "Example Cricket",
                    "url": f"https://example.com/story/{index}",
                    "license": "Unverified web source",
                    "license_url": f"https://example.com/story/{index}",
                },
            }
            for index in range(count)
        ],
        "target": 15,
        "success_threshold": 10,
        "articles": 4,
        "profile_pages": 0,
        "high_confidence": count,
        "ai_checked": 0,
        "queries": ["Virat Kohli batting"],
        "rejection_counts": {
            "final_images": count,
        },
    }


@pytest.fixture(autouse=True)
def _disable_live_web_crawler(monkeypatch):
    async def empty_crawler(*_args, **_kwargs):
        return {
            "assets": [],
            "target": 15,
            "success_threshold": 10,
            "articles": 0,
            "profile_pages": 0,
            "high_confidence": 0,
            "ai_checked": 0,
            "queries": [],
            "rejection_counts": {},
        }

    monkeypatch.setattr(
        content_runtime,
        "_load_web_fresh_image_pool",
        empty_crawler,
    )


def _run_process(bot, script_data, format_mode="regular"):
    content_runtime.patch_content_first_visuals(bot)
    return asyncio.run(
        bot.process_visuals_async(
            script_data,
            {"font": ""},
            format_mode=format_mode,
        )
    )


def test_content_first_visuals_installer_is_idempotent(tmp_path):
    bot = _fake_bot(tmp_path)

    content_runtime.patch_content_first_visuals(bot)
    first = bot.process_visuals_async
    content_runtime.patch_content_first_visuals(bot)

    assert bot.process_visuals_async is first
    assert bot._content_first_visuals_patch_installed is True


def test_initial_visual_pass_only_builds_shared_website_pool_and_keeps_slides_empty(
    monkeypatch,
    tmp_path,
):
    image = Image.new("RGB", (1200, 1600), (40, 50, 60))
    raw = io.BytesIO()
    image.save(raw, format="JPEG")

    async def crawler(*_args, **_kwargs):
        return _crawler_result(raw.getvalue(), count=10)

    def fail_factory_search(*_args, **_kwargs):
        raise AssertionError(
            "the established factory visual search must not run during the automatic website pass"
        )

    def fake_materialize(_bot, assets, pool_id):
        return [
            {
                **dict(asset),
                "path": str(tmp_path / f"{asset['hash']}.jpg"),
                "original_path": str(tmp_path / f"{asset['hash']}.jpg"),
                "used": False,
                "pool_origin": "web-crawler",
            }
            for asset in assets
        ]

    for asset in crawler.__name__,:
        pass

    monkeypatch.setattr(content_runtime, "_load_web_fresh_image_pool", crawler)
    monkeypatch.setattr(
        visual_retrieval_runtime,
        "materialize_manual_visual_pool",
        fake_materialize,
    )
    monkeypatch.setattr(
        visual_retrieval_runtime,
        "collect_manual_visual_pool",
        fail_factory_search,
    )

    bot = _fake_bot(tmp_path)
    bot._active_web_config = {
        "run_id": "run-web-first",
        "selected_story": {
            "story_url": "https://example.com/story",
            "title": "Virat Kohli batting story",
        },
        "visual_search_queries": "Virat Kohli batting",
    }
    script_data = {
        "title": "Virat Kohli batting story",
        "script": [
            {
                "primary_entity": "Virat Kohli",
                "voiceover": "Virat Kohli is the subject.",
                "visual_intent": "person action",
            },
            {
                "primary_entity": "Virat Kohli",
                "voiceover": "The second scene stays empty too.",
                "visual_intent": "person action",
            },
        ],
    }

    packages = _run_process(bot, script_data)

    assert len(packages) == 2
    assert script_data["visual_web_crawler_pool_size"] == 10
    assert len(script_data["visual_manual_pool"]) == 10
    assert script_data["visual_fallback_provider_queries"] == []
    assert script_data["visuals_verified"] is False
    assert script_data["visual_coverage"] == 0.0
    assert all(layer[0]["image"] == "" for layer in packages)
    assert all(layer[0]["visual_verified"] is False for layer in packages)
    assert all(
        layer[0]["visual_qc_block_reason"].startswith("No image selected")
        for layer in packages
    )


def test_underfilled_website_pool_never_auto_falls_back_to_factory_providers(
    monkeypatch,
    tmp_path,
):
    image = Image.new("RGB", (1200, 1600), (40, 50, 60))
    raw = io.BytesIO()
    image.save(raw, format="JPEG")

    async def crawler(*_args, **_kwargs):
        return _crawler_result(raw.getvalue(), count=2)

    called = {"factory": 0}

    def fail_factory(*_args, **_kwargs):
        called["factory"] += 1
        raise AssertionError("factory providers must not be called for crawler underfill")

    monkeypatch.setattr(content_runtime, "_load_web_fresh_image_pool", crawler)
    monkeypatch.setattr(
        visual_retrieval_runtime,
        "collect_manual_visual_pool",
        fail_factory,
    )

    def materialize(_bot, assets, pool_id):
        return [
            {
                **dict(asset),
                "path": str(tmp_path / f"{asset['hash']}.jpg"),
                "original_path": str(tmp_path / f"{asset['hash']}.jpg"),
                "used": False,
            }
            for asset in assets
        ]

    monkeypatch.setattr(
        visual_retrieval_runtime,
        "materialize_manual_visual_pool",
        materialize,
    )

    bot = _fake_bot(tmp_path)
    bot._active_web_config = {
        "selected_story": {"title": "Virat Kohli story"},
        "visual_search_queries": "Virat Kohli batting",
    }
    script_data = {
        "title": "Virat Kohli story",
        "script": [{"primary_entity": "Virat Kohli", "voiceover": "One."}],
    }

    packages = _run_process(bot, script_data)

    assert called["factory"] == 0
    assert script_data["visual_web_crawler_pool_size"] == 2
    assert len(script_data["visual_manual_pool"]) == 2
    assert packages[0][0]["image"] == ""


def test_initial_visual_stage_supports_actual_script_length_without_hard_coding(
    monkeypatch,
    tmp_path,
):
    async def crawler(*_args, **_kwargs):
        return {"assets": [], "target": 15, "success_threshold": 10, "queries": []}

    monkeypatch.setattr(content_runtime, "_load_web_fresh_image_pool", crawler)

    bot = _fake_bot(tmp_path)
    script_data = {
        "title": "Five scene test",
        "script": [
            {"voiceover": f"Scene {index}."}
            for index in range(1, 6)
        ],
    }

    packages = _run_process(bot, script_data)

    assert len(packages) == 5
    assert [layer[0]["text"] for layer in packages] == [
        f"Scene {index}." for index in range(1, 6)
    ]


def test_top5_visual_acquisition_keeps_slide_slots_empty(tmp_path):
    bot = _fake_bot(tmp_path)
    script_data = {
        "title": "Top 5 story",
        "script": [
            {"voiceover": "Story one.", "primary_entity": "Story one"},
            {"voiceover": "Story two.", "primary_entity": "Story two"},
        ],
    }

    packages = _run_process(bot, script_data, "top5")

    assert len(packages) == 2
    assert all(package[0]["image"] == "" for package in packages)
    assert all(package[0]["text"] == "" for package in packages)


def test_no_renderer_rescue_runs_during_visual_acquisition(tmp_path):
    bot = _fake_bot(tmp_path)
    script_data = {
        "title": "No rescue test",
        "script": [
            {"voiceover": "One.", "primary_entity": "Scene one"},
            {"voiceover": "Two.", "primary_entity": "Scene two"},
        ],
    }

    _run_process(bot, script_data)

    assert script_data["visual_rescue_count"] == 0
    assert script_data["visual_fallback_count"] == 0
    assert script_data["visuals_verified"] is False


def test_global_manual_queries_remain_parseable_for_explicit_qc_search():
    from manual_visual_query_runtime import assign_manual_queries

    scenes = [
        {
            "primary_entity": "India Afghanistan cricket match",
            "voiceover": "The final match is underway.",
        },
        {
            "primary_entity": "Shubman Gill",
            "voiceover": "Gill is leading the batting.",
        },
    ]
    assignments = assign_manual_queries(
        scenes,
        "India Afghanistan cricket match; Shubman Gill batting",
    )

    assert assignments[0]["query"] == "India Afghanistan cricket match"
    assert assignments[1]["query"] == "Shubman Gill batting"


def test_automatic_visual_pass_no_longer_contains_provider_fallback_call():
    source = Path(__file__).resolve().parents[1].joinpath(
        "visual_content_runtime.py"
    ).read_text(encoding="utf-8")
    process = source[
        source.index('    async def process(script_data, language_cfg, format_mode="regular"):'):
        source.index("    bot.process_visuals_async = process")
    ]

    assert "collect_manual_visual_pool(" not in process
    assert "search_slide_visual(" not in process
    assert "select_manual_visual_candidate(" not in process
    assert 'provider fallback=disabled' in process
