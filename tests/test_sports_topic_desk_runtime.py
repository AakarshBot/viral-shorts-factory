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


def test_cricket_desk_duplicate_headlines_form_one_concept():
    rows = [
        _article("India survive Japan scare in dramatic T20 finish", "one.example"),
        _article("India survive Japan scare in dramatic T20 finish", "two.example"),
        _article("India survive Japan scare in dramatic T20 finish", "three.example"),
        _article("Pakistan recall uncapped fast bowler for Zimbabwe tour", "four.example"),
    ]
    concepts = desk._cluster(rows)
    assert len(concepts) == 2
    india = next(item for item in concepts if "Japan" in item["title"])
    assert india["article_count"] == 3
    assert india["independent_source_count"] == 3


def test_cricket_desk_keeps_social_leads_separate():
    rows = [
        _article("Player responds after controversial umpiring call", "one.example"),
        {
            "title": "Fans debate the controversial umpiring call",
            "text": "Fans debate the controversial umpiring call",
            "description": "Fans debate the controversial umpiring call",
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
    concepts = desk._cluster(rows)
    assert any(
        concept["social_post_count"] == 1 and concept["article_count"] == 1
        for concept in concepts
    )


def test_cricket_desk_buckets_are_distinct_and_target_ten_each(monkeypatch):
    concepts = []
    for index in range(36):
        concepts.append(_article(
            f"Undercovered cricket development story {index} player {index} milestone",
            f"source{index}.example",
        ))
    monkeypatch.setattr(desk, "_collect", lambda: concepts)
    monkeypatch.setattr(desk, "_normalise_rows", lambda rows: rows)
    monkeypatch.setattr(desk, "_trend_signal", lambda item, trends: 0.0)

    result = desk.discover_cricket_topics(
        bot=None,
        scope="Global",
        requested_topic="",
        max_candidates=30,
        retained_candidates=[],
    )
    assert len(result) >= 30
    buckets = {key: [x for x in result if x.get("discovery_bucket") == key] for key in ("news", "viral", "social")}
    assert all(len(items) == 10 for items in buckets.values())
    assert len({item["cluster_id"] for item in result[:30]}) == 30


def test_cricket_desk_exposes_undercoverage_and_signal_dimensions():
    item = _article("Uncapped bowler takes first five wicket haul in domestic thriller", "example.com")
    item["social_post_count"] = 3
    item["social_engagement_total"] = 7
    item["independent_source_count"] = 1
    item["article_count"] = 1
    scored = desk._score(item, [], [])
    assert scored["undercovered_score"] >= 7
    assert "viral_signal_score" in scored
    assert "social_signal_score" in scored


def test_cricket_desk_scope_keeps_india_asia_primary(monkeypatch):
    rows = [
        _article("India domestic bowler breaks record", "india.example"),
        _article("England batter breaks record", "england.example"),
    ]
    monkeypatch.setattr(desk, "_collect", lambda: rows)
    monkeypatch.setattr(desk, "_normalise_rows", lambda rows: rows)
    result = desk.discover_cricket_topics(
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
