import sqlite3

import dashboard_runtime
import visual_entity_grounding_runtime


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


def _base_config():
    return {
        "format_mode": "regular",
        "category": "national_global_affairs",
        "language": "english",
    }


def test_visual_query_dry_run_uses_real_query_path_and_excludes_manual_queries(monkeypatch):
    calls = {"writer": 0}

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
                    "visual_intent": "press conference person",
                    "manual_visual_query": "THIS MUST NOT REACH THE DRY RUN",
                },
                {
                    "primary_entity": "India cricket team",
                    "voiceover": "India prepares for the next major fixture.",
                    "visual_intent": "cricket team press conference",
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

    bot = _DryRunBot(writer)
    result = dashboard_runtime.run_visual_query_dry_run(
        bot,
        {
            "title": "Rishabh Pant returns to training",
            "category": "national_global_affairs",
        },
        _base_config(),
    )

    assert calls["writer"] == 1
    assert result["status"] == "PASS"
    assert result["image_retrieval_performed"] is False
    assert result["manual_queries_excluded"] is True
    assert len(result["results"]) == 2
    assert all(item.get("query") for item in result["results"])
    assert all(item.get("queries") for item in result["results"])
    assert all(
        "manual_visual_query" not in scene
        for scene in result["script"]["script"]
    )


def test_visual_query_dry_run_stops_at_retrieval_boundary(monkeypatch):
    def writer(*_args, **_kwargs):
        return {
            "title": "Test story",
            "research_source_count": 1,
            "research_evidence_status": "supported",
            "script": [
                {
                    "primary_entity": "Test Entity",
                    "voiceover": "A test scene with a grounded subject.",
                    "visual_intent": "press conference person",
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

    def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("Image retrieval provider must never run in the dry-run diagnostic.")

    monkeypatch.setattr(
        "visual_query_entities_runtime.run_visual_retrieval",
        provider_must_not_run,
    )

    bot = _DryRunBot(writer)
    result = dashboard_runtime.run_visual_query_dry_run(
        bot,
        {"title": "Test story", "category": "national_global_affairs"},
        _base_config(),
    )

    assert result["status"] == "PASS"
    assert result["image_retrieval_performed"] is False
    assert result["results"][0]["query"]
