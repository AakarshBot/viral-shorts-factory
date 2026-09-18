import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from dashboard_runtime import (
    DashboardWorkflowController,
    build_discovery_evidence,
    collect_channel_statistics,
)
from workflow_runtime import WorkflowController


class _Bot:
    def __init__(self):
        def run_robot():
            return None

        self.run_robot = run_robot


def test_dashboard_controller_pauses_after_visuals_until_approval(monkeypatch):
    async def fake_visuals(*_args, **_kwargs):
        return [[{"image": "/tmp/scene_1.jpg", "source_type": "Pexels"}]]

    def fake_install(self):
        self._patched = True
        self.bot.run_robot.__globals__["process_visuals_async"] = fake_visuals
        self.bot.process_visuals_async = fake_visuals

    monkeypatch.setattr(WorkflowController, "_install_production_wrappers", fake_install)

    controller = DashboardWorkflowController(_Bot())
    controller._install_production_wrappers()

    result = {}

    def runner():
        result["packages"] = asyncio.run(
            controller.bot.run_robot.__globals__["process_visuals_async"]()
        )

    thread = threading.Thread(target=runner)
    thread.start()

    deadline = time.time() + 2
    while time.time() < deadline:
        if controller.snapshot()["stage"] == "visual_approval":
            break
        time.sleep(0.01)

    snapshot = controller.snapshot()
    assert snapshot["visual_review_required"] is True
    assert snapshot["visual_packages"]

    assert controller.approve_visuals() is True
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result["packages"] == snapshot["visual_packages"]
    assert controller.snapshot()["stage"] == "render"


def test_dashboard_controller_rejects_visuals_and_wakes_worker(monkeypatch):
    async def fake_visuals(*_args, **_kwargs):
        return [[{"image": "/tmp/scene_1.jpg"}]]

    def fake_install(self):
        self._patched = True
        self.bot.run_robot.__globals__["process_visuals_async"] = fake_visuals
        self.bot.process_visuals_async = fake_visuals

    monkeypatch.setattr(WorkflowController, "_install_production_wrappers", fake_install)

    controller = DashboardWorkflowController(_Bot())
    controller._install_production_wrappers()

    result = {}

    def runner():
        try:
            asyncio.run(controller.bot.run_robot.__globals__["process_visuals_async"]())
        except Exception as exc:
            result["error"] = str(exc)

    thread = threading.Thread(target=runner)
    thread.start()

    deadline = time.time() + 2
    while time.time() < deadline:
        if controller.snapshot()["stage"] == "visual_approval":
            break
        time.sleep(0.01)

    assert controller.reject_visuals() is True
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert "Visual review was rejected" in result["error"]


def test_collect_channel_statistics_reads_recorded_vault_data(tmp_path):
    db_path = Path(tmp_path) / "vault.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE vault (
            id INTEGER PRIMARY KEY,
            date_used TEXT,
            topic TEXT,
            genre TEXT,
            format_used TEXT,
            language_used TEXT,
            views INTEGER,
            avg_view_percentage REAL,
            title_ctr REAL,
            status TEXT,
            video_id TEXT,
            created_at TEXT
        )"""
    )
    conn.executemany(
        """INSERT INTO vault
        (date_used, topic, genre, format_used, language_used, views, avg_view_percentage, title_ctr, status, video_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("2026-09-17", "Story A", "technology", "regular", "english", 1000, 62.0, 4.0, "COMPLETED", "abc", "2026-09-17"),
            ("2026-09-16", "Story B", "sports", "top5", "hindi", 500, 58.0, 3.0, "COMPLETED", "def", "2026-09-16"),
        ],
    )
    conn.commit()
    conn.close()

    stats = collect_channel_statistics(str(db_path))

    assert stats["total_runs"] == 2
    assert stats["completed_runs"] == 2
    assert stats["total_views"] == 1500
    assert stats["avg_view_percentage"] == 60.0
    assert stats["avg_ctr"] == 3.5
    assert {row["format"] for row in stats["by_format"]} == {"regular", "top5"}


