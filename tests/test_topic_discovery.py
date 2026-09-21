import sqlite3
from datetime import datetime, timedelta, timezone

import story_ranker
from db_architecture import migrate_vault


def _event(title, url, published, actions, body="", entities=None, event_id=""):
    return {
        "title": title,
        "url": url,
        "source": "example.in",
        "publishedAt": published,
        "description": body,
        "event_actions": actions,
        "event_entities": entities or ["India"],
        "event_clustered": True,
        "event_id": event_id or title.lower().replace(" ", "-"),
        "event_source_count": 1,
        "event_article_count": 1,
    }


def test_recent_topic_cooldown_ignores_selected_but_not_posted_runs(tmp_path):
    db_path = tmp_path / "vault.db"
    conn = sqlite3.connect(db_path)
    migrate_vault(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO vault
        (run_id, topic, date_used, video_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("selected", "India cricket squad update", now, "READY_FOR_UPLOAD", "READY_FOR_UPLOAD", now, now),
    )
    conn.execute(
        """INSERT INTO vault
        (run_id, topic, date_used, video_id, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("posted", "India budget tax change", now, "youtube-123", "UPLOADED", now, now),
    )
    conn.commit()

    story_ranker_tester = story_ranker._recent_topic_cooldown
    stories = [
        {"title": "India cricket squad update", "event_actions": ["announce"]},
        {"title": "India budget tax change", "event_actions": ["change"]},
    ]
    try:
        kept = story_ranker_tester(conn, stories, hours=72)
    finally:
        conn.close()

    assert [item["title"] for item in kept] == ["India cricket squad update"]


def test_deduplicate_stage_dedupes_similar_event_clusters():
    stories = [
        _event(
            "BCCI announces India's new cricket squad",
            "https://example.in/a",
            "2026-09-21T10:00:00+00:00",
            ["announce"],
            "BCCI announced the squad for the upcoming series.",
            ["BCCI", "India"],
            event_id="event-a",
        ),
        _event(
            "India cricket squad announced by BCCI for upcoming series",
            "https://example.in/b",
            "2026-09-21T09:30:00+00:00",
            ["announce"],
            "The latest squad announcement was released by BCCI.",
            ["BCCI", "India"],
            event_id="event-b",
        ),
    ]

    selected = story_ranker._deduplicate_stage(stories, max_items=10)

    assert len(selected) == 1
    assert selected[0]["event_id"] == "event-a"


def test_topic_actionability_rejects_hollow_headline():
    story = _event(
        "You will not believe what happened today in cricket",
        "https://example.in/a",
        "2026-09-21T10:00:00+00:00",
        [],
        "",
        [],
    )
    assert story_ranker._topic_actionability(story) < 3.0


def test_topic_actionability_accepts_event_with_story_detail():
    story = _event(
        "India women win the semifinal and reach the final",
        "https://example.in/a",
        "2026-09-21T10:00:00+00:00",
        ["win"],
        "India women won the semifinal and advanced to the final after a decisive performance.",
        ["India women"],
    )
    assert story_ranker._topic_actionability(story) >= 3.0


def test_discovery_queries_have_india_first_and_global_lane():
    cfg = {
        "india_gnews_q": "(India OR Indian) (technology OR AI)",
        "global_gnews_q": "(global OR worldwide) (AI OR technology) (launch OR breakthrough)",
        "gnews_q": "(India OR Indian) technology OR global technology",
    }
    queries = story_ranker._build_discovery_google_queries(
        "technology",
        cfg,
        broad_discovery=True,
    )

    assert queries[0] == cfg["india_gnews_q"]
    assert cfg["global_gnews_q"] in queries
    assert len(queries) <= story_ranker.DISCOVERY_MAX_GOOGLE_QUERIES_BROAD

def test_explicit_genre_does_not_add_generic_cross_genre_radar():
    cfg = {
        "india_gnews_q": "(India OR Indian) (technology OR AI)",
        "global_gnews_q": "(global OR worldwide) (AI OR technology) (launch OR breakthrough)",
        "gnews_q": "(India OR Indian) technology",
    }
    queries = story_ranker._build_discovery_google_queries(
        "technology",
        cfg,
        broad_discovery=True,
    )
    assert " (sports OR cricket OR football OR tennis)" not in " ".join(queries).lower()
    assert story_ranker.GOOGLE_NEWS_RADAR_QUERIES[0] not in queries

def test_used_topic_history_contains_only_posted_runs(tmp_path):
    db_path = tmp_path / "vault.db"
    conn = sqlite3.connect(db_path)
    migrate_vault(conn)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO vault (run_id, topic, date_used, video_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("clicked", "Clicked cricket story", now, "READY_FOR_UPLOAD", "READY_FOR_UPLOAD", now, now),
    )
    conn.execute(
        "INSERT INTO vault (run_id, topic, date_used, video_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("posted", "Posted cricket story", now, "youtube-456", "UPLOADED", now, now),
    )
    conn.commit()

    try:
        topics = story_ranker._load_used_topics(conn)
    finally:
        conn.close()

    assert "Posted cricket story" in topics
    assert "Clicked cricket story" not in topics
