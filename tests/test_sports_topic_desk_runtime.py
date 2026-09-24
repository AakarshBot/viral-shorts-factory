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
    assert all(1 <= len(queries) <= 3 for queries in profiles.values())
    assert any("selection" in query for query in profiles["news"])
    assert any("breakthrough" in query or "unusual" in query for query in profiles["emerging"])
    assert any("reaction" in query or "fans" in query for query in profiles["social"])



def test_global_and_niche_profiles_have_real_breadth():
    global_profiles = desk._profile_queries("Global")
    niche_profiles = desk._profile_queries("Niche Sports")

    assert all(len(queries) == 3 for queries in global_profiles.values())
    assert all(len(queries) == 3 for queries in niche_profiles.values())

    global_text = " ".join(" ".join(values) for values in global_profiles.values()).casefold()
    niche_text = " ".join(" ".join(values) for values in niche_profiles.values()).casefold()

    assert "pakistan" in global_text
    assert "afghanistan" in global_text
    assert "women cricket" in global_text
    assert "uncapped" in global_text or "emerging" in global_text
    assert "football" in niche_text
    assert "badminton" in niche_text
    assert "athletics" in niche_text
    assert "motorsport" in niche_text
    assert "chess" in niche_text


def test_global_collect_does_not_use_bcci_listing(monkeypatch):
    google_calls = []
    direct_calls = []

    monkeypatch.setattr(
        desk,
        "_google_search",
        lambda query, profile, scope: google_calls.append((query, profile, scope)) or [],
    )
    monkeypatch.setattr(
        desk,
        "_direct_listing_source",
        lambda name, url: direct_calls.append((name, url)) or [],
    )
    monkeypatch.setattr(desk.sr, "_rss_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_bluesky", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk.sr, "_google_trends_items", lambda *args, **kwargs: [])

    desk._collect("Global")

    assert len(google_calls) == 9
    assert {profile for _, profile, _ in google_calls} == {"news", "emerging", "social"}
    assert "BCCI" not in {name for name, _ in direct_calls}
    assert "ICC" in {name for name, _ in direct_calls}
    assert "ESPNcricinfo" in {name for name, _ in direct_calls}
    assert "Wisden" in {name for name, _ in direct_calls}


def test_niche_sports_collect_uses_all_three_profiles(monkeypatch):
    calls = []

    monkeypatch.setattr(
        desk,
        "_google_search",
        lambda query, profile, scope: calls.append((query, profile, scope)) or [],
    )
    monkeypatch.setattr(desk.sr, "_rss_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_bluesky", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk.sr, "_google_trends_items", lambda *args, **kwargs: [])

    assert desk._collect("Niche Sports") == []
    assert len(calls) == 9
    assert {profile for _, profile, _ in calls} == {"news", "emerging", "social"}


def test_enrich_events_preserves_multiple_discovery_profiles():
    shared_url = "https://example.com/shared"
    rows = [
        _article("India cricket player comment", "news.example", "news"),
        _article("Fans react to India cricket player", "social.example", "social"),
    ]
    for row in rows:
        row["url"] = shared_url
    event = {
        "event_evidence": rows,
        "event_source_count": 2,
        "event_article_count": 2,
    }

    enriched = desk._enrich_events([event], rows)

    assert set(enriched[0]["discovery_profiles"]) >= {"news", "social"}



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


def test_normalise_rows_keeps_cricket_query_matches_without_cricket_in_headline(monkeypatch):
    row = _article("BCCI names surprise squad change", "sports.example", "news")
    row["collection_source"] = "google_news_rss"
    row["discovery_query"] = "India cricket latest selection"
    monkeypatch.setattr(desk.sr, "_safety_gate", lambda story: (True, []))
    monkeypatch.setattr(desk.sr, "_source_page_pass", lambda story: True)
    monkeypatch.setattr(desk.sr, "_cricket_service_title_pass", lambda story: True)

    result = desk._normalise_rows([row], scope="India / Asia")

    assert len(result) == 1


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



