import ultimate_bot
import story_ranker
from workflow_runtime import CRICKET_CATEGORIES


DASHBOARD_GENRES = {
    "national_global_affairs",
    "technology",
    "business_finance",
    "entertainment",
    "viral_phenomenon",
    "health_lifestyle",
    "regional_state_news",
}


def test_dashboard_genres_have_india_first_queries():
    for genre in DASHBOARD_GENRES:
        cfg = ultimate_bot.CONTENT_CATEGORIES[genre]
        assert str(cfg.get("india_gnews_q") or "").strip(), genre


def test_dashboard_genres_have_global_lane_except_regional():
    for genre in DASHBOARD_GENRES - {"regional_state_news"}:
        cfg = ultimate_bot.CONTENT_CATEGORIES[genre]
        assert str(cfg.get("global_gnews_q") or "").strip(), genre
    assert ultimate_bot.CONTENT_CATEGORIES["regional_state_news"].get("global_gnews_q") == ""


def test_cricket_scopes_have_story_event_queries():
    for name in ("India / Asia", "Global", "AI-assisted top story in cricket"):
        query = str(CRICKET_CATEGORIES[name]["query"]).lower()
        assert "match" in query
        assert any(term in query for term in ("result", "squad", "record", "series", "final", "win", "loss"))


def test_query_budget_preserves_india_global_and_category_lanes():
    cfg = ultimate_bot.CONTENT_CATEGORIES["technology"]
    queries = story_ranker._build_discovery_google_queries(
        "technology",
        cfg,
        broad_discovery=False,
    )
    assert queries[0] == cfg["india_gnews_q"]
    assert cfg["global_gnews_q"] in queries
    assert len(queries) <= story_ranker.DISCOVERY_MAX_GOOGLE_QUERIES_STANDARD


def test_query_budget_is_parallel_budgeted_not_unbounded():
    cfg = ultimate_bot.CONTENT_CATEGORIES["business_finance"]
    queries = story_ranker._build_discovery_google_queries(
        "business_finance",
        cfg,
        broad_discovery=True,
    )
    assert cfg["india_gnews_q"] in queries
    assert cfg["global_gnews_q"] in queries
    assert len(queries) <= story_ranker.DISCOVERY_MAX_GOOGLE_QUERIES_BROAD


def test_cricket_scope_routing_uses_selected_scope_lane(monkeypatch):
    import dashboard_runtime
    captured = []

    def fake_collect(*args, **kwargs):
        captured.append(args[2])
        return ([], [])

    monkeypatch.setattr(story_ranker, "collect_high_recall_stories", fake_collect)
    monkeypatch.setattr(story_ranker, "rank_discovery_candidates", lambda stories, **kwargs: stories)

    bot = type(
        "Bot",
        (),
        {
            "CONTENT_CATEGORIES": ultimate_bot.CONTENT_CATEGORIES,
            "_active_web_config": {},
        },
    )()

    dashboard_runtime.discover_ranked_topics(
        bot,
        {"format_mode": "cricket", "cricket_pipeline": True, "cricket_category": "India / Asia"},
        None,
        max_candidates=5,
    )
    india_cfg = captured[-1]
    assert india_cfg["global_gnews_q"] == ""
    assert "India" in india_cfg["india_gnews_q"]

    dashboard_runtime.discover_ranked_topics(
        bot,
        {"format_mode": "cricket", "cricket_pipeline": True, "cricket_category": "Global"},
        None,
        max_candidates=5,
    )
    global_cfg = captured[-1]
    assert global_cfg["india_gnews_q"] == ""
    assert "ICC" in global_cfg["global_gnews_q"]


def test_dual_geo_genre_query_budget_skips_redundant_base_lane():
    cfg = ultimate_bot.CONTENT_CATEGORIES["technology"]
    queries = story_ranker._build_discovery_google_queries(
        "technology",
        cfg,
        broad_discovery=True,
    )
    assert cfg["india_gnews_q"] in queries
    assert cfg["global_gnews_q"] in queries
    assert cfg["gnews_q"] not in queries
    assert len(queries) <= story_ranker.DISCOVERY_MAX_GOOGLE_QUERIES_BROAD
