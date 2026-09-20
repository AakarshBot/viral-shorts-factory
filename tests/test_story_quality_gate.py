from datetime import datetime, timezone

import story_ranker


def _fresh_story(**overrides):
    story = {
        "title": "NASA launches Artemis mission from Florida",
        "url": "https://example.com/story/nasa-artemis-launch",
        "source": "Example News",
        "source_name": "Example News",
        "description": "NASA launched the Artemis mission from Florida after a successful countdown and weather check.",
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "collection_source": "gnews",
        "event_article_count": 2,
        "event_source_count": 2,
        "event_non_gdelt_count": 2,
    }
    story.update(overrides)
    return story


def test_article_url_gate_rejects_section_and_search_pages():
    assert story_ranker._article_url_is_plausible(
        _fresh_story(url="https://example.com/search?q=NASA")
    ) is False
    assert story_ranker._article_url_is_plausible(
        _fresh_story(url="https://example.com/category/world")
    ) is False
    assert story_ranker._article_url_is_plausible(
        _fresh_story(url="https://example.com/world")
    ) is False
    assert story_ranker._article_url_is_plausible(
        _fresh_story(url="https://example.com/story/nasa-artemis-launch")
    ) is True


def test_title_gate_rejects_generic_page_headlines():
    assert story_ranker._title_is_story_like("Latest News") is False
    assert story_ranker._title_is_story_like("Sports News") is False
    assert story_ranker._title_is_story_like("NASA launches Artemis mission from Florida") is True
    assert story_ranker._title_is_story_like("Why the new iPhone battery matters") is True


def test_story_intake_gate_rejects_thin_gdelt_only_candidate():
    story = _fresh_story(
        description="",
        text="",
        summary="",
        snippet="",
        event_article_count=1,
        event_source_count=1,
        event_non_gdelt_count=0,
        collection_source="gdelt",
        source="small-unknown-domain.example",
        source_name="small-unknown-domain.example",
        url="https://small-unknown-domain.example/story/123",
    )
    assert story_ranker._story_intake_quality_pass(story) is False
    assert "Radar-only" in story["discovery_rejection"] or "Insufficient article evidence" in story["discovery_rejection"]


def test_story_intake_gate_keeps_real_multi_source_story():
    story = _fresh_story(
        description="Reuters reports that NASA launched Artemis after a successful countdown.",
        event_article_count=3,
        event_source_count=3,
        event_non_gdelt_count=3,
    )
    assert story_ranker._story_intake_quality_pass(story) is True


def test_deduplicate_stage_removes_same_entity_same_action_repeats():
    first = _fresh_story(
        title="NASA launches Artemis mission from Florida",
        event_id="event-a",
        event_entities=["nasa", "artemis"],
        event_actions=["launch"],
        event_corroboration_score=6.0,
    )
    repeated = _fresh_story(
        title="NASA successfully launches Artemis mission",
        event_id="event-b",
        event_entities=["nasa", "artemis"],
        event_actions=["launch"],
        event_corroboration_score=4.0,
    )
    different = _fresh_story(
        title="NASA delays Artemis mission in Florida",
        event_id="event-c",
        event_entities=["nasa", "artemis"],
        event_actions=["delay"],
        event_corroboration_score=4.0,
    )

    selected = story_ranker._deduplicate_stage([first, repeated, different], max_items=10)

    assert [item["title"] for item in selected] == [
        first["title"],
        different["title"],
    ]


def test_discovery_portfolio_rejects_weak_exploratory_padding():
    story = {
        "title": "Generic current headline about a local update",
        "url": "https://example.com/story/local-update",
        "source": "Example News",
        "description": "A brief report with some details about the local update.",
        "event_non_gdelt_count": 1,
        "event_article_count": 1,
        "candidate_score": 15.0,
        "discovery_dimensions": {
            "freshness": 5.0,
            "event_momentum": 1.0,
            "importance": 3.5,
            "shorts_viability": 3.2,
            "corroboration": 1.0,
            "source_quality": 1.0,
        },
    }

    assert story_ranker._discovery_portfolio_pass(story) is False
    assert story["discovery_rejection"] == "Weak editorial importance"
