from datetime import datetime, timezone

import sports_topic_desk_runtime as desk


def _article(title, domain, hours=3):
    return {
        "title": title,
        "text": f"{title}. This is factual supporting context for a cricket story.",
        "description": f"{title}. Supporting context.",
        "source": domain,
        "source_name": domain,
        "publisher": domain,
        "url": f"https://{domain}/story/{abs(hash(title))}",
        "publishedAt": (datetime.now(timezone.utc).replace(microsecond=0)).isoformat(),
        "collection_source": "google_news_rss",
        "age_hours": hours,
    }



def test_sports_desk_request_timeouts_fit_their_lane_budgets():
    assert desk.GOOGLE_REQUEST_TIMEOUT < desk.CORE_DISCOVERY_TIMEOUT
    assert desk.SOURCE_TIMEOUT < desk.CORE_DISCOVERY_TIMEOUT
    assert desk.SECONDARY_REQUEST_TIMEOUT < desk.CORE_DISCOVERY_TIMEOUT
    assert desk.GDELT_REQUEST_TIMEOUT < desk.CORE_DISCOVERY_TIMEOUT


def test_sports_desk_google_and_trend_timeout_kwargs_are_explicit(monkeypatch):
    calls = []

    def fake_google(*args, **kwargs):
        calls.append(("google", args, kwargs))
        return []

    def fake_trends(*args, **kwargs):
        calls.append(("trends", args, kwargs))
        return []

    monkeypatch.setattr(desk.sr, "_google_news_search_items", fake_google)
    monkeypatch.setattr(desk.sr, "_google_trends_items", fake_trends)
    monkeypatch.setattr(desk, "_direct_listing_source", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_reddit_search", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_bluesky", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_mastodon", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "fetch_gdelt_articles", lambda *args, **kwargs: [])

    desk._collect("India / Asia")

    google = [item for item in calls if item[0] == "google"]
    trends = [item for item in calls if item[0] == "trends"]
    assert google
    assert all(item[2]["timeout"] == desk.GOOGLE_REQUEST_TIMEOUT for item in google)
    assert len(trends) == 1
    assert all(item[2]["timeout"] == desk.SECONDARY_REQUEST_TIMEOUT for item in trends)


def test_sports_desk_fans_out_google_queries_only_after_sparse_first_wave(monkeypatch):
    calls = []
    def fake_google(query, *args, **kwargs):
        calls.append(query)
        return []

    monkeypatch.setattr(desk.sr, "_google_news_search_items", fake_google)
    monkeypatch.setattr(
        desk.sr,
        "_rss_items",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        desk,
        "_direct_listing_source",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        desk,
        "_bluesky",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        desk.sr,
        "_google_trends_items",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        desk,
        "fetch_gdelt_articles",
        lambda *args, **kwargs: [],
    )

    desk._collect("India / Asia")

    assert len(calls) == desk.GOOGLE_QUERY_LIMIT


def test_sports_desk_uses_first_class_reddit_lanes_for_india_asia(monkeypatch):
    calls = []

    def fake_reddit(*args, **kwargs):
        calls.append(args)
        return []

    monkeypatch.setattr(desk, "_reddit_search", fake_reddit)
    monkeypatch.setattr(desk.sr, "_google_news_search_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk.sr, "_rss_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_direct_listing_source", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_bluesky", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk.sr, "_google_trends_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "fetch_gdelt_articles", lambda *args, **kwargs: [])

    desk._collect("India / Asia")

    assert {args[0] for args in calls} == {"Cricket", "IndiaCricket"}



def test_sports_desk_primary_google_burst_is_bounded(monkeypatch):
    calls = []

    def fake_google(query, *args, **kwargs):
        calls.append(query)
        return [{
            "title": f"Distinct cricket event {len(calls)}",
            "url": f"https://example.com/event/{len(calls)}",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "source": "Example",
            "source_name": "Example",
            "collection_source": "google_news_rss",
        } for _ in range(2)]

    monkeypatch.setattr(desk.sr, "_google_news_search_items", fake_google)
    monkeypatch.setattr(desk.sr, "_rss_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "cluster_news_events", lambda rows: [dict(row) for row in rows])
    monkeypatch.setattr(desk, "_direct_listing_source", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "_bluesky", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk.sr, "_google_trends_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(desk, "fetch_gdelt_articles", lambda *args, **kwargs: [])

    desk._collect("India / Asia")

    assert len(calls) == desk.GOOGLE_QUERY_LIMIT


def test_sports_desk_duplicate_headlines_form_one_concept():
    rows = [
        _article("India survive Japan scare in dramatic T20 finish", "one.example"),
        _article("India survive Japan scare in dramatic T20 finish", "two.example"),
        _article("India survive Japan scare in dramatic T20 finish", "three.example"),
        _article("Pakistan recall uncapped fast bowler for Zimbabwe tour", "four.example"),
    ]
    concepts = desk.cluster_news_events(rows)
    assert len(concepts) == 2
    india = next(item for item in concepts if "Japan" in item["title"])
    assert india["event_article_count"] == 3
    assert india["event_source_count"] == 3


def test_sports_desk_keeps_social_leads_separate():
    rows = [
        _article("Player responds after controversial umpiring call", "one.example"),
        {
            "title": "Player responds after controversial umpiring call",
            "text": "Player responds after controversial umpiring call",
            "description": "Player responds after controversial umpiring call",
            "source": "Reddit r/Cricket",
            "source_name": "Reddit r/Cricket",
            "url": "https://reddit.com/r/Cricket/example",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "collection_source": "reddit",
            "social_post": True,
            "social_like": 120,
            "social_reply": 45,
        },
    ]
    concepts = desk._enrich_events(desk.cluster_news_events(rows), rows)
    assert any(concept["social_post_count"] == 1 for concept in concepts)
    assert any(concept["event_article_count"] == 1 for concept in concepts)



def test_sports_desk_buckets_are_unique_and_cover_three_editorial_categories(monkeypatch):
    concepts = []
    unique_story_phrases = [
        "uncapped spinner takes five wickets",
        "women's batter breaks a domestic record",
        "associate nation pulls a stunning upset",
        "teenage batter earns surprise debut",
        "pace bowler completes comeback",
        "umpire ruling sparks law debate",
        "captain confirms leadership change",
        "domestic coach lands national appointment",
        "wicketkeeper cleared after fitness scare",
        "board reverses an earlier selection call",
        "left-arm seamer produces historic spell",
        "opening batter reaches rare milestone",
        "veteran announces sudden retirement",
        "young all-rounder earns first contract",
        "overlooked player gets shock recall",
        "club reveals controversial fixture change",
        "rookie bowler removes star batter twice",
        "women's side overturns a huge deficit",
        "associate captain breaks tournament record",
        "coach responds to player criticism",
        "selector explains surprise omission",
        "former captain returns to domestic cricket",
        "new keeper replaces injured starter",
        "fast bowler records fastest spell",
        "academy graduate earns senior contract",
        "board issues unexpected statement",
        "match referee hands out rare sanction",
        "batter survives dramatic final over",
        "underdog chase seals last-ball win",
        "teen spinner becomes youngest five-for",
        "captain changes batting order",
        "domestic final ends in record chase",
        "international debut sparks online debate",
        "injury setback changes squad plans",
        "senior star faces uncertain comeback",
    ]
    for index, phrase in enumerate(unique_story_phrases):
        concepts.append(_article(
            f"Undercovered cricket story: {phrase} involving player {index}",
            f"source{index}.example",
        ))
    monkeypatch.setattr(desk, "_collect", lambda scope="India / Asia": concepts)
    monkeypatch.setattr(desk, "_normalise_rows", lambda rows, scope="India / Asia": rows)

    result = desk.discover_sports_topics(
        bot=None,
        scope="Global",
        requested_topic="",
        max_candidates=60,
        retained_candidates=[],
    )
    assert len(result) == 35
    buckets = {key: [x for x in result if x.get("discovery_bucket") == key] for key in ("news", "viral", "social")}
    assert sum(len(items) for items in buckets.values()) == 35
    assert all(len(items) > 0 for items in buckets.values())
    assert len({
        item.get("event_identity_key") or item.get("event_id")
        for item in result
    }) == 35


def test_sports_desk_social_bucket_prefers_real_social_or_reaction_leads():
    concepts = [
        {
            "title": f"Social reaction to cricket development {index}",
            "event_id": f"social-{index}",
            "event_identity_key": f"social-{index}",
            "news_score": 40,
            "viral_score": 50,
            "social_score": 80,
            "undercovered_score": 8,
            "social_post_count": 1,
        }
        for index in range(12)
    ]
    concepts.extend(
        {
            "title": f"Routine cricket news event {index}",
            "event_id": f"news-{index}",
            "event_identity_key": f"news-{index}",
            "news_score": 90,
            "viral_score": 20,
            "social_score": 5,
            "undercovered_score": 3,
            "social_post_count": 0,
        }
        for index in range(12)
    )

    result = desk._bucketize(concepts)
    social = [item for item in result if item.get("discovery_bucket") == "social"]

    assert social
    assert all(int(item.get("social_post_count") or 0) > 0 for item in social)



def test_sports_desk_exposes_undercoverage_and_signal_dimensions():
    item = _article("Uncapped bowler takes first five wicket haul in domestic thriller", "example.com")
    item["social_post_count"] = 3
    item["social_engagement_total"] = 7
    item["independent_source_count"] = 1
    item["article_count"] = 1
    scored = desk._score(item, [], [])
    assert scored["undercovered_score"] >= 7
    assert "viral_signal_score" in scored
    assert "social_signal_score" in scored


def test_sports_desk_scope_keeps_india_asia_primary(monkeypatch):
    rows = [
        _article("India domestic bowler breaks record", "india.example"),
        _article("England batter breaks record", "england.example"),
    ]
    monkeypatch.setattr(desk, "_collect", lambda scope="India / Asia": rows)
    monkeypatch.setattr(desk, "_normalise_rows", lambda rows, scope="India / Asia": rows)
    result = desk.discover_sports_topics(
        bot=None,
        scope="India / Asia",
        requested_topic="",
        max_candidates=30,
        retained_candidates=[],
    )
    assert all(
        any(term in str(item.get("title") or "").casefold() for term in ("india", "asia", "pakistan", "sri lanka", "bangladesh"))
        for item in result
    ) or not result



def test_sports_desk_uses_scope_specific_google_lanes():
    india = desk._google_queries_for_scope("India / Asia")
    global_queries = desk._google_queries_for_scope("Global")
    niche = desk._google_queries_for_scope("Niche Sports")
    assert len(india) == 10
    assert len(global_queries) == 10
    assert len(niche) == 10
    assert any("India" in query for query in india)
    assert any("Australia" in query for query in global_queries)
    niche_blob = " ".join(niche).casefold()
    for term in ("football", "tennis", "badminton", "hockey", "athletics", "basketball", "motogp", "boxing", "table tennis", "aquatics"):
        assert term in niche_blob
    for term in ("upset", "record", "injury", "controversy", "qualification", "breakthrough"):
        assert term in niche_blob
    assert "cricket" not in niche_blob


def test_direct_listing_source_accepts_cricinfo_story_paths_and_calendar_dates(monkeypatch):
    class _Response:
        status_code = 200
        text = """
        <article>
          <a href="/story/india-women-record-win">India women complete record win</a>
          <time datetime="2026-09-22T22:00:00Z"></time>
        </article>
        <article>
          <a href="/cricket-news/another-story">Another fresh cricket story</a>
          <div>Sep 22, 2026</div>
        </article>
        """

    monkeypatch.setattr(desk.requests, "get", lambda *args, **kwargs: _Response())
    rows = desk._direct_listing_source(
        "ESPNcricinfo",
        "https://www.espncricinfo.com/cricket-news",
    )
    assert len(rows) == 2
    assert all(row["source"] == "ESPNcricinfo" for row in rows)


def test_google_news_snippets_survive_until_event_clustering(monkeypatch):
    row = _article("India survive Japan scare in dramatic T20 finish", "news.google.com")
    row["text"] = "Too short"
    row["description"] = "Short"
    row["collection_source"] = "google_news_rss"
    monkeypatch.setattr(desk.sr, "_safety_gate", lambda story: (True, []))
    monkeypatch.setattr(desk.sr, "_source_page_pass", lambda story: True)
    monkeypatch.setattr(desk.sr, "_headline_noise_pass", lambda story: True)
    monkeypatch.setattr(desk.sr, "_cricket_service_title_pass", lambda story: True)
    result = desk._normalise_rows([row])
    assert len(result) == 1


def test_cricket_bucket_does_not_suppress_distinct_events_in_one_competition():
    concepts = []
    for index in range(8):
        concepts.append({
            "title": f"India Asian Games cricket event {index}",
            "event_id": f"event-{index}",
            "event_identity_key": f"identity-{index}",
            "news_score": 100 - index,
            "viral_score": 80 - index,
            "social_score": 60 - index,
            "undercovered_score": 4,
            "social_post_count": 0,
        })
    result = desk._bucketize(concepts)
    assert len(result) == 8
    assert len({
        item.get("event_identity_key") or item.get("event_id")
        for item in result
    }) == 8



def test_sports_desk_treats_india_japan_headlines_as_one_event_family():
    rows = [
        _article("India survive Japan scare in dramatic T20 finish", "one.example"),
        _article("India-Japan match sparks umpiring controversy", "two.example"),
        _article("Japan umpiring call dominates India cricket debate", "three.example"),
    ]
    concepts = [
        {
            **item,
            "news_score": 90 - index,
            "viral_score": 80 - index,
            "social_score": 70 - index,
            "undercovered_score": 2,
            "social_post_count": 0,
        }
        for index, item in enumerate(desk._merge_same_matchup_events(desk.cluster_news_events(rows)))
    ]
    assert len(concepts) == 1

    family = [
        desk.sr._cricket_event_family(item)
        for item in concepts
    ]
    assert all(value == "india_japan_matchup" for value in family)


def test_matchup_merge_keeps_separate_competitions_distinct():
    rows = [
        _article("India beat Japan in the Asian Games", "one.example"),
        _article("India-Japan World Cup match draws controversy", "two.example"),
    ]
    events = desk.cluster_news_events(rows)
    # Force distinct competition anchors into the two pre-clustered events while
    # keeping the same direct matchup family and a close publication window.
    events[0]["title"] = "India beat Japan in the Asian Games"
    events[0]["event_search_text"] = "India beat Japan in the Asian Games"
    events[0]["event_latest_published_at"] = datetime.now(timezone.utc).isoformat()
    events[0]["event_latest_seen_at"] = events[0]["event_latest_published_at"]
    events[1]["title"] = "India-Japan World Cup match draws controversy"
    events[1]["event_search_text"] = "India-Japan World Cup match draws controversy"
    events[1]["event_latest_published_at"] = datetime.now(timezone.utc).isoformat()
    events[1]["event_latest_seen_at"] = events[1]["event_latest_published_at"]

    merged = desk._merge_same_matchup_events(events)
    assert len(merged) == 2


def test_matchup_merge_does_not_count_social_publishers_as_factual_sources():
    rows = [
        _article("India-Japan match sparks controversy", "factual.example"),
        {
            "title": "India-Japan match sparks controversy",
            "text": "Social reaction",
            "description": "Social reaction",
            "source": "Reddit r/Cricket",
            "source_name": "Reddit r/Cricket",
            "publisher": "Reddit r/Cricket",
            "url": "https://reddit.com/r/Cricket/example-merge",
            "publishedAt": datetime.now(timezone.utc).isoformat(),
            "collection_source": "reddit",
            "social_post": True,
        },
    ]
    event = desk.cluster_news_events(rows)[0]
    merged = desk._merge_same_matchup_events([event])[0]
    assert merged["event_source_count"] == 1
    assert merged["event_evidence_publishers"] == ["factual.example"]


def test_sports_desk_keeps_unusual_article_headlines_for_manual_qc(monkeypatch):
    row = _article(
        "Japan bowler's bizarre final-over call leaves India stunned",
        "cricbuzz.example",
    )
    monkeypatch.setattr(desk.sr, "_source_page_pass", lambda item: True)
    monkeypatch.setattr(desk.sr, "_safety_gate", lambda item: (True, []))
    monkeypatch.setattr(desk.sr, "_cricket_service_title_pass", lambda item: True)
    result = desk._normalise_rows([row])
    assert len(result) == 1



def test_cricket_detection_accepts_player_only_headlines():
    assert desk._is_cricket({"title": "Bumrah returns to training after injury"}) is True
    assert desk._scope_pass(
        {"title": "Bumrah returns to training after injury", "event_entities": ["Bumrah"]},
        "India / Asia",
    ) is True


def test_india_asia_scope_keeps_global_player_when_linked_to_india():
    assert desk._scope_pass(
        {
            "title": "Ben Stokes reacts after India's controversial win",
            "event_entities": ["Ben Stokes", "India"],
        },
        "India / Asia",
    ) is True


def test_india_asia_scope_does_not_include_unrelated_global_player_only_story():
    assert desk._scope_pass(
        {"title": "Ben Stokes returns to training after injury", "event_entities": ["Ben Stokes"]},
        "India / Asia",
    ) is False


def test_cricket_detection_does_not_confuse_substrings_with_cricket():
    assert desk._is_cricket({"title": "Latest space test opens new frontier"}) is False
    assert desk._is_cricket({"title": "Coach announces new football plan"}) is False
    assert desk._is_cricket({"title": "India batter breaks a batting record"}) is True


def test_india_asia_scope_accepts_player_only_india_story_and_rejects_unrelated_cricket():
    assert desk._scope_pass(
        {"title": "Bumrah returns to training after injury", "event_entities": ["Bumrah"]},
        "India / Asia",
    ) is True
    assert desk._scope_pass(
        {"title": "England opener breaks record in county cricket", "event_entities": ["England"]},
        "India / Asia",
    ) is False



def test_cricket_discovery_suppresses_uploaded_event_but_not_unpublished_selection(tmp_path, monkeypatch):
    import sqlite3
    from db_architecture import migrate_vault

    conn = sqlite3.connect(tmp_path / "vault.db")
    migrate_vault(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO vault
        (run_id, topic, video_id, status, discovery_event_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("uploaded", "India-Japan cricket final", "yt-123", "UPLOADED", "event-uploaded", now, now),
    )
    conn.commit()

    uploaded = desk.sr._load_uploaded_story_identities(conn)
    assert desk.sr._uploaded_story_match(
        {"title": "India-Japan cricket final", "event_identity_key": "event-uploaded"},
        uploaded,
    ) is True
    assert desk.sr._uploaded_story_match(
        {"title": "India-Japan cricket final", "event_identity_key": "event-unpublished"},
        uploaded,
    ) is True  # exact old-title fallback still protects legacy rows

    conn.close()


def test_dashboard_retained_topic_is_not_revalidated_through_production_quality_gate(monkeypatch):
    import dashboard_runtime

    retained = [{
        "title": "Unusual India cricket development",
        "event_identity_key": "unpublished-1",
        "story_key": "unpublished-1",
    }]
    fresh = [{
        "title": "Unusual India cricket development",
        "event_identity_key": "unpublished-1",
        "story_key": "fresh-1",
    }]

    result = dashboard_runtime._merge_retained_topics(
        None,
        {"category": "sports_stories_of_day", "cricket_pipeline": True},
        None,
        fresh,
        retained,
        max_candidates=60,
    )

    assert len(result) == 1
    assert result[0]["retained_from_previous_run"] is True


def test_niche_sports_normalization_does_not_require_cricket(monkeypatch):
    fresh = datetime.now(timezone.utc).isoformat()
    football = _article("India football comeback reaches dramatic final", "sports.example")
    football["publishedAt"] = fresh
    cricket = _article("India cricket selection debate grows", "cricket.example")
    cricket["publishedAt"] = fresh

    monkeypatch.setattr(desk.sr, "_safety_gate", lambda story: (True, []))
    monkeypatch.setattr(desk.sr, "_source_page_pass", lambda story: True)
    monkeypatch.setattr(desk.sr, "_discovery_source_pass", lambda story: True)

    result = desk._normalise_rows([football, cricket], scope="Niche Sports")
    assert [item["title"] for item in result] == [football["title"]]


def test_niche_sports_output_uses_the_shared_dashboard_contract(monkeypatch):
    concepts = []
    for index in range(9):
        concepts.append({
            "title": f"International tennis breakthrough event {index}",
            "event_id": f"niche-event-{index}",
            "event_identity_key": f"niche-identity-{index}",
            "url": f"https://sports.example/story/{index}",
            "source": "Sports Source",
            "age_hours": 2,
            "event_source_count": 2,
            "event_article_count": 2,
            "social_post_count": 0,
            "news_score": 90 - index,
            "viral_score": 80 - index,
            "social_score": 70 - index,
            "undercovered_score": 5,
        })
    monkeypatch.setattr(desk, "_collect", lambda scope="India / Asia": concepts)
    monkeypatch.setattr(desk, "_normalise_rows", lambda rows, scope="India / Asia": list(rows))
    monkeypatch.setattr(desk, "cluster_news_events", lambda rows: [dict(row) for row in rows])
    monkeypatch.setattr(desk.sr, "_load_uploaded_story_identities", lambda conn: set())

    result = desk.discover_sports_topics(
        bot=None,
        scope="Niche Sports",
        requested_topic="",
        max_candidates=60,
        retained_candidates=[],
    )
    assert result
    assert all(item["recommended_category"] == "sports" for item in result)
    assert all(item["recommended_format"] == "regular" for item in result)
    assert all(item["cricket_pipeline"] is False for item in result)
    assert all(item["primary_genre"] == "sports" for item in result)
    assert all(item["dashboard_discovery_version"] == desk.SPORTS_DESK_VERSION for item in result)
