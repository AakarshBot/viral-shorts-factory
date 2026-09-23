from datetime import datetime, timezone

import dashboard_topic_discovery_runtime as discovery


def _fresh_story(title, **extra):
    story = {
        "title": title,
        "description": "A current article with enough factual context for dashboard review.",
        "url": "https://example.com/story/" + str(abs(hash(title))),
        "source": "Example News",
        "publishedAt": datetime.now(timezone.utc).isoformat(),
        "genre": "sports_stories_of_day",
    }
    story.update(extra)
    return story


def test_india_asia_query_lanes_cover_multiple_cricket_editorial_angles():
    queries = discovery._query_lanes(
        "sports_stories_of_day",
        {},
        cricket_scope="India / Asia",
    )

    assert len(queries) == discovery.GOOGLE_QUERY_LIMIT
    assert len({query.casefold() for query in queries}) == len(queries)
    joined = " ".join(queries).casefold()
    assert "virat kohli" in joined
    assert "pakistan" in joined
    assert "india women" in joined
    assert "ranji" in joined
    assert "japan" in joined
    assert "umpire" in joined


def test_hard_dashboard_gate_is_not_a_production_quality_gate(monkeypatch):
    story = _fresh_story(
        "India cricket selection update after squad changes",
        event_source_count=1,
        event_article_count=1,
    )

    monkeypatch.setattr(discovery.sr, "_source_page_pass", lambda item: True)
    monkeypatch.setattr(discovery.sr, "_headline_noise_pass", lambda item: True)
    monkeypatch.setattr(discovery.sr, "_discovery_source_pass", lambda item: True)
    monkeypatch.setattr(discovery.sr, "_cricket_relevance_pass", lambda item, genre: True)

    assert discovery._hard_dashboard_pass(
        story,
        "sports_stories_of_day",
        "",
    ) is True


def test_cricket_factual_lane_keeps_valid_headline_without_cricket_keyword():
    story = _fresh_story(
        "Bumrah returns with a match-winning spell",
        event_source_count=2,
        event_article_count=2,
        collection_source="google_news_rss",
    )

    assert discovery._hard_dashboard_pass(
        story,
        "sports_stories_of_day",
        "",
    ) is True


def test_hard_dashboard_gate_rejects_service_noise():
    story = _fresh_story(
        "India cricket live score and match timings",
        event_source_count=2,
        event_article_count=2,
    )

    assert discovery._hard_dashboard_pass(
        story,
        "sports_stories_of_day",
        "",
    ) is False
    assert story["discovery_rejection"] in {
        "Non-event/SEO headline",
        "Low-value cricket service article",
    }


def test_dashboard_discovery_keeps_multiple_distinct_events(monkeypatch):
    events = [
        _fresh_story(
            "Former batter calls India arrogant after rivalry clash",
            event_id="event-a",
            event_entities=["India", "Former batter"],
            event_actions=["comment"],
            event_source_count=2,
            event_article_count=2,
        ),
        _fresh_story(
            "India women win cricket gold at Asian Games",
            event_id="event-b",
            event_entities=["India Women", "Asian Games"],
            event_actions=["win"],
            event_source_count=3,
            event_article_count=3,
        ),
        _fresh_story(
            "India reach new milestone after dramatic T20 win",
            event_id="event-c",
            event_entities=["India", "Japan"],
            event_actions=["win"],
            event_source_count=2,
            event_article_count=2,
        ),
        _fresh_story(
            "Rashid Khan praises an Indian sensation after the match",
            event_id="event-d",
            event_entities=["Rashid Khan", "India"],
            event_actions=["comment"],
            event_source_count=2,
            event_article_count=2,
        ),
    ]

    monkeypatch.setattr(
        discovery,
        "_collect_articles",
        lambda *args, **kwargs: (events, []),
    )
    monkeypatch.setattr(
        discovery,
        "cluster_news_events",
        lambda rows: [dict(row) for row in rows],
    )

    result = discovery.discover_dashboard_topics(
        bot=type("Bot", (), {})(),
        genre_key="sports_stories_of_day",
        genre_cfg={},
        target_category="sports_stories_of_day",
        cricket_scope="India / Asia",
        max_candidates=4,
    )

    assert len(result) == 4
    assert len({item.get("event_id") for item in result}) == 4


