from pathlib import Path

import pytest

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


def test_cricket_scopes_have_event_driven_queries():
    for name in ("India / Asia", "Global", "AI-assisted top story in cricket"):
        query = str(CRICKET_CATEGORIES[name]["query"]).lower()
        assert any(term in query for term in ("record", "milestone", "debut", "selected", "injury", "final", "win"))
        assert "-\"live score\"" in query
        assert "-\"playing xi\"" in query
        assert "-schedule" in query


def test_query_budget_preserves_india_global_and_category_lanes():
    cfg = ultimate_bot.CONTENT_CATEGORIES["technology"]
    queries = story_ranker._build_discovery_google_queries(
        "technology",
        cfg,
        broad_discovery=False,
    )
    assert cfg["india_gnews_q"] in queries
    assert cfg["global_gnews_q"] in queries
    assert queries[0] != cfg["gnews_q"]
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
    import dashboard_topic_discovery_runtime
    captured = []

    def fake_discovery(bot, genre_key, genre_cfg, **kwargs):
        captured.append({
            "genre_key": genre_key,
            "genre_cfg": dict(genre_cfg),
            "cricket_scope": kwargs.get("cricket_scope"),
        })
        return []

    monkeypatch.setattr(
        dashboard_topic_discovery_runtime,
        "discover_dashboard_topics",
        fake_discovery,
    )

    bot = type(
        "Bot",
        (),
        {"CONTENT_CATEGORIES": ultimate_bot.CONTENT_CATEGORIES, "_active_web_config": {}},
    )()

    dashboard_runtime.discover_ranked_topics(
        bot,
        {"format_mode": "cricket", "cricket_pipeline": True, "cricket_category": "India / Asia"},
        None,
        max_candidates=5,
    )
    india_cfg = captured[-1]
    assert india_cfg["cricket_scope"] == "India / Asia"
    assert india_cfg["genre_key"] == "sports_stories_of_day"
    assert india_cfg["genre_cfg"]["global_gnews_q"] == ""
    assert "India" in india_cfg["genre_cfg"]["india_gnews_q"]

    dashboard_runtime.discover_ranked_topics(
        bot,
        {"format_mode": "cricket", "cricket_pipeline": True, "cricket_category": "Global"},
        None,
        max_candidates=5,
    )
    global_cfg = captured[-1]
    assert global_cfg["cricket_scope"] == "Global"
    assert global_cfg["genre_cfg"]["india_gnews_q"] == ""
    assert "ICC" in global_cfg["genre_cfg"]["global_gnews_q"]


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



def test_all_dashboard_topic_catalog_entries_have_valid_production_categories():
    import app

    for mode, topics in (
        ("regular", app.DEEP_DIVE_TOPICS),
        ("top5", app.TOP_FIVE_TOPICS),
    ):
        labels = app.category_options(mode, "Top 5" if mode == "top5" else "Deep Dive")
        assert labels
        assert set(labels.values()).issubset(set(ultimate_bot.CONTENT_CATEGORIES))
        assert set(topics).issubset(set(labels.values()))


def test_sports_ai_discovery_routes_through_real_sports_lanes(monkeypatch):
    import dashboard_runtime
    import dashboard_topic_discovery_runtime
    captured = {}

    def fake_discovery(bot, genre_key, genre_cfg, **kwargs):
        captured["genre_key"] = genre_key
        captured["genre_cfg"] = dict(genre_cfg)
        captured["broad_discovery"] = True
        return []

    monkeypatch.setattr(
        dashboard_topic_discovery_runtime,
        "discover_dashboard_topics",
        fake_discovery,
    )

    bot = type("Bot", (), {"CONTENT_CATEGORIES": ultimate_bot.CONTENT_CATEGORIES})()
    dashboard_runtime.discover_ai_topics(
        bot,
        {
            "format_mode": "regular",
            "editorial_mode": "AI",
            "discovery_mode": "ai_sports",
            "category": "sports",
            "language": "english",
        },
        None,
        max_candidates=5,
    )

    assert captured["genre_key"] == "sports"
    assert captured["genre_cfg"]["india_gnews_q"]
    assert captured["genre_cfg"]["global_gnews_q"]
    assert captured["broad_discovery"] is True


