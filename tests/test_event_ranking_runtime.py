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


def test_historical_discovery_score_uses_similar_completed_signal_profiles():
    current = {
        "discovery_dimensions": {
            "event_momentum": 7.0,
            "freshness": 8.0,
            "corroboration": 6.0,
            "independent_corroboration": 7.5,
            "source_quality": 4.0,
            "social_signal": 2.0,
            "google_trends": 1.0,
            "visual_potential": 8.0,
            "originality": 7.0,
            "safety_risk": 0.0,
        }
    }

    good_profile = {
        "discovery_dimensions": dict(current["discovery_dimensions"]),
    }
    weaker_profile = {
        "discovery_dimensions": {
            **current["discovery_dimensions"],
            "visual_potential": 3.0,
            "originality": 3.0,
        }
    }

    rows = [
        {
            "status": "COMPLETED",
            "video_id": "good-1",
            "avg_view_percentage": 80.0,
            "discovery_json": __import__("json").dumps(good_profile),
        },
        {
            "status": "COMPLETED",
            "video_id": "good-2",
            "avg_view_percentage": 78.0,
            "discovery_json": __import__("json").dumps(good_profile),
        },
        {
            "status": "COMPLETED",
            "video_id": "weak-1",
            "avg_view_percentage": 45.0,
            "discovery_json": __import__("json").dumps(weaker_profile),
        },
    ]

    signal, matches = story_ranker._historical_discovery_score(current, rows)

    assert matches == 3
    assert 7.0 < signal <= 8.0


def test_historical_discovery_score_waits_for_two_matching_completed_runs():
    story = {
        "discovery_dimensions": {
            "event_momentum": 7.0,
            "freshness": 8.0,
            "corroboration": 6.0,
            "independent_corroboration": 7.5,
            "source_quality": 4.0,
            "social_signal": 2.0,
            "google_trends": 1.0,
            "visual_potential": 8.0,
            "originality": 7.0,
            "safety_risk": 0.0,
        }
    }
    row = {
        "status": "COMPLETED",
        "video_id": "only-one",
        "avg_view_percentage": 90.0,
        "discovery_json": __import__("json").dumps(
            {"discovery_dimensions": story["discovery_dimensions"]}
        ),
    }

    signal, matches = story_ranker._historical_discovery_score(story, [row])

    assert signal == 0.0
    assert matches == 1
