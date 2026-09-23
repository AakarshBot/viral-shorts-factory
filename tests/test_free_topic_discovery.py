import story_ranker


def test_google_trends_rss_parser_keeps_linked_news_and_trend_strength(monkeypatch):
    xml = b"""<?xml version="1.0"?>
    <rss xmlns:ht="https://trends.google.com/trending/rss">
      <channel>
        <item>
          <title>example trend</title>
          <ht:approx_traffic>200K+</ht:approx_traffic>
          <pubDate>Sun, 20 Sep 2026 10:00:00 GMT</pubDate>
          <ht:news_item>
            <ht:news_item_title>Example trend becomes a major story</ht:news_item_title>
            <ht:news_item_snippet>A useful news summary.</ht:news_item_snippet>
            <ht:news_item_url>https://example.com/story</ht:news_item_url>
            <ht:news_item_source>Example News</ht:news_item_source>
          </ht:news_item>
        </item>
      </channel>
    </rss>"""

    class Response:
        content = xml

        def raise_for_status(self):
            return None

    monkeypatch.setattr(story_ranker.requests, "get", lambda *args, **kwargs: Response())
    rows = story_ranker._google_trends_items("IN", max_items=10)

    assert len(rows) == 1
    assert rows[0]["trend_query"] == "example trend"
    assert rows[0]["trend_bonus"] == 3.5
    assert rows[0]["url"] == "https://example.com/story"
    assert rows[0]["collection_source"] == "google_trends"


def test_google_news_search_uses_public_rss_not_gnews(monkeypatch):
    captured = {}

    def fake_rss(url, genre_key, collection_source="rss", max_items=60, timeout=8.0):
        captured.update(
            url=url,
            genre_key=genre_key,
            collection_source=collection_source,
            max_items=max_items,
        )
        return []

    monkeypatch.setattr(story_ranker, "_rss_items", fake_rss)
    story_ranker._google_news_search_items("India breaking news", "national_global_affairs", 40)

    assert captured["url"].startswith("https://news.google.com/rss/search?")
    assert "api.gnews.io" not in captured["url"]
    assert captured["collection_source"] == "google_news_rss"
    assert captured["max_items"] == 40


def test_topic_discovery_has_no_removed_paid_or_library_discovery_path():
    source = open(story_ranker.__file__, "r", encoding="utf-8").read()
    assert "api.gnews.io" not in source
    assert "GNEWS_API_KEY" not in source
    assert "TrendReq" not in source
    assert "_gnews_items" not in source
    assert "_discovery_query_lanes" not in source
    assert "_adaptive_discovery_query" not in source


def test_discovery_query_budget_prioritizes_requested_and_category_context():
    requested = "Smriti Mandhana latest match"
    category = "Cricket OR IPL OR BCCI OR Tennis"
    queries = story_ranker._build_discovery_google_queries(
        "sports_stories_of_day",
        {"gnews_q": category},
        custom_gnews_q=requested,
        broad_discovery=False,
    )

    assert queries[0] == requested
    assert category in queries
    assert len(queries) <= story_ranker.DISCOVERY_MAX_GOOGLE_QUERIES_STANDARD


def test_google_news_search_rss_url_is_not_fetched_as_a_second_rss_lane():
    query = "Test Cricket OR ICC OR Ashes"
    rss_url = (
        "https://news.google.com/rss/search?q="
        + story_ranker.quote_plus(query)
        + "&hl=en-IN&gl=IN&ceid=IN:en"
    )

    queries = story_ranker._build_discovery_google_queries(
        "sports_stories_of_day",
        {},
        custom_gnews_q=query,
        selected_rss=rss_url,
        broad_discovery=False,
    )

    assert queries.count(query) == 1


def test_source_quality_uses_publisher_not_article_text():
    story = {
        "source": "Unknown Source",
        "url": "https://example.com/story",
        "text": "Reuters reported that the event happened today.",
    }

    assert story_ranker._source_quality(story) == 0


def test_reddit_is_social_signal_not_factual_event_input(monkeypatch):
    def fake_google(*_args, **_kwargs):
        return [{
            "title": "Major company announces new product",
            "text": "Company announcement",
            "description": "Company announcement",
            "source": "Reuters",
            "source_name": "Reuters",
            "publisher": "Reuters",
            "url": "https://reuters.example/product",
            "publishedAt": "2026-09-20T10:00:00Z",
            "genre": "",
            "collection_source": "google_news_rss",
        }]

    def fake_reddit(*_args, **_kwargs):
        return [{
            "title": "Major company announces new product",
            "text": "Social discussion",
            "description": "Social discussion",
            "source": "Reddit r/news",
            "source_name": "Reddit r/news",
            "publisher": "Reddit r/news",
            "url": "https://reddit.com/r/news/example",
            "publishedAt": "2026-09-20T10:01:00Z",
            "genre": "",
            "collection_source": "reddit",
        }]

    monkeypatch.setattr(story_ranker, "_google_news_search_items", fake_google)
    monkeypatch.setattr(story_ranker, "_rss_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(story_ranker, "_official_feed_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(story_ranker, "_google_trends_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(story_ranker, "_reddit_items", fake_reddit)
    monkeypatch.setattr(story_ranker, "fetch_gdelt_articles", lambda *args, **kwargs: [])

    events, social_titles = story_ranker.collect_high_recall_stories(
        None,
        "national_global_affairs",
        {"gnews_q": "company news"},
        broad_discovery=False,
    )

    assert social_titles == ["Major company announces new product"]
    assert events
    assert all(
        str(item.get("collection_source") or "").lower() != "reddit"
        for event in events
        for item in (event.get("event_evidence") or [])
    )


def test_gdelt_is_only_used_as_one_fallback_when_core_intake_is_light(monkeypatch):
    gdelt_calls = []

    def fake_google(*_args, **_kwargs):
        return [{
            "title": "Single core event",
            "text": "Core event",
            "description": "Core event",
            "source": "Example News",
            "source_name": "Example News",
            "publisher": "Example News",
            "url": "https://example.com/core",
            "publishedAt": "2026-09-20T10:00:00Z",
            "genre": "",
            "collection_source": "google_news_rss",
        }]

    monkeypatch.setattr(story_ranker, "_google_news_search_items", fake_google)
    monkeypatch.setattr(story_ranker, "_rss_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(story_ranker, "_official_feed_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(story_ranker, "_google_trends_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(story_ranker, "_reddit_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        story_ranker,
        "fetch_gdelt_articles",
        lambda *args, **kwargs: gdelt_calls.append((args, kwargs)) or [],
    )

    story_ranker.collect_high_recall_stories(
        None,
        "national_global_affairs",
        {"gnews_q": "company news"},
        broad_discovery=False,
    )

    assert len(gdelt_calls) == 1
    assert gdelt_calls[0][1]["max_records"] == 75
    assert gdelt_calls[0][1]["timeout"] == 3.0


def test_configured_reddit_json_feed_is_routed_to_its_subreddit_social_lane():
    url = "https://www.reddit.com/r/Damnthatsinteresting/hot.json?limit=20"
    assert story_ranker._is_reddit_json_url(url)
    assert story_ranker._reddit_subreddit_from_url(url) == "Damnthatsinteresting"
