import asyncio
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
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


def test_dashboard_script_review_pauses_and_applies_queries(monkeypatch):
    def fake_write_script(*_args, **_kwargs):
        return {
            "title": "Synthetic story",
            "script": [
                {
                    "primary_entity": "Rishabh Pant",
                    "voiceover": "Rishabh Pant speaks at a press conference.",
                },
                {
                    "primary_entity": "India",
                    "voiceover": "India announced the squad.",
                },
            ],
        }

    def fake_install(self):
        self._patched = True
        self.bot.run_robot.__globals__["write_script"] = fake_write_script
        self.bot.write_script = fake_write_script

    monkeypatch.setattr(WorkflowController, "_install_production_wrappers", fake_install)

    controller = DashboardWorkflowController(_Bot())
    controller._install_production_wrappers()

    result = {}
    namespace = controller.bot.run_robot.__globals__

    def runner():
        result["script"] = namespace["write_script"]()

    thread = threading.Thread(target=runner)
    thread.start()

    deadline = time.time() + 2
    while time.time() < deadline:
        if controller.snapshot()["stage"] == "script_review":
            break
        time.sleep(0.01)

    snapshot = controller.snapshot()
    assert snapshot["script_review_required"] is True
    assert len(snapshot["script_data"]["script"]) == 2

    assert controller.submit_script_visual_queries(
        ["Rishabh Pant press conference", ""]
    ) is True

    thread.join(timeout=2)
    assert not thread.is_alive()

    script = result["script"]
    assert script["script"][0]["manual_visual_query"] == "Rishabh Pant press conference"
    assert "manual_visual_query" not in script["script"][1]
    assert controller.snapshot()["stage"] == "audio"


def test_dashboard_start_production_reaches_script_review(monkeypatch):
    import workflow_runtime

    def fake_write_script(*_args, **_kwargs):
        return {
            "title": "Live integration story",
            "script": [
                {"primary_entity": "Gautam Gambhir", "voiceover": "Gautam Gambhir speaks."},
                {"primary_entity": "India", "voiceover": "India prepares for the match."},
            ],
        }

    class LiveBot:
        CONTENT_CATEGORIES = {"national_global_affairs": {"label": "News"}}

        def __init__(self):
            def run_robot(web_config=None):
                return run_robot.__globals__["write_script"](
                    {}, {}, "national_global_affairs", None, "regular"
                )

            self.run_robot = run_robot
            self.run_robot.__globals__["write_script"] = fake_write_script

    bot = LiveBot()
    controller = DashboardWorkflowController(bot)

    monkeypatch.setattr(
        workflow_runtime,
        "run_robot_with_exact_identity",
        lambda bot_obj, web_config=None: bot_obj.run_robot(web_config=web_config),
    )
    monkeypatch.setattr(controller, "_mark_latest_run_ready_for_qc", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        workflow_runtime,
        "_build_clean_metadata",
        lambda *_args, **_kwargs: ("Title", "Description", []),
    )
    monkeypatch.setattr(
        workflow_runtime,
        "build_pinned_comment",
        lambda *_args, **_kwargs: "Comment",
    )

    controller.start_production(
        {
            "format_mode": "regular",
            "category": "national_global_affairs",
            "language": "english",
        },
        {
            "title": "Live integration story",
            "story_key": "live integration story",
            "discovery_rank": 1,
            "dashboard_discovery_pool": True,
        },
    )

    deadline = time.time() + 2
    while time.time() < deadline:
        if controller.snapshot()["stage"] == "script_review":
            break
        time.sleep(0.01)

    snapshot = controller.snapshot()
    assert snapshot["script_review_required"] is True
    assert len(snapshot["script_data"]["script"]) == 2

    assert controller.submit_script_visual_queries(
        ["Gautam Gambhir press conference", ""]
    ) is True

    deadline = time.time() + 2
    while time.time() < deadline and controller.snapshot()["thread_alive"]:
        time.sleep(0.01)

    assert not controller.snapshot()["thread_alive"]


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


def test_dashboard_controller_captures_generated_audio_paths(tmp_path):
    bot = _Bot()
    audio_one = tmp_path / "voiceover_1.mp3"
    audio_two = tmp_path / "voiceover_2.mp3"
    audio_one.write_bytes(b"audio")
    audio_two.write_bytes(b"audio")

    async def fake_voiceover(*_args, **_kwargs):
        return (
            [str(audio_one), str(audio_two)],
            [[{"word": "one"}], [{"word": "two"}]],
        )

    bot.run_robot.__globals__["generate_voiceover_and_timestamps"] = fake_voiceover

    controller = DashboardWorkflowController(bot)
    controller._install_production_wrappers()

    result = asyncio.run(
        bot.run_robot.__globals__["generate_voiceover_and_timestamps"]()
    )

    assert result[0] == [str(audio_one), str(audio_two)]
    assert controller.snapshot()["audio_paths"] == [str(audio_one.resolve()), str(audio_two.resolve())]
    assert controller.snapshot()["stage"] == "audio"


