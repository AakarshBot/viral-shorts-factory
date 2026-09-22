import story_ranker
"""Focused tests for topic discovery quality and portfolio behavior."""

from story_ranker import (
    _cricket_service_title_pass,
    _cricket_story_worthiness_score,
    _cricket_story_worthiness_pass,
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


def test_cricket_service_articles_are_rejected_before_ranking():
    service_titles = [
        "India vs Japan T20I live streaming: where to watch",
        "India vs Japan playing XI prediction and probable XI",
        "India vs Japan match preview: pitch, weather and timings",
        "India vs Japan tickets, schedule and live score",
    ]
    for title in service_titles:
        story = {"title": title}
        assert _cricket_service_title_pass(story) is False
        assert _cricket_story_worthiness_score(story) == 0.0


def test_cricket_worthiness_accepts_real_current_developments():
    stories = [
        {
            "title": "India survive Japan scare to clinch 200th T20I win",
            "description": "India won by two runs in a rain-shortened match, becoming the first side to reach 200 T20I victories.",
            "event_entities": ["India", "Japan"],
            "event_actions": ["win"],
            "event_source_count": 2,
        },
        {
            "title": "India Women win cricket gold, country's first at Asian Games 2026",
            "description": "India defeated Sri Lanka by 147 runs to secure the country's first gold medal at the Asian Games.",
            "event_entities": ["India Women", "Sri Lanka", "Asian Games"],
            "event_actions": ["win"],
            "event_source_count": 3,
        },
        {
            "title": "DDCA writes to BCCI about corrupt approach",
            "description": "DDCA informed the BCCI about an alleged corrupt approach involving a player, with the matter reported to the anti-corruption unit for investigation.",
            "event_entities": ["DDCA", "BCCI", "DPL"],
            "event_actions": ["investigation"],
            "event_source_count": 1,
        },
    ]
    for story in stories:
        assert _cricket_story_worthiness_score(story) >= 5.0
        assert _cricket_story_worthiness_pass(story, minimum_score=5.0) is True


def test_niche_but_service_like_cricket_story_does_not_qualify_for_editorial_floor():
    story = {
        "title": "India Women probable XI for next match",
        "description": "Possible playing XI combinations and team selection options ahead of the next match.",
        "event_entities": ["India Women"],
    }
    assert _cricket_story_worthiness_pass(story, minimum_score=5.0) is False
    assert story["discovery_rejection"] == "Low-value cricket service article"


def test_india_cricket_discovery_uses_multiple_editorial_lanes():
    cfg = {
        "india_gnews_q": "old narrow India cricket query",
        "gnews_q": "old narrow India cricket query",
    }
    queries = story_ranker._build_discovery_google_queries(
        "sports_stories_of_day",
        cfg,
        broad_discovery=True,
    )

    assert len(queries) >= 7
    joined = "\n".join(queries).lower()
    assert "cricket" in joined
    assert "said" in joined or "controversy" in joined
    assert "selection" in joined or "injury" in joined
    assert "record" in joined or "upset" in joined
    assert "ranji" in joined or "u19" in joined or "domestic" in joined
    assert "india pakistan" in joined or "rivalry" in joined
    assert "old narrow india cricket query" not in queries

def test_cricket_worthiness_accepts_quote_and_conflict_story_without_result_keyword():
    story = {
        "title": "Gautam Gambhir called out over India team selection",
        "description": "A senior cricket figure criticised the selection approach and called for a rethink, triggering a fresh debate among supporters.",
        "event_entities": ["Gautam Gambhir", "India"],
        "event_actions": ["comment"],
        "event_source_count": 2,
    }
    score = story_ranker._cricket_story_worthiness_score(story)
    assert score >= 5.0
    assert story_ranker._cricket_story_worthiness_pass(story, minimum_score=5.0) is True

def test_dashboard_fact_source_stage_keeps_strong_hook_single_source():
    story = {
        "title": "Former Pakistan batter calls India arrogant",
        "description": "The former batter criticised India's approach and called the rivalry unusually heated.",
        "url": "https://example.com/cricket/story",
        "source": "ESPNcricinfo",
        "event_clustered": True,
        "event_article_count": 1,
        "event_source_count": 1,
        "event_source_domains": ["espncricinfo.com"],
        "event_publishers": ["ESPNcricinfo"],
        "event_entities": ["Pakistan batter", "India"],
        "event_actions": [],
    }
    result = story_ranker._fact_source_stage(
        [story],
        max_items=1,
        allow_strong_hook_single_source=True,
    )
    assert result == [story]
    assert story["fact_source_pass"] is True


def test_originality_keeps_distinct_event_ids_with_similar_headlines():
    stories = [
        {
            "title": "India batter responds to strong criticism",
            "event_id": "event-1",
        },
        {
            "title": "India batter responds to strong criticism after new row",
            "event_id": "event-2",
        },
    ]
    selected = story_ranker._originality_stage(stories, used_topics=[], max_items=2)
    assert len(selected) == 2


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


def test_mega_event_score_identifies_saturated_mass_news_without_becoming_a_hard_rejection():
    from story_ranker import _major_event_score

    score = _major_event_score({
        "title": "Global government summit decision draws worldwide attention",
        "description": "The major policy decision is being covered by national and international outlets.",
        "event_source_count": 7,
        "event_article_count": 12,
    })
    assert score >= 3.5


def test_hook_potential_prefers_conflict_and_quote_over_routine_cricket_news():
    from story_ranker import _hook_potential_score

    conflict = {
        "title": "Former Pakistan batter calls India arrogant after the latest clash",
        "description": "The former batter criticised India's approach and described the rivalry in unusually strong terms.",
        "event_actions": ["comment"],
        "event_entities": ["Pakistan batter", "India"],
    }
    routine = {
        "title": "India vs Australia probable XI, schedule and match timings",
        "description": "The teams prepare for the next match with the probable playing XI and scheduled timings.",
        "event_actions": ["schedule"],
        "event_entities": ["India", "Australia"],
    }

    assert _hook_potential_score(conflict) > _hook_potential_score(routine)
    assert "conflict/tension" in conflict["hook_potential_signals"]
    assert "routine-news penalty" in routine["hook_potential_signals"]


def test_cricket_worthiness_is_not_satisfied_by_routine_headline_alone():
    from story_ranker import _cricket_story_worthiness_score, _cricket_story_worthiness_pass

    routine = {
        "title": "India squad announced for upcoming series with schedule details",
        "description": "The squad announcement covers the selected players and the upcoming series schedule.",
        "event_entities": ["India", "BCCI"],
        "event_actions": ["announce"],
        "event_source_count": 3,
        "event_article_count": 4,
    }
    strong = {
        "title": "Former Pakistan batter calls India arrogant after major rivalry clash",
        "description": "The former batter called India arrogant after the match, triggering a fresh debate around the rivalry.",
        "event_entities": ["Pakistan batter", "India"],
        "event_actions": ["comment"],
        "event_source_count": 3,
        "event_article_count": 4,
    }

    assert _cricket_story_worthiness_score(strong) > _cricket_story_worthiness_score(routine)
    assert _cricket_story_worthiness_pass(strong, minimum_score=5.0) is True
    assert _cricket_story_worthiness_pass(routine, minimum_score=5.0) is False

def test_cricket_event_family_groups_asian_games_and_known_competitions():
    from story_ranker import _cricket_event_family

    assert _cricket_event_family({"title": "India Women win cricket gold at the Asian Games"}) == "asian_games"
    assert _cricket_event_family({"title": "India-Pakistan clash sparks a fresh cricket row"}) == "india_pakistan_rivalry"
    assert _cricket_event_family({"title": "Virat Kohli responds to criticism over India selection"}) == "person:virat_kohli"


def test_cricket_diversity_caps_one_umbrella_event_in_the_first_page():
    stories = []
    for index in range(5):
        stories.append({
            "title": f"India cricket Asian Games story {index}",
            "event_entities": ["Asian Games", f"Player {index}"],
            "event_actions": ["win"],
            "candidate_score": 40 - index,
            "niche_opportunity_score": 2.0,
            "freshness_score": 9.0,
            "discovery_target_category": "sports_stories_of_day",
            "cricket_story_worthiness_score": 7.0,
        })
    for index, title in enumerate([
        "Gautam Gambhir called India arrogant after major rivalry clash",
        "Rashid Khan praises an Indian star in a stunning statement",
        "India selection row erupts after major squad shock",
        "Virat Kohli responds to fresh criticism from a former batter",
    ]):
        stories.append({
            "title": title,
            "event_entities": ["India", f"Different Entity {index}"],
            "event_actions": ["comment"],
            "candidate_score": 35 - index,
            "niche_opportunity_score": 4.0,
            "freshness_score": 9.0,
            "discovery_target_category": "sports_stories_of_day",
            "cricket_story_worthiness_score": 7.0,
        })

    selected = diversity_rerank(stories, max_items=6)
    families = [
        item.get("cricket_event_family") or story_ranker._cricket_event_family(item)
        for item in selected
    ]
    assert families.count("asian_games") <= 2
    assert any(family != "asian_games" for family in families[:6])


def test_cricket_diversity_does_not_pad_when_only_one_family_exists():
    stories = [
        {
            "title": f"Asian Games India cricket story {index}",
            "event_entities": ["Asian Games", f"Player {index}"],
            "event_actions": ["win"],
            "candidate_score": 30 - index,
            "niche_opportunity_score": 2.0,
            "freshness_score": 9.0,
            "discovery_target_category": "sports_stories_of_day",
            "cricket_story_worthiness_score": 7.0,
        }
        for index in range(5)
    ]
    selected = diversity_rerank(stories, max_items=5)
    assert len(selected) == 5


def test_cricket_headline_hook_ignores_clustered_body_quotes():
    story = {
        "title": "India women win cricket gold at Asian Games",
        "event_search_text": "Former Pakistan batter calls India arrogant in separate coverage",
        "description": "The gold medal capped a successful tournament.",
        "event_entities": ["India Women", "Asian Games"],
        "event_actions": ["win"],
    }
    score = story_ranker._hook_potential_score(story)
    assert "conflict/tension" not in story["hook_potential_signals"]
    assert score < 5.0


def test_cricket_marquee_quote_outscores_plain_result_for_youtube_fit():
    quote = {
        "title": "Rashid Khan calls Indian sensation God-gifted",
        "description": "Rashid Khan praised the Indian batter after the match.",
        "event_entities": ["Rashid Khan", "India"],
        "event_actions": ["comment"],
    }
    result = {
        "title": "India win another Asian Games cricket match",
        "description": "India won the match to move closer to the medal round.",
        "event_entities": ["India", "Asian Games"],
        "event_actions": ["win"],
    }
    assert story_ranker._cricket_story_worthiness_score(quote) > story_ranker._cricket_story_worthiness_score(result)
}
