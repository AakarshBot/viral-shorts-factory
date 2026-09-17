import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from dashboard_runtime import DashboardWorkflowController, collect_channel_statistics
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


def test_dashboard_discovery_retains_twelve_ranked_topics(monkeypatch):
    import story_ranker

    class Bot:
        CONTENT_CATEGORIES = {"technology": {"gnews_q": "technology news"}}
        GNEWS_API_KEY = ""

    topics = [
        {
            "title": f"Technology story {index} about a new launch",
            "url": f"https://reuters.example/story-{index}",
            "source": "Reuters",
            "publishedAt": "2026-09-18T00:00:00+00:00",
            "description": "A current technology development with verified reporting.",
            "genre": "technology",
        }
        for index in range(1, 13)
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
        max_candidates=12,
    )

    assert len(pool) == 12
    assert [item["discovery_rank"] for item in pool] == list(range(1, 13))
