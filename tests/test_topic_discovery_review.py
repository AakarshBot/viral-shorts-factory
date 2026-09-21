from datetime import datetime, timezone

import story_ranker


class _FakeResponse:
    status_code = 200

    def __init__(self, content):
        self.content = content


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
