from datetime import datetime, timedelta, timezone

import story_ranker


def _iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_event_momentum_rewards_recent_reporting_velocity():
    fast_event = {
        "event_clustered": True,
        "event_evidence": [
            {"publishedAt": _iso(0.5), "publisher": "Reuters"},
            {"publishedAt": _iso(1.5), "publisher": "BBC"},
            {"publishedAt": _iso(4), "publisher": "AP"},
        ],
    }
    slow_event = {
        "event_clustered": True,
        "event_evidence": [
            {"publishedAt": _iso(18), "publisher": "Reuters"},
            {"publishedAt": _iso(22), "publisher": "BBC"},
        ],
    }

    assert story_ranker._event_momentum_score(fast_event) > story_ranker._event_momentum_score(slow_event)
    assert story_ranker._event_momentum_score(fast_event) > 0


def test_independent_corroboration_ignores_raw_article_volume():
    event = {
        "event_article_count": 12,
        "event_source_count": 2,
        "event_source_domains": ["reuters.com", "bbc.com"],
        "event_evidence_publishers": ["Reuters", "BBC"],
    }

    assert story_ranker._independent_corroboration_score(event) == 5.0


def test_editorial_score_exposes_event_level_dimensions(monkeypatch):
    monkeypatch.setattr(story_ranker, "_trend_signal", lambda value: 0.0)
    story = {
        "title": "NASA launches Artemis mission",
        "event_search_text": (
            "NASA launches Artemis mission "
            "Artemis lifts off as NASA begins lunar journey"
        ),
        "event_clustered": True,
        "event_source_count": 3,
        "event_source_domains": ["reuters.com", "bbc.com", "apnews.com"],
        "event_evidence_publishers": ["Reuters", "BBC", "Associated Press"],
        "event_corroboration_score": 6.0,
        "event_article_count": 3,
        "event_evidence": [
            {"publishedAt": _iso(0.5), "publisher": "Reuters"},
            {"publishedAt": _iso(1.5), "publisher": "BBC"},
            {"publishedAt": _iso(2.5), "publisher": "Associated Press"},
        ],
        "originality_score": 8.0,
    }

    ranked = story_ranker._editorial_score(
        story,
        rows=[],
        target_category="",
        target_format="",
        target_language="",
        social_titles=[],
    )

    assert ranked["event_momentum_score"] > 0
    assert ranked["independent_corroboration_score"] == 7.5
    assert ranked["discovery_dimensions"]["event_momentum"] == ranked["event_momentum_score"]
    assert ranked["discovery_dimensions"]["independent_corroboration"] == 7.5

def test_editorial_score_exposes_separate_editorial_audience_and_shorts_dimensions(monkeypatch):
    monkeypatch.setattr(story_ranker, "_trend_signal", lambda value: 2.0)
    story = {
        "title": "NASA launches Artemis mission after historic countdown",
        "event_search_text": "NASA launches Artemis mission after historic countdown",
        "event_clustered": True,
        "event_actions": ["launch"],
        "event_source_count": 3,
        "event_source_domains": ["nasa.gov", "reuters.com", "bbc.com"],
        "event_evidence_publishers": ["NASA", "Reuters", "BBC"],
        "event_corroboration_score": 6.0,
        "event_article_count": 3,
        "event_evidence": [
            {"publishedAt": _iso(0.5), "publisher": "NASA"},
            {"publishedAt": _iso(1.5), "publisher": "Reuters"},
            {"publishedAt": _iso(2.5), "publisher": "BBC"},
        ],
        "originality_score": 8.0,
        "velocity_score": 7.0,
        "trend_bonus": 5.0,
    }

    ranked = story_ranker._editorial_score(
        story,
        rows=[],
        target_category="",
        target_format="",
        target_language="",
        social_titles=["NASA Artemis launch draws huge public attention"],
    )

    dimensions = ranked["discovery_dimensions"]
    assert 0.0 <= dimensions["importance"] <= 10.0
    assert 0.0 <= dimensions["audience_potential"] <= 10.0
    assert 0.0 <= dimensions["shorts_viability"] <= 10.0
    assert ranked["importance_score"] == dimensions["importance"]
    assert ranked["audience_potential_score"] == dimensions["audience_potential"]
    assert ranked["shorts_viability_score"] == dimensions["shorts_viability"]


def test_adaptive_discovery_query_requires_repeated_social_novelty():
    assert story_ranker._adaptive_discovery_query(
        "technology news",
        [
            "OpenAI unveils a new product",
            "OpenAI product launch draws attention",
            "OpenAI dominates discussion today",
        ],
    ) == "openai product"

    assert story_ranker._adaptive_discovery_query(
        "technology news",
        ["single unrelated topic"],
    ) == ""