def test_dashboard_discovery_retains_twenty_ranked_topics(monkeypatch):
    import story_ranker

    class Bot:
        CONTENT_CATEGORIES = {"technology": {"gnews_q": "technology news"}}
        GNEWS_API_KEY = ""

    topic_specs = [
        ("Quantum chip breakthrough", "quantum computing"),
        ("Electric vehicle battery milestone", "electric vehicles"),
        ("Satellite internet expansion", "satellite internet"),
        ("Robot factory rollout", "industrial robotics"),
        ("Foldable phone launch", "foldable smartphone"),
        ("AI search assistant release", "AI search"),
        ("Space telescope discovery", "space telescope"),
        ("Semiconductor plant investment", "semiconductor plant"),
        ("Cloud security platform update", "cloud security"),
        ("Autonomous taxi expansion", "autonomous taxi"),
        ("New gene editing platform", "gene editing"),
        ("AR headset developer launch", "augmented reality"),
        ("Quantum battery research milestone", "quantum battery"),
        ("Space station cargo mission", "space station cargo"),
        ("AI coding tool enterprise rollout", "AI coding"),
        ("Solar farm expansion project", "solar energy"),
        ("New privacy regulation proposal", "privacy regulation"),
        ("Next generation gaming console", "gaming console"),
        ("Medical imaging breakthrough", "medical imaging"),
        ("Data centre investment boom", "data centre"),
    ]
    topics = [
        {
            "title": f"{title} changes the {subject} market",
            "url": f"https://reuters.example/story-{index}",
            "source": "Reuters",
            "publishedAt": "2026-09-18T00:00:00+00:00",
            "description": f"Current reporting about {subject} with verified details.",
            "genre": "technology",
        }
        for index, (title, subject) in enumerate(topic_specs, 1)
    ]

    monkeypatch.setattr(story_ranker, "_query_variants", lambda *_args, **_kwargs: ["technology"])
    monkeypatch.setattr(story_ranker, "_gnews_items", lambda *_args, **_kwargs: list(topics))
    monkeypatch.setattr(story_ranker, "_rss_items", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(story_ranker, "_reddit_items", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(story_ranker, "_india_trend_terms", lambda: tuple())

    pool = __import__("dashboard_runtime").discover_ranked_topics(
        Bot(),
        {"format_mode": "regular", "category": "technology", "language": "english"},
        conn=None,
        max_candidates=20,
    )

    assert 15 <= len(pool) <= 20
    assert [item["discovery_rank"] for item in pool] == list(range(1, len(pool) + 1))


def test_recent_topic_cooldown_removes_only_recent_repeats(tmp_path):
    from dashboard_runtime import _recent_topic_cooldown

    db_path = Path(tmp_path) / "cooldown.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE vault (
            topic TEXT,
            date_used TEXT,
            created_at TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO vault (topic, date_used, created_at) VALUES (?, ?, ?)",
        [
            ("Major battery breakthrough announced", "2026-09-17T10:00:00+00:00", "2026-09-17T10:00:00+00:00"),
            ("Old satellite launch story", "2026-09-10T10:00:00+00:00", "2026-09-10T10:00:00+00:00"),
        ],
    )
    conn.commit()

    stories = [
        {"title": "Battery breakthrough announced with new results"},
        {"title": "Satellite launch gets a fresh update"},
        {"title": "Completely new robotics factory opens"},
    ]

    kept = _recent_topic_cooldown(
        conn,
        stories,
        hours=48,
    )

    kept_titles = [item["title"] for item in kept]
    assert "Battery breakthrough announced with new results" not in kept_titles
    assert "Completely new robotics factory opens" in kept_titles
    assert "Satellite launch gets a fresh update" in kept_titles

    conn.close()



def test_build_discovery_evidence_summarises_event_support_and_signals():
    candidate = {
        "event_article_count": 7,
        "event_source_count": 3,
        "event_evidence_publishers": ["Reuters", "BBC", "Associated Press"],
        "event_source_domains": ["reuters.com", "bbc.com", "apnews.com"],
        "event_latest_published_at": "2026-09-18T08:00:00+00:00",
        "event_momentum_score": 6.5,
        "freshness_score": 8.0,
        "corroboration_bonus": 6.0,
        "source_quality_score": 4.5,
        "social_signal": 2.0,
        "google_trends_signal": 1.0,
        "visual_potential": 8.0,
        "originality_score": 7.5,
        "risk_signal_count": 0,
        "discovery_dimensions": {
            "channel_history": 4.0,
        },
        "event_evidence": [
            {
                "title": "NASA launches Artemis",
                "url": "https://reuters.com/story",
                "publisher": "Reuters",
                "publishedAt": "2026-09-18T08:00:00+00:00",
                "collection_source": "official",
            },
            {
                "title": "Artemis lifts off",
                "url": "https://bbc.com/story",
                "publisher": "BBC",
                "publishedAt": "2026-09-18T07:30:00+00:00",
                "collection_source": "test",
            },
        ],
    }

    evidence = build_discovery_evidence(candidate)

    assert evidence["articles"] == 7
    assert evidence["independent_publishers"] == 3
    assert evidence["independent_domains"] == 3
    assert evidence["official_records"] == 1
    assert evidence["event_momentum"] == 6.5
    assert evidence["channel_history"] == 4.0
    assert len(evidence["sources"]) == 2
