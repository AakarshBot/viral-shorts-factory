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
