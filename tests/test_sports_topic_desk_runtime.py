from datetime import datetime, timezone

import sports_topic_desk_runtime as desk


def _article(title, domain="example.com", profile="news"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "title": title,
        "text": title,
        "description": title,
        "source": domain,
        "source_name": domain,
        "url": f"https://{domain}/{title.lower().replace(' ', '-')}",
        "publishedAt": now,
        "collection_source": "google_news_rss",
        "discovery_profile": profile,
    }


def test_profile_queries_are_small_and_editorially_distinct():
    profiles = desk._profile_queries("India / Asia")
    assert set(profiles) == {"news", "emerging", "social"}
    assert all(1 <= len(queries) <= 2 for queries in profiles.values())
    assert any("selection" in query for query in profiles["news"])
    assert any("breakthrough" in query or "unusual" in query for query in profiles["emerging"])
    assert any("reaction" in query or "fans" in query for query in profiles["social"])


def test_cricket_detection_and_scope_are_strict():
    assert desk._is_cricket({"title": "Bumrah returns after injury"}) is True
    assert desk._is_cricket({"title": "Coach announces new football plan"}) is False
    assert desk._scope_pass({"title": "Bumrah returns after injury", "event_entities": ["Bumrah"]}, "India / Asia") is True
    assert desk._scope_pass({"title": "Ben Stokes returns after injury", "event_entities": ["Ben Stokes"]}, "India / Asia") is False


def test_normalise_rows_keeps_recent_safe_cricket(monkeypatch):
    row = _article("India batter breaks a record", "sports.example")
    monkeypatch.setattr(desk.sr, "_safety_gate", lambda story: (True, []))
    monkeypatch.setattr(desk.sr, "_source_page_pass", lambda story: True)
    monkeypatch.setattr(desk.sr, "_cricket_service_title_pass", lambda story: True)
    result = desk._normalise_rows([row])
    assert len(result) == 1
    assert result[0]["discovery_profile"] == "news"


def test_normalise_rows_filters_non_cricket_for_india_desk(monkeypatch):
    football = _article("India football comeback reaches final", "sports.example")
    monkeypatch.setattr(desk.sr, "_safety_gate", lambda story: (True, []))
    monkeypatch.setattr(desk.sr, "_source_page_pass", lambda story: True)
    monkeypatch.setattr(desk.sr, "_cricket_service_title_pass", lambda story: True)
    assert desk._normalise_rows([football], scope="India / Asia") == []


def test_diversification_uses_discovery_profiles_and_avoids_duplicate_themes():
    events = []
    for i in range(12):
        events.append({
            "title": f"Routine India cricket selection update {i}",
            "event_identity_key": f"news-{i}",
            "discovery_profiles": ["news"],
            "news_score": 90 - i,
            "viral_score": 10,
            "social_score": 5,
            "undercovered_score": 2,
        })
    for i in range(12):
        events.append({
            "title": f"Unusual uncapped player breakthrough story {i}",
            "event_identity_key": f"emerging-{i}",
            "discovery_profiles": ["emerging"],
            "news_score": 40,
            "viral_score": 90 - i,
            "social_score": 20,
            "undercovered_score": 8,
        })
    for i in range(12):
        events.append({
            "title": f"Fans react to India cricket decision {i}",
            "event_identity_key": f"social-{i}",
            "discovery_profiles": ["social"],
            "news_score": 30,
            "viral_score": 40,
            "social_score": 95 - i,
            "undercovered_score": 7,
            "social_post_count": 2,
        })
    result = desk._diversify_events(events, limit=30)
    assert len(result) == 30
    buckets = {bucket: [x for x in result if x["discovery_bucket"] == bucket] for bucket in ("news", "viral", "social")}
    assert all(buckets.values())
    assert len({x["event_identity_key"] for x in result}) == 30
    assert all(any(profile in x["discovery_profiles"] for profile in ("news", "emerging", "social")) for x in result)


