from datetime import datetime, timezone
from pathlib import Path

import story_ranker


class _FakeResponse:
    status_code = 200

    def __init__(self, content):
        self.content = content




def test_script_pipeline_tries_original_free_provider_before_private_extractive_fallback(monkeypatch):
    import pipeline_integrity_runtime as pir
    import research_runtime as rr
    import script_router_runtime as router
    import script_runtime as sr

    class _Bot:
        def run_robot(self):
            return None

    bot = _Bot()

    def primary_raises(*_args, **_kwargs):
        raise RuntimeError("primary provider failed")

    bot.write_script = primary_raises

    monkeypatch.setattr(rr, "discover_sources", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        rr,
        "build_evidence_pack",
        lambda *_args, **_kwargs: {
            "status": "ok",
            "counts": {"usable_sources": 2, "independent_domains": 2, "claims": 3, "corroborated_claims": 2, "conflicted_claims": 0},
            "sources": [],
        },
    )
    monkeypatch.setattr(rr, "format_evidence_pack_for_script", lambda *_args, **_kwargs: "Verified evidence text.")
    monkeypatch.setattr(rr, "_prepare_primary_writer_data", lambda data, _format: dict(data))

    original_script = {
        "title": "Recovered original script",
        "editorial_angle": "Why the development matters",
        "script": [{
            "voiceover": "A substantive development changes the situation today.",
            "primary_entity": "Subject",
            "specific_search_prompt": "Subject latest development",
        }],
    }

    monkeypatch.setattr(rr, "_openrouter_script_fallback", lambda *_args, **_kwargs: dict(original_script))
    monkeypatch.setattr(
        rr,
        "_ollama_script_fallback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Ollama should not run after OpenRouter succeeds")),
    )
    monkeypatch.setattr(
        pir,
        "strict_fallback",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Extractive fallback should not run after an original provider succeeds")),
    )
    monkeypatch.setattr(pir, "_clean_script_result", lambda result, *_args: dict(result))
    monkeypatch.setattr(sr, "clean_script_data", lambda result, *_args: (dict(result), {"changed_scenes": 0, "removed_scenes": 0}))
    monkeypatch.setattr(sr, "validate_content_density", lambda *_args: (True, "ok"))
    monkeypatch.setattr(sr, "assess_release_structure", lambda *_args: (True, "ok", "Editorial Explainer"))
    monkeypatch.setattr(sr, "check_script_originality", lambda *_args: {"passed": True, "failures": []})
    monkeypatch.setattr(sr, "_run_real_critique", lambda *_args: {"unsupported_claims": []})

    write_script = router.install_script_pipeline(bot)
    result = write_script({"title": "Selected story", "text": "Verified story evidence."}, {}, "technology", object(), "regular")

    assert result["title"] == "Recovered original script"
    assert result.get("fallback_mode") != "extractive_source_grounded"
    assert result.get("public_publish_blocked") is not True


def test_latest_trustworthy_publication_or_update_time_drives_freshness():
    story = {
        "publishedAt": "2026-09-17T10:00:00+00:00",
        "updated_at": "2026-09-21T18:00:00+00:00",
    }

    observed = story_ranker._published_datetime(story)

    assert observed == datetime(2026, 9, 21, 18, 0, tzinfo=timezone.utc)