def test_bucket_uses_content_signals_even_when_profile_is_news():
    events = [
        {
            "title": "Fans react after shocking player statement",
            "event_identity_key": "social-signal",
            "discovery_profiles": ["news"],
            "news_score": 50,
            "viral_score": 25,
            "social_score": 80,
            "social_post_count": 3,
            "undercovered_score": 8,
        },
        {
            "title": "Uncapped player makes bizarre breakthrough",
            "event_identity_key": "viral-signal",
            "discovery_profiles": ["news"],
            "news_score": 45,
            "viral_score": 80,
            "social_score": 20,
            "undercovered_score": 8,
        },
        {
            "title": "Team announces squad change",
            "event_identity_key": "news-signal",
            "discovery_profiles": ["news"],
            "news_score": 75,
            "viral_score": 15,
            "social_score": 10,
            "undercovered_score": 2,
        },
    ]

    result = desk._diversify_events(events, limit=3)
    buckets = {item["event_identity_key"]: item["discovery_bucket"] for item in result}

    assert buckets["social-signal"] == "news"
    assert buckets["viral-signal"] == "news"
    assert buckets["news-signal"] == "news"



def test_overlapping_news_and_emerging_profile_stays_news():
    events = [
        {
            "title": "Current cricket development",
            "event_identity_key": "news-emerging-overlap",
            "discovery_profiles": ["news", "emerging"],
            "news_score": 80,
            "viral_score": 90,
            "social_score": 5,
        },
        {
            "title": "Emerging-only cricket breakthrough",
            "event_identity_key": "emerging-only",
            "discovery_profiles": ["emerging"],
            "news_score": 20,
            "viral_score": 80,
            "social_score": 5,
        },
    ]

    result = desk._diversify_events(events, limit=2)
    buckets = {item["event_identity_key"]: item["discovery_bucket"] for item in result}

    assert buckets["news-emerging-overlap"] == "news"
    assert buckets["emerging-only"] == "viral"


def test_discovery_buckets_follow_profile_lane():
    events = [
        {
            "title": "Confirmed cricket selection update",
            "event_identity_key": "news-first",
            "discovery_profiles": ["news"],
            "news_score": 80,
            "viral_score": 10,
            "social_score": 5,
        },
        {
            "title": "Uncapped player makes breakthrough debut",
            "event_identity_key": "emerging-first",
            "discovery_profiles": ["emerging"],
            "news_score": 20,
            "viral_score": 80,
            "social_score": 5,
        },
        {
            "title": "Fans debate surprise selection",
            "event_identity_key": "social-first",
            "discovery_profiles": ["social"],
            "news_score": 10,
            "viral_score": 30,
            "social_score": 80,
        },
    ]

    result = desk._diversify_events(events, limit=3)
    buckets = {item["event_identity_key"]: item["discovery_bucket"] for item in result}

    assert buckets == {
        "news-first": "news",
        "emerging-first": "viral",
        "social-first": "social",
    }


def test_confirmed_event_with_social_reactions_stays_out_of_social_bucket():
    events = [
        {
            "title": "Confirmed cricket report draws heavy fan reaction",
            "event_identity_key": "confirmed-with-social",
            "discovery_profiles": ["news", "social"],
            "news_score": 75,
            "viral_score": 35,
            "social_score": 95,
            "event_article_count": 1,
            "social_post_count": 10,
            "undercovered_score": 5,
        },
        {
            "title": "Social-first fan reaction to surprise selection",
            "event_identity_key": "social-only",
            "discovery_profiles": ["social"],
            "news_score": 20,
            "viral_score": 55,
            "social_score": 90,
            "event_article_count": 0,
            "social_post_count": 4,
            "undercovered_score": 8,
        },
    ]

    result = desk._diversify_events(events, limit=2)
    buckets = {item["event_identity_key"]: item["discovery_bucket"] for item in result}

    assert buckets["confirmed-with-social"] == "news"
    assert buckets["social-only"] == "social"


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
    assert len(calls) == 9
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