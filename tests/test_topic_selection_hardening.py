from datetime import datetime, timezone

from event_discovery_runtime import cluster_news_events
from story_ranker import _cheap_filter, _fact_source_stage, _originality_stage


def test_cheap_filter_evaluates_full_intake_before_truncating(monkeypatch):
    monkeypatch.setattr(
        "story_ranker._safety_gate",
        lambda _story: (True, []),
    )
    monkeypatch.setattr(
        "story_ranker._age_hours",
        lambda story: float(story["age_hours"]),
    )
    monkeypatch.setattr(
        "story_ranker._freshness_score",
        lambda story: float(story.get("freshness", 0)),
    )
    monkeypatch.setattr(
        "story_ranker._source_quality",
        lambda story: float(story.get("source_quality", 0)),
    )

    stories = [
        {"title": "Older weak event story", "url": "https://example.com/1", "age_hours": 2, "freshness": 1, "source_quality": 1, "event_corroboration_score": 1},
        {"title": "Strong event story one", "url": "https://example.com/2", "age_hours": 3, "freshness": 8, "source_quality": 8, "event_corroboration_score": 8},
        {"title": "Strong event story two", "url": "https://example.com/3", "age_hours": 4, "freshness": 7, "source_quality": 7, "event_corroboration_score": 7},
    ]

    result = _cheap_filter(stories, max_items=2, max_age_hours=48)

    assert [item["title"] for item in result] == [
        "Strong event story one",
        "Strong event story two",
    ]


def test_fact_source_stage_does_not_bypass_failed_source_gate():
    stories = [
        {
            "title": "Social-only claim",
            "url": "https://reddit.com/example",
            "collection_source": "reddit",
        }
    ]

    result = _fact_source_stage(stories, max_items=5)

    assert result == []
    assert stories[0]["fact_source_pass"] is False
    assert stories[0]["discovery_rejection"] == "Insufficient source support"


def test_originality_stage_returns_only_actual_passes():
    stories = [
        {"title": "Previously covered battery breakthrough"},
        {"title": "Fresh robotics factory opens"},
        {"title": "Fresh satellite mission launches"},
    ]

    result = _originality_stage(
        stories,
        ["Previously covered battery breakthrough"],
        max_items=5,
    )

    assert [item["title"] for item in result] == [
        "Fresh robotics factory opens",
        "Fresh satellite mission launches",
    ]
    assert all(item["originality_pass"] is True for item in result)


def test_event_clustering_preserves_category_provenance():
    articles = [
        {
            "title": "OpenAI launches new model",
            "url": "https://example.com/ai",
            "publishedAt": "2026-09-18T10:00:00+00:00",
            "publisher": "Example A",
            "genre": "technology",
        },
        {
            "title": "OpenAI launches new model today",
            "url": "https://example.org/ai",
            "publishedAt": "2026-09-18T09:00:00+00:00",
            "publisher": "Example B",
            "genre": "technology",
        },
    ]

    events = cluster_news_events(articles)

    assert len(events) == 1
    assert events[0]["event_genres"] == ["technology"]
    assert events[0]["primary_genre"] == "technology"
def test_production_selection_does_not_call_legacy_gather(monkeypatch):
    import story_ranker

    calls = {"collect": 0, "rank": 0}

    def canonical_collect(*args, **kwargs):
        calls["collect"] += 1
        return ([{"title": "Canonical event"}], ["social signal"])

    def canonical_rank(stories, **kwargs):
        calls["rank"] += 1
        assert stories == [{"title": "Canonical event"}]
        return stories

    monkeypatch.setattr(story_ranker, "collect_high_recall_stories", canonical_collect)
    monkeypatch.setattr(story_ranker, "rank_story_candidates", canonical_rank)

    def legacy_gather(*_args, **_kwargs):
        raise AssertionError("legacy gather must not be called")

    bot = type("Bot", (), {"gather_and_filter_stories": legacy_gather})()
    story_ranker.patch_story_selection(bot)

    result = bot.gather_and_filter_stories(
        object(),
        "technology",
        {},
    )

    assert result == [{"title": "Canonical event"}]
    assert calls == {"collect": 1, "rank": 1}

