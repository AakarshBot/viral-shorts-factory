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
    assert any(concept["social_post_count"] == 1 for concept in concepts)
    assert any(concept["article_count"] == 1 for concept in concepts)



def test_cricket_desk_buckets_are_distinct_and_target_ten_each(monkeypatch):
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



def test_cricket_desk_uses_scope_specific_google_lanes():
    india = desk._google_queries_for_scope("India / Asia")
    global_queries = desk._google_queries_for_scope("Global")
    assert len(india) == 7
    assert len(global_queries) == 7
    assert any("India" in query for query in india)
    assert any("Australia" in query for query in global_queries)


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


def test_google_news_snippets_survive_until_event_clustering():
    row = _article("India survive Japan scare in dramatic T20 finish", "news.google.com")
    row["text"] = "Too short"
    row["description"] = "Short"
    row["collection_source"] = "google_news_rss"
    result = desk._normalise_rows([row])
    assert len(result) == 1


def test_cricket_bucket_caps_one_event_family_in_the_top_window():
    concepts = []
    for index in range(8):
        concepts.append({
            "title": f"India win Asian Games cricket story {index}",
            "news_score": 100 - index,
            "viral_score": 80 - index,
            "social_score": 60 - index,
            "undercovered_score": 2,
            "social_post_count": 0,
        })
    for index in range(30):
        concepts.append({
            "title": f"Distinct cricket development {index} in player {index}",
            "news_score": 70 - index * 0.1,
            "viral_score": 65 - index * 0.1,
            "social_score": 60 - index * 0.1,
            "undercovered_score": 8,
            "social_post_count": 0,
        })
    result = desk._bucketize(concepts)
    assert len(result) == 30
    assert sum(
        1 for item in result[:6]
        if item.get("cricket_event_family") == "asian_games"
    ) <= 2
    assert sum(
        1 for item in result
        if item.get("cricket_event_family") == "asian_games"
    ) <= 4
