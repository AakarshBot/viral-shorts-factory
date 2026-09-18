from datetime import datetime, timezone

from event_discovery_runtime import cluster_news_events
from story_ranker import (
    _cheap_filter,
    _fact_source_stage,
    _originality_stage,
    _discovery_lane_queries,
    diversity_rerank,
)


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

def test_adaptive_queries_target_underrepresented_configured_subjects():
    from story_ranker import _adaptive_query_candidates

    events = [{"title": "Artificial intelligence startup launches product"}]
    queries = _adaptive_query_candidates(
        "Artificial Intelligence OR Gadgets OR Startups OR Tech Launch",
        events,
        max_queries=2,
    )

    assert queries
    assert "Gadgets" in queries or "Tech Launch" in queries
    assert len(queries) <= 2


def test_adaptive_queries_do_not_expand_single_subject_query():
    from story_ranker import _adaptive_query_candidates

    assert _adaptive_query_candidates("Artificial Intelligence", [], max_queries=2) == []


def test_cricket_discovery_uses_complementary_free_lanes():
    lanes = _discovery_lane_queries(
        "sports_stories_of_day",
        "Cricket OR ICC OR BCCI OR Test Cricket OR T20 Cricket",
    )

    assert len(lanes) == 4
    assert any("women" in lane.lower() for lane in lanes)
    assert any("bcci" in lane.lower() for lane in lanes)
    assert any("sponsorship" in lane.lower() for lane in lanes)


def test_custom_non_cricket_query_does_not_get_forced_cricket_lanes():
    assert _discovery_lane_queries(
        "sports_stories_of_day",
        "Formula 1 OR MotoGP",
    ) == []


def test_diversity_reranker_separates_repeated_subjects():
    stories = [
        {
            "title": "Rishabh Pant omitted from India squad",
            "candidate_score": 100,
            "event_search_text": "Rishabh Pant omitted from India squad",
            "event_entities": ["Rishabh Pant", "India"],
            "event_actions": ["announce"],
        },
        {
            "title": "Rishabh Pant selection debate grows",
            "candidate_score": 98,
            "event_search_text": "Rishabh Pant selection debate India squad",
            "event_entities": ["Rishabh Pant", "India"],
            "event_actions": ["announce"],
        },
        {
            "title": "Major satellite mission launches",
            "candidate_score": 90,
            "event_search_text": "Major satellite mission launches",
            "event_entities": ["Satellite Mission"],
            "event_actions": ["launch"],
        },
    ]

    selected = diversity_rerank(stories, max_items=3)

    assert selected[0]["title"] == "Rishabh Pant omitted from India squad"
    assert selected[1]["title"] == "Major satellite mission launches"
    assert len(selected) == 3