def test_dashboard_discovery_does_not_require_cricket_worthiness_floor(monkeypatch):
    weak_but_valid = _fresh_story(
        "DDCA writes to BCCI about player investigation",
        event_id="event-investigation",
        event_entities=["DDCA", "BCCI"],
        event_actions=["investigation"],
        event_source_count=1,
        event_article_count=1,
    )

    monkeypatch.setattr(
        discovery,
        "_collect_articles",
        lambda *args, **kwargs: ([weak_but_valid], []),
    )
    monkeypatch.setattr(
        discovery,
        "cluster_news_events",
        lambda rows: [dict(row) for row in rows],
    )

    result = discovery.discover_dashboard_topics(
        bot=type("Bot", (), {})(),
        genre_key="sports_stories_of_day",
        genre_cfg={},
        target_category="sports_stories_of_day",
        cricket_scope="India / Asia",
        max_candidates=1,
    )

    assert len(result) == 1
    assert result[0]["title"] == weak_but_valid["title"]


def test_ai_topic_path_uses_dashboard_discovery_v2(monkeypatch):
    calls = []

    def fake_discovery(*args, **kwargs):
        calls.append(kwargs)
        return [{
            "title": "India cricket story",
            "url": "https://example.com/ai-story",
            "source": "Example News",
        }]

    monkeypatch.setattr(
        discovery,
        "discover_dashboard_topics",
        fake_discovery,
        raising=False,
    )

    bot = type(
        "Bot",
        (),
        {"CONTENT_CATEGORIES": {"sports": {"gnews_q": "sports"}}},
    )()

    import dashboard_runtime

    monkeypatch.setattr(
        dashboard_runtime,
        "_merge_retained_topics",
        lambda bot, config, conn, ranked, retained, max_candidates: ranked,
    )

    result = dashboard_runtime.discover_ai_topics(
        bot,
        {"category": "sports", "editorial_mode": "AI"},
        None,
        max_candidates=1,
        retained_candidates=[],
    )

    assert len(result) == 1
    assert calls
    assert calls[0]["target_category"] == "sports"


def test_dashboard_discovery_can_request_full_sixty_topic_portfolio(monkeypatch):
    discovery._DASHBOARD_DISCOVERY_CACHE.clear()
    calls = {}

    monkeypatch.setattr(
        discovery,
        "_collect_articles",
        lambda *args, **kwargs: ([], []),
    )
    monkeypatch.setattr(
        discovery,
        "cluster_news_events",
        lambda rows: [],
    )

    def fake_rank(events, **kwargs):
        calls["max_candidates"] = kwargs["max_candidates"]
        return []

    monkeypatch.setattr(discovery, "_rank_dashboard_events", fake_rank)

    discovery.discover_dashboard_topics(
        bot=type("Bot", (), {})(),
        genre_key="sports",
        genre_cfg={},
        target_category="sports",
        max_candidates=60,
    )

    assert calls["max_candidates"] == 60
    discovery._DASHBOARD_DISCOVERY_CACHE.clear()


def test_sports_hard_gate_rejects_non_sports_headline_but_keeps_niche_sport(monkeypatch):
    non_sports = _fresh_story(
        "New smartphone launch expands battery life",
        event_source_count=2,
        event_article_count=2,
        genre="sports",
    )
    niche_sport = _fresh_story(
        "India badminton pair reaches surprise BWF final",
        event_source_count=1,
        event_article_count=1,
        genre="sports",
    )

    monkeypatch.setattr(discovery.sr, "_source_page_pass", lambda item: True)
    monkeypatch.setattr(discovery.sr, "_discovery_source_pass", lambda item: True)
    assert discovery._hard_dashboard_pass(non_sports, "sports", "") is False
    assert discovery._hard_dashboard_pass(niche_sport, "sports", "") is True


def test_dashboard_ranked_topic_default_pool_is_sixty():
    import inspect
    from dashboard_runtime import discover_ranked_topics

    assert inspect.signature(discover_ranked_topics).parameters["max_candidates"].default == 60


