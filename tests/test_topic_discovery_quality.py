"""Focused tests for topic discovery quality and portfolio behavior."""

from story_ranker import (
    _headline_noise_pass,
    _niche_opportunity_score,
    _source_page_pass,
    diversity_rerank,
)


def test_rejects_roundup_and_headline_only_titles():
    assert _headline_noise_pass({"title": "Latest News: Top Headlines Today"}) is False
    assert _headline_noise_pass({"title": "News Roundup: What You Need To Know"}) is False


def test_rejects_index_style_source_paths():
    story = {"title": "City launches new metro line", "url": "https://example.com/category/city-news/"}
    assert _source_page_pass(story) is False
    assert story["discovery_rejection"] == "Non-article/index source page"


def test_allows_specific_article_paths():
    story = {"title": "City launches new metro line", "url": "https://example.com/news/city-launches-new-metro-line-12345"}
    assert _source_page_pass(story) is True


def test_niche_opportunity_rewards_specific_current_angles():
    niche = _niche_opportunity_score({
        "title": "Uncapped academy player makes domestic debut",
        "description": "The emerging player made a first-team debut after a standout academy season.",
        "event_actions": ["join", "win"],
        "event_entities": ["Academy Player", "State League"],
        "event_source_count": 2,
        "shorts_viability_score": 7,
        "audience_potential_score": 7,
    })
    major = _niche_opportunity_score({
        "title": "Government announces major global summit decision",
        "description": "The decision is being covered widely by major national and international outlets.",
        "event_actions": ["announce"],
        "event_entities": ["Government", "Global Summit"],
        "event_source_count": 8,
        "shorts_viability_score": 7,
        "audience_potential_score": 7,
    })
    assert niche >= 6.0
    assert niche > major


def test_diversity_rerank_reserves_slots_for_qualified_niche_stories():
    stories = []
    for index in range(24):
        stories.append({
            "title": f"Major story {index}",
            "event_entities": [f"Entity {index}"],
            "candidate_score": 30 - index * 0.2,
            "niche_opportunity_score": 1.0,
            "freshness_score": 8.0,
        })
    for index in range(8):
        stories.append({
            "title": f"Specific emerging story {index}",
            "event_entities": [f"Niche Entity {index}", "Emerging"],
            "candidate_score": 20 - index * 0.2,
            "niche_opportunity_score": 7.0,
            "freshness_score": 7.0,
        })

    selected = diversity_rerank(stories, max_items=20)
    niche_count = sum(
        1 for item in selected
        if float(item.get("niche_opportunity_score") or 0) >= 6.0
    )
    assert niche_count >= 6


def test_niche_discovery_query_lanes_are_defined():
    import story_ranker
    for genre in (
        "entertainment",
        "national_global_affairs",
        "sports",
        "sports_stories_of_day",
        "technology",
        "business_finance",
        "health_lifestyle",
        "regional_state_news",
    ):
        assert story_ranker.NICHE_DISCOVERY_QUERIES.get(genre)


def test_single_source_niche_story_can_survive_fact_source_stage_with_substance():
    from story_ranker import _fact_source_stage

    niche = {
        "title": "Uncapped academy player makes domestic debut",
        "description": "The emerging academy player made a first-team domestic debut after a breakout season and became the youngest player from the club to reach the senior squad this year.",
        "url": "https://specialist.example.com/story/uncapped-player-domestic-debut-123",
        "event_clustered": True,
        "event_article_count": 1,
        "event_source_count": 1,
        "event_source_domains": ["specialist.example.com"],
        "event_publishers": ["Specialist Sports"],
        "event_entities": ["Academy Player", "State League"],
        "event_actions": ["join", "debut"],
    }
    result = _fact_source_stage([niche], max_items=1)
    assert result == [niche]
    assert niche["fact_source_pass"] is True


def test_fact_source_stage_keeps_a_niche_slice_when_major_coverage_fills_the_lane():
    from story_ranker import _fact_source_stage

    stories = []
    for index in range(10):
        stories.append({
            "title": f"Major event headline {index}",
            "description": "A widely reported national development with extensive coverage from large publishers and a detailed factual summary.",
            "url": f"https://major.example.com/news/event-{index}",
            "event_clustered": True,
            "event_article_count": 8,
            "event_source_count": 6,
            "event_source_domains": [f"major{index}.example.com"],
            "event_publishers": [f"Major Publisher {index}"],
            "event_entities": [f"Entity {index}"],
            "event_actions": ["announce"],
        })
    for index in range(3):
        stories.append({
            "title": f"Emerging academy debut story {index}",
            "description": "An emerging domestic player made a debut after academy development and a strong season, giving viewers a specific new story beyond the main headline cycle.",
            "url": f"https://specialist.example.com/sports/emerging-debut-{index}",
            "event_clustered": True,
            "event_article_count": 1,
            "event_source_count": 1,
            "event_source_domains": ["specialist.example.com"],
            "event_publishers": ["Specialist Sports"],
            "event_entities": [f"Niche Player {index}", "Academy"],
            "event_actions": ["debut"],
        })

    selected = _fact_source_stage(stories, max_items=10)
    assert any("Emerging academy debut" in item["title"] for item in selected)
