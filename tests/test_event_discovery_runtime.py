from event_discovery_runtime import cluster_news_events, normalize_publisher, normalize_url


def _article(title, url, publisher, published_at="2026-09-18T08:00:00+00:00"):
    return {
        "title": title,
        "url": url,
        "source_name": publisher,
        "publishedAt": published_at,
        "collection_source": "test",
    }


def test_normalize_url_removes_tracking_parameters():
    value = normalize_url(
        "https://www.example.com/story/123/?utm_source=x&utm_campaign=y&id=7#fragment"
    )
    assert value == "https://example.com/story/123?id=7"


def test_normalize_publisher_prefers_explicit_source_over_gnews_wrapper():
    article = {
        "source": "GNews",
        "source_name": "Reuters",
        "url": "https://news.google.com/articles/example",
    }
    assert normalize_publisher(article) == "reuters"


def test_cluster_news_events_preserves_corroborating_articles_as_one_event():
    articles = [
        _article(
            "NASA launches new lunar mission from Florida",
            "https://reuters.com/nasa-lunar-mission",
            "Reuters",
        ),
        _article(
            "NASA successfully launches new lunar mission from Florida",
            "https://apnews.com/nasa-lunar-mission",
            "Associated Press",
            "2026-09-18T07:30:00+00:00",
        ),
        _article(
            "NASA lunar mission launches successfully from Florida",
            "https://bbc.com/nasa-lunar-mission",
            "BBC",
            "2026-09-18T07:00:00+00:00",
        ),
        _article(
            "Central bank holds interest rates steady",
            "https://example.com/rates",
            "Example Finance",
            "2026-09-18T07:45:00+00:00",
        ),
    ]

    events = cluster_news_events(articles)

    assert len(events) == 2
    lunar = next(item for item in events if "lunar mission" in item["title"].lower())
    assert lunar["event_article_count"] == 3
    assert lunar["event_source_count"] == 3
    assert len(lunar["event_evidence"]) == 3
    assert lunar["event_corroboration_score"] == 6.0


def test_cluster_news_events_does_not_merge_unrelated_headlines():
    articles = [
        _article(
            "India announces new solar power investment",
            "https://example.com/solar",
            "Example One",
        ),
        _article(
            "India announces new cricket stadium investment",
            "https://example.com/cricket",
            "Example Two",
        ),
    ]

    events = cluster_news_events(articles)

    assert len(events) == 2