def test_dashboard_controller_replaces_only_requested_visual(monkeypatch, tmp_path):
    from PIL import Image
    import visual_query_entities_runtime
    import visual_quality_runtime
    import branding_runtime
    import visual_strategy_runtime

    bot = _Bot()
    bot.ASSETS_DIR = str(tmp_path)
    bot.LANGUAGES = {"english": {"font": "arial.ttf"}}
    bot._active_web_config = {"format_mode": "regular", "language": "english"}

    old_one = tmp_path / "scene_1_img.jpg"
    old_two = tmp_path / "scene_2_img.jpg"
    Image.new("RGB", (1080, 1920), "white").save(old_one, "JPEG")
    Image.new("RGB", (1080, 1920), "black").save(old_two, "JPEG")

    controller = DashboardWorkflowController(bot)
    controller.state.script_data = {
        "title": "Replacement test",
        "script": [
            {
                "primary_entity": "Subject One",
                "voiceover": "Subject one appears.",
                "sport_or_topic_category": "news",
                "visual_genre": "GENERAL_CONTEXT",
            },
            {
                "primary_entity": "Subject Two",
                "voiceover": "Subject two appears.",
                "sport_or_topic_category": "news",
                "visual_genre": "GENERAL_CONTEXT",
            },
        ],
    }
    controller._visual_packages = [
        [{"image": str(old_one), "manual_visual_query": "old query one", "source_type": "pexels", "visual_verified": True}],
        [{"image": str(old_two), "manual_visual_query": "old query two", "source_type": "pexels", "visual_verified": True}],
    ]
    controller.update("visual_approval", 76, "Visuals ready.")

    def fake_search(*_args, **kwargs):
        scene = _args[2]
        assert kwargs["manual_query"] == "new query for slide one"
        scene["visual_verified"] = True
        scene["visual_query_used"] = "new query for slide one"
        scene["visual_genre"] = "GENERAL_CONTEXT"
        return Image.new("RGB", (900, 1200), "gray"), False, "pexels"

    monkeypatch.setattr(visual_query_entities_runtime, "search_slide_visual", fake_search)
    monkeypatch.setattr(visual_quality_runtime, "fit_visual_image", lambda image, *_args: image)
    monkeypatch.setattr(branding_runtime, "source_credit_for_type", lambda source: f"credit:{source}")
    monkeypatch.setattr(visual_strategy_runtime, "classify_scene", lambda *_args: "GENERAL_CONTEXT")

    ok, message = controller.replace_visual(1, "new query for slide one")

    assert ok is True
    assert "replaced successfully" in message.lower()
    snapshot = controller.snapshot()
    assert snapshot["visual_packages"][0][0]["manual_visual_query"] == "new query for slide one"
    assert snapshot["visual_packages"][1][0]["image"] == str(old_two)
    assert snapshot["visual_replacement_history"][1][0]["old_path"] == str(old_one)
    assert snapshot["visual_replacement_history"][1][0]["new_query"] == "new query for slide one"
    assert snapshot["visual_review_approved"] is False
    assert Path(snapshot["visual_packages"][0][0]["image"]).is_file()
    assert Path(snapshot["visual_packages"][0][0]["image"]) != old_one


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



