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



def test_discovery_query_lanes_are_bounded_and_category_aware():
    lanes = story_ranker._discovery_query_lanes(
        "Cricket OR BCCI",
        genre_key="sports_stories_of_day",
        ai_cricket=True,
    )
    assert lanes[0] == "Cricket OR BCCI"
    assert len(lanes) == 4
    assert any("record" in query for query in lanes[1:])
    assert any("latest" in query for query in lanes[1:])


def test_updated_timestamp_can_make_a_currently_updated_story_fresh():
    story = {
        "published_at": _iso(240),
        "updated_at": _iso(2),
    }
    assert story_ranker._age_hours(story) < 3
    assert story_ranker._freshness_score(story) >= 6


def test_recent_topic_cooldown_allows_a_new_event_action():
    class Conn:
        def execute(self, _query):
            return [
                ("India beat Afghanistan in Delhi", _iso(20)),
            ]

    candidate = {
        "title": "India names squad after Varun injury",
        "event_actions": ["announce", "injury"],
    }
    kept = story_ranker._recent_topic_cooldown(Conn(), [candidate], hours=72)
    assert kept == [candidate]


def test_candidate_quality_floor_rejects_stale_low_value_topic():
    story = {
        "candidate_score": 20.0,
        "discovery_dimensions": {
            "freshness": 0.0,
            "event_momentum": 0.0,
            "importance": 7.0,
            "shorts_viability": 7.0,
            "corroboration": 3.0,
            "source_quality": 3.0,
        },
    }
    assert story_ranker._candidate_quality_pass(story) is False
    assert story["discovery_rejection"] == "Insufficient current-event signal"


def test_candidate_quality_floor_keeps_current_supported_topic():
    story = {
        "candidate_score": 24.0,
        "discovery_dimensions": {
            "freshness": 8.0,
            "event_momentum": 5.0,
            "importance": 7.0,
            "shorts_viability": 6.0,
            "corroboration": 4.0,
            "source_quality": 3.0,
        },
    }
    assert story_ranker._candidate_quality_pass(story) is True


def test_discovery_portfolio_keeps_current_niche_topic_but_marks_it_exploratory():
    story = {
        "candidate_score": 11.5,
        "discovery_dimensions": {
            "freshness": 6.0,
            "event_momentum": 1.25,
            "importance": 3.0,
            "shorts_viability": 2.5,
            "corroboration": 2.0,
            "source_quality": 1.0,
        },
    }

    assert story_ranker._discovery_portfolio_pass(story) is True
    assert story["discovery_tier"] == "exploratory"


def test_discovery_portfolio_still_rejects_stale_low_signal_topic():
    story = {
        "candidate_score": 24.0,
        "discovery_dimensions": {
            "freshness": 0.0,
            "event_momentum": 0.0,
            "importance": 8.0,
            "shorts_viability": 7.0,
            "corroboration": 4.0,
            "source_quality": 3.0,
        },
    }

    assert story_ranker._discovery_portfolio_pass(story) is False
    assert story["discovery_rejection"] == "Insufficient current-event signal"
