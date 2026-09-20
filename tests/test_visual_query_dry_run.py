import sqlite3
from types import SimpleNamespace

import dashboard_runtime
import visual_entity_grounding_runtime
import visual_query_entities_runtime


class _DryRunBot:
    DB_PATH = ":memory:"
    LANGUAGES = {
        "english": {
            "script_instruction": "Write in English.",
        }
    }
    CONTENT_CATEGORIES = {
        "national_global_affairs": {
            "label": "National & Global Affairs",
        }
    }

    def __init__(self, writer):
        def run_robot():
            return None

        run_robot.__globals__["write_script"] = writer
        self.run_robot = run_robot
        self.write_script = writer


def test_visual_query_dry_run_uses_script_and_query_path_without_retrieval(monkeypatch):
    calls = {"writer": 0, "search": 0, "manual_queries": []}

    def writer(story, language_cfg, genre_key, conn, format_mode):
        calls["writer"] += 1
        assert story["title"] == "Rishabh Pant returns to training"
        assert genre_key == "national_global_affairs"
        assert format_mode == "regular"
        assert language_cfg["script_instruction"] == "Write in English."
        assert isinstance(conn, sqlite3.Connection)
        return {
            "title": "Rishabh Pant returns to training",
            "research_source_count": 3,
            "research_evidence_status": "supported",
            "script": [
                {
                    "primary_entity": "Rishabh Pant",
                    "voiceover": "Rishabh Pant addressed reporters at a press conference.",
                    "manual_visual_query": "THIS MUST NOT REACH THE DRY RUN",
                },
                {
                    "primary_entity": "India cricket team",
                    "voiceover": "India prepares for the next major fixture.",
                },
            ],
        }

    def fake_install(_bot):
        return None

    def fake_grounding(scene, _script_data):
        grounded = dict(scene)
        grounded["visual_entity_grounded"] = True
        grounded["visual_entity_grounding_reason"] = "test"
        return grounded

    def fake_search(_runtime, _bot, scene, *_args, manual_query=""):
        calls["search"] += 1
        calls["manual_queries"].append(manual_query)
        assert not scene.get("manual_visual_query")
        scene["_visual_search_intent"] = SimpleNamespace(
            subject=scene["primary_entity"],
            query=f'{scene["primary_entity"]} press conference',
            queries=(f'{scene["primary_entity"]} press conference', scene["primary_entity"]),
            visual_type="PERSON",
            visual_genre="PERSON_ACTION",
            confidence=1.0,
        )
        return None, False, "dry-run"

    monkeypatch.setattr(
        "production_hardening_runtime.install_production_hardening",
        fake_install,
    )
    monkeypatch.setattr(
        visual_entity_grounding_runtime,
        "apply_grounding",
        fake_grounding,
    )
    monkeypatch.setattr(
        visual_query_entities_runtime,
        "search_slide_visual",
        fake_search,
    )

    bot = _DryRunBot(writer)
    result = dashboard_runtime.run_visual_query_dry_run(
        bot,
        {
            "title": "Rishabh Pant returns to training",
            "category": "national_global_affairs",
        },
        {
            "format_mode": "regular",
            "category": "national_global_affairs",
            "language": "english",
        },
    )

    assert calls["writer"] == 1
    assert calls["search"] == 2
    assert calls["manual_queries"] == ["", ""]
    assert result["status"] == "PASS"
    assert result["image_retrieval_performed"] is False
    assert result["manual_queries_excluded"] is True
    assert [item["query"] for item in result["results"]] == [
        "Rishabh Pant press conference",
        "India cricket team press conference",
    ]
    assert all(
        "manual_visual_query" not in scene
        for scene in result["script"]["script"]
    )


def test_visual_query_dry_run_marks_retrieval_block_without_calling_provider(monkeypatch):
    def writer(*_args, **_kwargs):
        return {
            "title": "Test story",
            "script": [
                {
                    "primary_entity": "Test Entity",
                    "voiceover": "A test scene with a grounded subject.",
                },
            ],
        }

    monkeypatch.setattr(
        "production_hardening_runtime.install_production_hardening",
        lambda _bot: None,
    )
    monkeypatch.setattr(
        visual_entity_grounding_runtime,
        "apply_grounding",
        lambda scene, _script_data: {
            **scene,
            "visual_entity_grounded": True,
            "visual_entity_grounding_reason": "test",
        },
    )

    provider_calls = {"count": 0}

    def fake_search(*_args, **_kwargs):
        provider_calls["count"] += 1
        raise AssertionError("Image retrieval must never run in the dry-run diagnostic.")

    monkeypatch.setattr(
        visual_query_entities_runtime,
        "search_slide_visual",
        fake_search,
    )

    bot = _DryRunBot(writer)
    result = dashboard_runtime.run_visual_query_dry_run(
        bot,
        {"title": "Test story", "category": "national_global_affairs"},
        {
            "format_mode": "regular",
            "category": "national_global_affairs",
            "language": "english",
        },
    )

    assert provider_calls["count"] == 1
    assert result["status"] == "BLOCKED"
    assert result["image_retrieval_performed"] is False
    assert "Image retrieval must never run" in result["results"][0]["error"]