def test_dashboard_top5_handoff_keeps_selected_story_in_full_intake(monkeypatch):
    import workflow_runtime

    bot = _Bot()
    original_pool = [
        {"title": "Other story one"},
        {"title": "Other story two"},
        {"title": "Other story three"},
        {"title": "Other story four"},
    ]
    bot.run_robot.__globals__["gather_and_filter_stories"] = lambda *_args, **_kwargs: list(original_pool)

    captured = {}

    monkeypatch.setattr(
        WorkflowController,
        "_install_production_wrappers",
        lambda self: None,
    )

    def fake_run_robot_with_exact_identity(bot_obj, web_config=None):
        captured["config"] = dict(web_config or {})
        captured["pool"] = bot_obj.run_robot.__globals__["gather_and_filter_stories"]()

    monkeypatch.setattr(
        workflow_runtime,
        "run_robot_with_exact_identity",
        fake_run_robot_with_exact_identity,
    )

    controller = DashboardWorkflowController(bot)
    selected = {
        "title": "Selected lead story",
        "discovery_rank": 1,
        "story_key": "selected lead story https example com/selected",
        "dashboard_discovery_pool": True,
    }
    controller.start_production(
        {
            "format_mode": "top5",
            "category": "technology",
            "language": "english",
        },
        selected,
    )

    deadline = time.time() + 2
    while time.time() < deadline and controller.snapshot()["thread_alive"]:
        time.sleep(0.01)

    assert captured["config"]["format_mode"] == "top5"
    assert "selected_story" not in captured["config"]
    assert captured["pool"][0]["title"] == "Selected lead story"
    assert len(captured["pool"]) == 5

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
            "publishedAt": (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
            "description": f"Current reporting about {subject} with verified details.",
            "genre": "technology",
            "event_actions": ["launch"],
            "event_article_count": 3,
            "event_source_count": 2,
            "event_source_domains": ["reuters.com", "bbc.com"],
            "event_evidence_publishers": ["Reuters", "BBC"],
        }
        for index, (title, subject) in enumerate(topic_specs, 1)
    ]

    def fake_collect_high_recall_stories(
        bot,
        genre_key,
        genre_cfg,
        trend_keyword=None,
        custom_gnews_q=None,
        custom_rss_url=None,
        ai_cricket=False,
    ):
        return list(topics), []

    monkeypatch.setattr(
        story_ranker,
        "collect_high_recall_stories",
        fake_collect_high_recall_stories,
    )
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
            (
                "Major battery breakthrough announced",
                (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
                (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
            ),
            (
                "Old satellite launch story",
                (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(),
                (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(),
            ),
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

def test_live_qc_gates_are_real_blocking_checks(tmp_path):
    from dashboard_runtime import evaluate_live_qc_gates, live_qc_passes

    video = Path(tmp_path) / "final.mp4"
    video.write_bytes(b"not a real video")
    scene = {"voiceover": "One", "primary_entity": "Subject", "specific_search_prompt": "Subject event"}
    snapshot = {
        "run_id": "run-test",
        "selected_story": {"title": "Selected story", "story_key": "selected story", "discovery_rank": 1},
        "script_data": {
            "title": "Selected story",
            "titles": ["One", "Two", "Three"],
            "recommended_title_index": 0,
            "seo_description": "This is a sufficiently long description for the release metadata check.",
            "script": [dict(scene) for _ in range(5)],
        },
        "audio_paths": [],
        "visual_packages": [],
        "visual_review_approved": False,
        "video_path": str(video),
    }
    metadata = {
        "title": "Selected story",
        "description": "This is a sufficiently long description for the release metadata check.",
        "comment": "",
    }

    gates = evaluate_live_qc_gates(snapshot, metadata)
    by_key = {gate["key"]: gate for gate in gates}
    assert by_key["story_lock"]["passed"] is True
    assert by_key["script_contract"]["passed"] is True
    assert by_key["visual_package"]["passed"] is False
    assert by_key["visual_review"]["passed"] is False
    assert by_key["artifact_qc"]["passed"] is False
    assert live_qc_passes(snapshot, metadata) is False


def test_dashboard_primary_menu_and_generated_outputs_contract():
    app_source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

    assert 'mode_labels = ["Deep Dive", "Top 5", "Cricket", "AI"]' in app_source
    assert '["Live Factory", "Channel Statistics", "Run Offline Diagnostics", "Demo Factory"]' not in app_source
    assert 'def render_generated_outputs(snapshot: Dict[str, Any]) -> None:' in app_source
    assert 'render_generated_outputs(snapshot)' in app_source
    assert 'def render_script_visual_query_review(' in app_source
    assert 'visual_search_queries' in app_source
    assert 'assign_manual_queries' not in app_source
    assert '"qc_passed": bool(layer.get("visual_verified", False))' in app_source
    assert 'disabled=bool(qc_blocked)' in app_source
    assert 'Visual semantic QC blocked:' in app_source


def test_dashboard_ai_discovery_uses_bounded_query_lanes():
    source = Path(__file__).resolve().parents[1].joinpath("dashboard_runtime.py").read_text(encoding="utf-8")
    assert "_discovery_query_lanes" in source
    assert "ThreadPoolExecutor(max_workers=8" in source
    assert "_discovery_query_lanes(query, genre_key=category)[:2]" in source
