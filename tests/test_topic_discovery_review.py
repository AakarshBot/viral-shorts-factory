from datetime import datetime, timezone

import story_ranker


class _FakeResponse:
    status_code = 200

    def __init__(self, content):
        self.content = content


def test_latest_trustworthy_publication_or_update_time_drives_freshness():
    story = {
        "publishedAt": "2026-09-17T10:00:00+00:00",
        "updated_at": "2026-09-21T18:00:00+00:00",
    }

    observed = story_ranker._published_datetime(story)

    assert observed == datetime(2026, 9, 21, 18, 0, tzinfo=timezone.utc)


def test_rss_adapter_accepts_atom_entries(monkeypatch):
    atom = b"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Fresh Atom headline</title>
        <link href="https://example.com/story"/>
        <summary>Useful story summary.</summary>
        <published>2026-09-22T09:30:00Z</published>
        <author><name>Example News</name></author>
      </entry>
    </feed>"""

    monkeypatch.setattr(
        story_ranker.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse(atom),
    )

    rows = story_ranker._rss_items(
        "https://example.com/feed",
        "technology",
        collection_source="official",
        max_items=5,
    )

    assert len(rows) == 1
    assert rows[0]["title"] == "Fresh Atom headline"
    assert rows[0]["url"] == "https://example.com/story"
    assert rows[0]["publishedAt"] == "2026-09-22T09:30:00Z"
    assert rows[0]["source"] == "Example News"


def test_sports_discovery_queries_cover_niche_sports():
    import ultimate_bot

    cfg = ultimate_bot.CONTENT_CATEGORIES["sports"]
    query_blob = " ".join(
        [
            cfg.get("gnews_q", ""),
            cfg.get("india_gnews_q", ""),
            cfg.get("global_gnews_q", ""),
        ]
    ).lower()

    for term in ("golf", "rugby", "motorsport", "boxing", "wrestling", "formula 1"):
        assert term in query_blob