def test_sports_ai_discovery_tolerates_legacy_virtual_category(monkeypatch):
    import dashboard_runtime
    import dashboard_topic_discovery_runtime
    captured = {}

    monkeypatch.setattr(
        dashboard_topic_discovery_runtime,
        "discover_dashboard_topics",
        lambda bot, genre_key, genre_cfg, **kwargs: captured.setdefault("genre_key", genre_key) or [],
    )

    bot = type("Bot", (), {"CONTENT_CATEGORIES": ultimate_bot.CONTENT_CATEGORIES})()
    dashboard_runtime.discover_ai_topics(
        bot,
        {
            "format_mode": "regular",
            "editorial_mode": "AI",
            "category": "ai_recommendation",
            "language": "english",
        },
        None,
        max_candidates=5,
    )
    assert captured["genre_key"] == "sports"


def test_legacy_ai_category_is_normalized_before_production_lookup():
    source = Path(ultimate_bot.__file__).read_text(encoding="utf-8")
    start = source.index('cat_choice = web_config.get("category", "national_global_affairs")')
    window = source[start:start + 700]
    assert 'if str(cat_choice or "").strip().lower() == "ai_recommendation":' in window
    assert 'cat_choice = "sports"' in window



@pytest.mark.parametrize(
    "category,format_mode",
    [
        ("national_global_affairs", "regular"),
        ("technology", "regular"),
        ("business_finance", "regular"),
        ("entertainment", "regular"),
        ("viral_phenomenon", "regular"),
        ("health_lifestyle", "regular"),
        ("regional_state_news", "regular"),
        ("national_global_affairs", "top5"),
        ("technology", "top5"),
        ("business_finance", "top5"),
        ("entertainment", "top5"),
        ("viral_phenomenon", "top5"),
    ],
)

def test_each_dashboard_category_can_enter_ranked_discovery(monkeypatch, category, format_mode):
    import dashboard_runtime
    import dashboard_topic_discovery_runtime
    captured = {}

    def fake_discovery(bot, genre_key, genre_cfg, **kwargs):
        captured["genre_key"] = genre_key
        captured["genre_cfg"] = dict(genre_cfg)
        captured["broad_discovery"] = True
        return []

    monkeypatch.setattr(
        dashboard_topic_discovery_runtime,
        "discover_dashboard_topics",
        fake_discovery,
    )

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
        {
            "format_mode": format_mode,
            "category": category,
            "language": "english",
        },
        None,
        max_candidates=5,
    )

    assert captured["genre_key"] == category
    assert captured["genre_cfg"] == ultimate_bot.CONTENT_CATEGORIES[category]
    assert captured["broad_discovery"] is True



def test_niche_sports_discovery_routes_through_sports_category(monkeypatch):
    import dashboard_runtime
    import dashboard_topic_discovery_runtime
    captured = {}

    def fake_discovery(bot, genre_key, genre_cfg, **kwargs):
        captured["genre_key"] = genre_key
        captured["genre_cfg"] = dict(genre_cfg)
        captured["broad_discovery"] = True
        return []

    monkeypatch.setattr(
        dashboard_topic_discovery_runtime,
        "discover_dashboard_topics",
        fake_discovery,
    )

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
        {
            "format_mode": "regular",
            "editorial_mode": "Niche Sports",
            "category": "sports",
            "language": "english",
        },
        None,
        max_candidates=5,
    )

    assert captured["genre_key"] == "sports"
    assert captured["genre_cfg"] == ultimate_bot.CONTENT_CATEGORIES["sports"]
    assert captured["broad_discovery"] is True