def test_score_exposes_simple_editorial_signals():
    item = _article("Uncapped bowler takes first five wicket haul", profile="emerging")
    item.update({"event_source_count": 1, "event_article_count": 1, "social_post_count": 2, "social_engagement_total": 5})
    scored = desk._score(item, [])
    assert scored["undercovered_score"] >= 7
    assert "viral_signal_score" in scored
    assert "social_signal_score" in scored


def test_collect_uses_small_bounded_profile_set(monkeypatch):
    calls = []
    monkeypatch.setattr(desk, "_google_search", lambda query, profile, scope: calls.append((query, profile, scope)) or [])
    monkeypatch.setattr(desk, "_direct_listing_source", lambda name, url: [])
    monkeypatch.setattr(desk.sr, "_rss_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_reddit_search", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_bluesky", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk.sr, "_google_trends_items", lambda *args, **kwargs: [])
    result = desk._collect("India / Asia")
    assert result == []
    assert len(calls) == 6
    assert {profile for _, profile, _ in calls} == {"news", "emerging", "social"}


def test_discover_preserves_shared_dashboard_contract(monkeypatch):
    rows = []
    for i in range(9):
        rows.append(_article(f"India cricket story {i} breakthrough", f"source{i}.example", "emerging"))
    monkeypatch.setattr(desk, "_collect", lambda scope="India / Asia": rows)
    monkeypatch.setattr(desk.sr, "_safety_gate", lambda story: (True, []))
    monkeypatch.setattr(desk.sr, "_source_page_pass", lambda story: True)
    monkeypatch.setattr(desk.sr, "_cricket_service_title_pass", lambda story: True)
    monkeypatch.setattr(desk, "cluster_news_events", lambda rows: [dict(row, event_identity_key=f"event-{i}", event_source_count=1, event_article_count=1) for i, row in enumerate(rows)])
    monkeypatch.setattr(desk.sr, "_load_uploaded_story_identities", lambda conn: set())
    result = desk.discover_sports_topics(None, scope="India / Asia", max_candidates=30)
    assert result
    assert all(item["dashboard_discovery_version"] == desk.SPORTS_DESK_VERSION for item in result)
    assert all(item["cricket_pipeline"] is True for item in result)
    assert all(item["primary_genre"] == "cricket" for item in result)


def test_niche_sports_contract(monkeypatch):
    row = _article("India football breakthrough reaches final", "sports.example", "emerging")
    monkeypatch.setattr(desk, "_collect", lambda scope="India / Asia": [row])
    monkeypatch.setattr(desk.sr, "_safety_gate", lambda story: (True, []))
    monkeypatch.setattr(desk.sr, "_source_page_pass", lambda story: True)
    monkeypatch.setattr(desk.sr, "_discovery_source_pass", lambda story: True)
    monkeypatch.setattr(desk, "cluster_news_events", lambda rows: [dict(rows[0], event_identity_key="niche-1", event_source_count=1, event_article_count=1)])
    monkeypatch.setattr(desk.sr, "_load_uploaded_story_identities", lambda conn: set())
    result = desk.discover_sports_topics(None, scope="Niche Sports", max_candidates=10)
    assert result
    assert result[0]["recommended_category"] == "sports"
    assert result[0]["cricket_pipeline"] is False


def test_reddit_search_is_keyword_search_not_subreddit_feed(monkeypatch):
    captured = {}
    class Response:
        status_code = 200
        def json(self):
            return {"data": {"children": []}}
    def fake_get(url, **kwargs):
        captured.update(kwargs.get("params") or {})
        return Response()
    monkeypatch.setattr(desk.requests, "get", fake_get)
    desk._reddit_search("Cricket", "India cricket")
    assert captured["q"] == "India cricket"
    assert captured["subreddit"] == "Cricket"


def test_direct_source_parser_accepts_recent_story(monkeypatch):
    class Response:
        status_code = 200
        text = "<article><a href='/story/india-record'>India women complete record win</a><time datetime='2026-09-23T22:00:00Z'></time></article>"
    monkeypatch.setattr(desk.requests, "get", lambda *args, **kwargs: Response())
    rows = desk._direct_listing_source("ESPNcricinfo", "https://www.espncricinfo.com/cricket-news")
    assert len(rows) == 1
    assert rows[0]["source"] == "ESPNcricinfo"