def test_dashboard_discovery_short_cache_avoids_duplicate_provider_sweep(monkeypatch):
    discovery._DASHBOARD_DISCOVERY_CACHE.clear()
    calls = {"collect": 0}
    story = _fresh_story(
        "India cricket board announces major selection change",
        event_id="cache-event",
        event_source_count=2,
        event_article_count=2,
    )

    def fake_collect(*args, **kwargs):
        calls["collect"] += 1
        return [dict(story)], []

    monkeypatch.setattr(discovery, "_collect_articles", fake_collect)
    monkeypatch.setattr(
        discovery,
        "cluster_news_events",
        lambda rows: [dict(rows[0])],
    )
    monkeypatch.setattr(
        discovery,
        "_hard_dashboard_pass",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        discovery,
        "_rank_dashboard_events",
        lambda events, **kwargs: [dict(events[0])],
    )

    kwargs = {
        "bot": type("Bot", (), {})(),
        "genre_key": "sports_stories_of_day",
        "genre_cfg": {},
        "target_category": "sports_stories_of_day",
        "cricket_scope": "India / Asia",
        "max_candidates": 1,
    }

    first = discovery.discover_dashboard_topics(**kwargs)
    second = discovery.discover_dashboard_topics(**kwargs)

    assert calls["collect"] == 1
    assert first == second

    discovery._DASHBOARD_DISCOVERY_CACHE.clear()



def test_cricket_dashboard_does_not_reject_weird_but_source_backed_headlines(monkeypatch):
    story = _fresh_story(
        "Japan bowler's bizarre final-over call leaves India stunned",
        event_source_count=1,
        event_article_count=1,
        collection_source="google_news_rss",
    )
    monkeypatch.setattr(discovery.sr, "_source_page_pass", lambda item: True)
    monkeypatch.setattr(discovery.sr, "_cricket_service_title_pass", lambda item: True)
    monkeypatch.setattr(discovery.sr, "_headline_noise_pass", lambda item: False)
    monkeypatch.setattr(discovery.sr, "_discovery_source_pass", lambda item: True)
    assert discovery._hard_dashboard_pass(
        story,
        "sports_stories_of_day",
        "",
    ) is True



def test_sports_dashboard_uses_ten_independent_editorial_query_lanes():
    queries = discovery._query_lanes("sports", {"gnews_q": "sports"})
    assert len(queries) == 10
    joined = " ".join(queries).casefold()
    for term in ("football", "tennis", "badminton", "hockey", "athletics", "boxing", "motorsport", "women", "olympics"):
        assert term in joined


def test_dashboard_collector_does_not_call_reddit_by_default(monkeypatch):
    calls = {"reddit": 0, "google": 0}

    def fake_google(*args, **kwargs):
        calls["google"] += 1
        return []

    def fake_reddit(*args, **kwargs):
        calls["reddit"] += 1
        return []

    monkeypatch.setattr(discovery.sr, "_google_news_search_items", fake_google)
    monkeypatch.setattr(discovery.sr, "_reddit_items", fake_reddit)
    monkeypatch.setattr(discovery.sr, "_google_trends_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(discovery, "fetch_gdelt_articles", lambda *args, **kwargs: [])

    discovery._collect_articles(
        type("Bot", (), {})(),
        "sports",
        {"rss_url": ""},
    )

    assert calls["google"] == 10
    assert calls["reddit"] == 0


def test_dashboard_uploaded_event_is_removed_but_unpublished_retained_is_not(tmp_path):
    import sqlite3
    from db_architecture import migrate_vault

    conn = sqlite3.connect(tmp_path / "vault.db")
    migrate_vault(conn)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO vault
        (run_id, topic, video_id, status, discovery_event_key, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("uploaded", "Uploaded event", "yt-999", "UPLOADED", "event-999", now, now),
    )
    conn.commit()

    uploaded = discovery.sr._load_uploaded_story_identities(conn)
    assert discovery.sr._uploaded_story_match(
        {"title": "Uploaded event", "event_identity_key": "event-999"},
        uploaded,
    ) is True
    assert discovery.sr._uploaded_story_match(
        {"title": "Unpublished event", "event_identity_key": "event-123"},
        uploaded,
    ) is False
    conn.close()