def test_rss_adapter_accepts_atom_entries(monkeypatch):
    atom = b"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Fresh Atom headline</title>
        <link href="https://example.com/story"/>
        <summary>Useful story summary.</summary>
        <published>2026-09-22T09:30:00Z</published>
        <author><name>Example News</name></author>
      </entry>
    </feed>"""

    monkeypatch.setattr(
        story_ranker.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse(atom),
    )

    rows = story_ranker._rss_items(
        "https://example.com/feed",
        "technology",
        collection_source="official",
        max_items=5,
    )

    assert len(rows) == 1
    assert rows[0]["title"] == "Fresh Atom headline"
    assert rows[0]["url"] == "https://example.com/story"
    assert rows[0]["publishedAt"] == "2026-09-22T09:30:00Z"
    assert rows[0]["source"] == "Example News"


def test_sports_discovery_queries_cover_niche_sports():
    import ultimate_bot

    cfg = ultimate_bot.CONTENT_CATEGORIES["sports"]
    query_blob = " ".join(
        [
            cfg.get("gnews_q", ""),
            cfg.get("india_gnews_q", ""),
            cfg.get("global_gnews_q", ""),
        ]
    ).lower()

    for term in ("golf", "rugby", "motorsport", "boxing", "wrestling", "formula 1"):
        assert term in query_blob


def test_discovery_portfolio_rejects_single_source_headline_without_story_substance():
    story = {
        "title": "Company announces major new move",
        "candidate_score": 14.0,
        "discovery_dimensions": {
            "freshness": 6.0,
            "event_momentum": 2.0,
            "importance": 5.0,
            "shorts_viability": 5.0,
            "corroboration": 2.0,
            "source_quality": 2.0,
        },
        "topic_actionability_score": 4.0,
        "event_source_count": 1,
        "event_article_count": 1,
        "event_actions": ["announce"],
        "event_entities": ["Company"],
        "description": "",
        "summary": "",
        "snippet": "",
        "text": "",
    }

    assert story_ranker._discovery_portfolio_pass(story) is False
    assert story["discovery_rejection"] == "Headline lacks enough story substance behind the event"
    assert story["story_substance_chars"] == 0


def test_discovery_portfolio_accepts_headline_only_event_when_independently_corroborated():
    story = {
        "title": "Company announces major new move",
        "candidate_score": 10.0,
        "discovery_dimensions": {
            "freshness": 6.0,
            "event_momentum": 2.0,
            "importance": 4.0,
            "shorts_viability": 5.0,
            "corroboration": 4.0,
            "source_quality": 2.0,
        },
        "topic_actionability_score": 4.0,
        "event_source_count": 2,
        "event_article_count": 2,
        "event_actions": ["announce"],
        "event_entities": ["Company"],
        "description": "",
        "summary": "",
        "snippet": "",
        "text": "",
    }

    assert story_ranker._discovery_portfolio_pass(story) is True
    assert story["discovery_tier"] == "exploratory"


def test_editorial_score_gives_india_relevance_a_material_ranking_lift():
    common = {
        "description": "A concrete current development with enough detail to support a short-form story.",
        "source": "Example News",
        "url": "https://example.com/story",
        "event_actions": ["launch"],
    }
    india_story = {
        **common,
        "title": "India launches major technology project",
    }
    global_story = {
        **common,
        "title": "Global company launches major technology project",
    }

    india_ranked = story_ranker._editorial_score(
        india_story, [], "technology", "regular", "english", []
    )
    global_ranked = story_ranker._editorial_score(
        global_story, [], "technology", "regular", "english", []
    )

    assert india_ranked["india_relevance_score"] > global_ranked["india_relevance_score"]
    assert india_ranked["candidate_score"] > global_ranked["candidate_score"]


def test_content_categories_keep_an_explicit_india_discovery_lane():
    import ultimate_bot

    assert ultimate_bot.CONTENT_CATEGORIES
    assert all(
        str(config.get("india_gnews_q") or "").strip()
        for config in ultimate_bot.CONTENT_CATEGORIES.values()
    )



def test_non_event_headline_is_rejected_even_when_fresh():
    story = {
        "title": "Live updates: what you need to know about today's announcement",
        "candidate_score": 20.0,
        "discovery_dimensions": {
            "freshness": 8.0,
            "event_momentum": 4.0,
            "importance": 7.0,
            "shorts_viability": 6.0,
            "corroboration": 4.0,
            "source_quality": 3.0,
        },
        "topic_actionability_score": 6.0,
        "event_source_count": 3,
        "event_article_count": 4,
        "description": "A detailed report describing the event, its participants, what changed, and the immediate consequences for the public.",
    }

    assert story_ranker._discovery_portfolio_pass(story) is False
    assert story["discovery_rejection"] == "Non-event/SEO headline"
    assert story["headline_noise_pass"] is False


def test_single_source_event_needs_real_body_substance():
    story = {
        "title": "India launches a new technology project",
        "event_source_count": 1,
        "event_article_count": 1,
        "description": "Brief headline summary only.",
    }

    assert story_ranker._story_substance_pass(story) is False


def test_single_source_event_with_real_body_substance_is_allowed():
    story = {
        "title": "India launches a new technology project",
        "event_source_count": 1,
        "event_article_count": 1,
        "description": (
            "India launched a new technology project after months of preparation. "
            "Officials said the program will expand access, with the first phase "
            "starting this week and further locations scheduled to follow."
        ),
    }

    assert story_ranker._story_substance_pass(story) is True


def test_real_video_event_is_not_rejected_by_headline_noise_filter():
    story = {
        "title": "AI video generation platform launches in India",
        "description": (
            "The company launched a new video generation platform in India, "
            "adding a new capability for creators and businesses."
        ),
    }

    assert story_ranker._headline_noise_pass(story) is True



def test_learning_readers_filter_invalid_vault_rows_at_sql_boundary():
    story_ranker = Path(__file__).resolve().parents[1].joinpath("story_ranker.py").read_text(encoding="utf-8")
    autopilot = Path(__file__).resolve().parents[1].joinpath("autopilot_runtime.py").read_text(encoding="utf-8")

    for source in (story_ranker, autopilot):
        assert "video_id IS NOT NULL" in source
        assert "READY_FOR_UPLOAD" in source
        assert "status NOT IN" in source
