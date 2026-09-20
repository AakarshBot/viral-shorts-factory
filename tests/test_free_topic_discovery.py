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

    def fake_rss(url, genre_key, collection_source="rss", max_items=60):
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


def test_manual_trending_wrapper_preserves_target_and_filter(monkeypatch):
    captured = {}

    def fake_trends(geos, max_terms=15):
        captured["geos"] = geos
        captured["max_terms"] = max_terms
        return ["Cricket World Cup", "AI research", "Bollywood release"]

    monkeypatch.setattr(story_ranker, "fetch_google_trending_topics", fake_trends)
    import ultimate_bot

    trends = ultimate_bot.fetch_trending_topics(
        target="india",
        query_filter="Cricket OR BCCI",
    )

    assert captured == {"geos": ("IN",), "max_terms": 30}
    assert trends == ["Cricket World Cup"]
