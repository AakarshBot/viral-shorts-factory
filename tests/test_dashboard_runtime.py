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



def test_live_monitor_polling_pauses_for_user_checkpoints():
    from dashboard_runtime import live_monitor_should_poll

    assert live_monitor_should_poll({"thread_alive": True, "stage": "research"}) is True
    assert live_monitor_should_poll({"thread_alive": True, "stage": "audio"}) is True
    assert live_monitor_should_poll({"thread_alive": True, "stage": "script_review"}) is False
    assert live_monitor_should_poll({"thread_alive": True, "stage": "visual_approval"}) is False
    assert live_monitor_should_poll({"thread_alive": False, "stage": "qc"}) is False


def test_dashboard_worker_console_capture():
    import dashboard_runtime

    controller = DashboardWorkflowController(_Bot())
    controller._worker_started()
    try:
        dashboard_runtime._DASHBOARD_STDOUT.write("synthetic dashboard console line\n")
    finally:
        controller._worker_finished()

    assert "synthetic dashboard console line" in controller.console_lines()


def test_live_qc_uses_active_format_from_snapshot():
    from dashboard_runtime import evaluate_live_qc_gates

    snapshot = {
        "format_mode": "top5",
        "selected_story": {
            "title": "Selected story",
            "story_key": "selected-story",
            "discovery_rank": 1,
        },
        "script_data": {
            "title": "Selected story",
            "titles": ["One", "Two", "Three"],
            "recommended_title_index": 0,
            "seo_description": "This is a sufficiently long description for the release metadata check.",
            "script": [
                {
                    "voiceover": "A valid scene with enough words.",
                    "primary_entity": "Subject",
                    "specific_search_prompt": "Subject event",
                }
                for _ in range(8)
            ],
        },
    }

    gates = evaluate_live_qc_gates(
        snapshot,
        {
            "title": "Selected story",
            "description": "A sufficiently long description for the release metadata check.",
        },
    )
    assert next(gate for gate in gates if gate["key"] == "script_contract")["passed"] is False


def test_dashboard_live_monitor_uses_controlled_polling():
    app_source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

    assert '@st.fragment(run_every="2s")' in app_source
    assert 'run_every="1s"' not in app_source
    assert "live_monitor_should_poll(snapshot)" in app_source
    assert "_render_content(live_snapshot)" in app_source


def test_dashboard_script_review_preserves_research_layer_marker(monkeypatch):
    def fake_write_script(*_args, **_kwargs):
        return {
            "title": "Marker story",
            "script": [
                {"voiceover": "A sufficiently long generated scene.", "primary_entity": "Story"},
                {"voiceover": "Another sufficiently long generated scene.", "primary_entity": "Story"},
            ],
        }

    fake_write_script._research_layer_live = True

    def fake_install(self):
        self._patched = True
        self.bot.run_robot.__globals__["write_script"] = fake_write_script
        self.bot.write_script = fake_write_script

    monkeypatch.setattr(WorkflowController, "_install_production_wrappers", fake_install)

    controller = DashboardWorkflowController(_Bot())
    controller._install_production_wrappers()

    wrapped = controller.bot.run_robot.__globals__["write_script"]
    assert wrapped._dashboard_script_review is True
    assert wrapped._research_layer_live is True

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
        ["Rishabh Pant press conference", ""],
    ) is True

    thread.join(timeout=2)
    assert not thread.is_alive()

    script = result["script"]
    assert script["script"][0]["manual_visual_query"] == "Rishabh Pant press conference"
    assert "manual_visual_query" not in script["script"][1]
    assert controller.snapshot()["stage"] == "audio"



def test_dashboard_manual_slide_query_alignment(monkeypatch):
    def fake_write_script(*_args, **_kwargs):
        return {
            "title": "Alignment story",
            "script": [
                {"primary_entity": "One", "voiceover": "One scene."},
                {"primary_entity": "Two", "voiceover": "Two scene."},
                {"primary_entity": "Three", "voiceover": "Three scene."},
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
    while time.time() < deadline and controller.snapshot()["stage"] != "script_review":
        time.sleep(0.01)

    assert controller.submit_script_visual_queries(
        ["query one", "query two", "query three"],
    ) is True

    thread.join(timeout=2)
    assert not thread.is_alive()

    scenes = result["script"]["script"]
    assert len(scenes) == 3
    assert scenes[0]["manual_visual_query"] == "query one"
    assert scenes[1]["manual_visual_query"] == "query two"
    assert scenes[2]["manual_visual_query"] == "query three"
    assert "human_contributed" not in scenes[0]
    assert "human_contributed" not in scenes[1]
    assert "human_contributed" not in scenes[2]

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
        ["Gautam Gambhir press conference", ""],
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



def test_dashboard_manual_qc_search_keeps_current_visual_and_returns_choices(monkeypatch, tmp_path):
    from PIL import Image
    import visual_retrieval_runtime

    bot = _Bot()
    bot.ASSETS_DIR = str(tmp_path)
    bot.LANGUAGES = {"english": {"font": "arial.ttf"}}
    bot._active_web_config = {"format_mode": "regular", "language": "english"}

    current = tmp_path / "current.jpg"
    Image.new("RGB", (1080, 1920), "white").save(current, "JPEG")

    option_paths = []
    options = []
    for index in range(3):
        path = tmp_path / f"option_{index + 1}.jpg"
        Image.new("RGB", (900, 1200), (40 + index * 20, 60, 90)).save(path, "JPEG")
        option_paths.append(path)
        options.append({
            "path": str(path),
            "hash": f"hash-{index + 1}",
            "source": "Commons",
            "query": "Shafali Verma batting",
            "visual_type": "PERSON",
            "visual_genre": "PERSON_ACTION",
            "provenance": {"url": f"https://example.com/{index + 1}"},
            "status": "entity-verified",
            "used": False,
        })

    controller = DashboardWorkflowController(bot)
    controller.state.script_data = {
        "title": "QC search test",
        "script": [
            {
                "primary_entity": "Shafali Verma",
                "factual_primary_entity": "Shafali Verma",
                "voiceover": "Shafali Verma is batting.",
                "sport_or_topic_category": "cricket",
                "visual_genre": "PERSON_ACTION",
            }
        ],
    }
    controller._visual_packages = [[{
        "image": str(current),
        "source_type": "Commons",
        "visual_verified": True,
    }]]
    controller.update("visual_approval", 76, "Visuals ready.")

    captured_used_hashes = []

    def fake_collect(*args, **kwargs):
        captured_used_hashes.append(set(kwargs.get("used_hashes") or set()))
        return {
            "assets": [
                {
                    "bytes": b"image-bytes",
                    "hash": item["hash"],
                    "source": item["source"],
                    "query": item["query"],
                    "visual_type": item["visual_type"],
                    "visual_genre": item["visual_genre"],
                    "provenance": item["provenance"],
                    "status": item["status"],
                }
                for item in options
            ],
            "target": 10,
            "hard_max": 10,
            "minimum_options": 0,
            "available_options": 3,
            "enough_options": True,
        }

    monkeypatch.setattr(
        visual_retrieval_runtime,
        "collect_manual_visual_options",
        fake_collect,
    )
    monkeypatch.setattr(
        visual_retrieval_runtime,
        "materialize_manual_visual_pool",
        lambda *args, **kwargs: [dict(item) for item in options],
    )

    ok, message = controller.search_visual_options(1, "Shafali Verma batting")

    assert ok is True
    assert "3 AI-checked" in message
    snapshot = controller.snapshot()
    stored = snapshot["visual_packages"][0][0]
    assert stored["image"] == str(current)
    assert len(stored["visual_search_options"]) == 3
    assert [item["hash"] for item in stored["visual_search_options"]] == [
        "hash-1",
        "hash-2",
        "hash-3",
    ]

    # A second search must exclude the three choices already displayed.
    ok, _ = controller.search_visual_options(1, "Shafali Verma century")
    assert ok is True
    assert {"hash-1", "hash-2", "hash-3"}.issubset(captured_used_hashes[-1])
    assert snapshot["visual_packages"][0][0]["image"] == str(current)

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
            "event_evidence": [
                {
                    "publishedAt": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
                },
                {
                    "publishedAt": (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
                },
            ],
            "velocity_score": 5.0,
            "trend_bonus": 2.0,
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
        broad_discovery=False,
    ):
        assert broad_discovery is True
        return list(topics), []

    monkeypatch.setattr(
        story_ranker,
        "collect_high_recall_stories",
        fake_collect_high_recall_stories,
    )
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



def test_dashboard_visual_review_keeps_missing_slots_visible_and_blocked():
    source = Path("app.py").read_text(encoding="utf-8")
    assert '"missing": missing' in source
    assert '"qc_passed": verified and not missing and not bool(layer.get("visual_qc_blocked", False))' in source
    assert 'if item.get("missing"):' in source

def test_dashboard_primary_menu_and_generated_outputs_contract():
    app_source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

    assert 'mode_labels = ["Deep Dive", "Top 5", "Cricket", "AI"]' in app_source
    assert '["Live Factory", "Channel Statistics", "Run Offline Diagnostics", "Demo Factory"]' not in app_source
    assert 'def render_generated_outputs(snapshot: Dict[str, Any]) -> None:' in app_source
    assert 'render_generated_outputs(snapshot)' in app_source
    assert 'def render_script_visual_query_review(' in app_source
    assert 'visual_search_queries' in app_source
    assert 'assign_manual_queries' not in app_source
    assert '"qc_passed": verified and not missing' in app_source
    assert 'disabled=bool(sum(1 for item in items if not item.get("qc_passed")))' in app_source
    assert 'Choose from the visual pool' in app_source
    assert 'NEEDS ATTENTION' not in app_source


def test_dashboard_ai_discovery_uses_shared_broad_radar():
    source = Path(__file__).resolve().parents[1].joinpath("dashboard_runtime.py").read_text(encoding="utf-8")
    assert "collect_high_recall_stories(" in source
    assert "custom_gnews_q=requested_topic or None" in source
    assert "broad_discovery=True" in source
    assert "_cheap_filter(raw, max_items=120, max_age_hours=48)" in source
    assert "_infer_discovery_category(item)" in source
    assert "diversity_rerank(ranked, max_items=max_candidates)" in source
    assert "category inferred after discovery, not used as an intake gate." in source


def test_dashboard_manual_crop_returns_shorts_frame():
    from PIL import Image
    from dashboard_runtime import _manual_crop_to_shorts

    image = Image.new("RGB", (2000, 1000), (100, 120, 140))
    cropped = _manual_crop_to_shorts(image, zoom=1.8, x_center=0.75, y_center=0.5)

    assert cropped.size == (1080, 1920)


def test_dashboard_visual_review_exposes_manual_pool_and_crop_modal_controls():
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

    assert "Choose from the visual pool" in source
    assert "Available verified images" in source
    assert "Available verified images" in source
    assert "Search up to 10 new images" in source
    assert '@st.dialog("Crop / reframe selected image", width="large")' in source
    assert 'st.session_state["visual_crop_target"]' in source
    assert "Apply crop" in source
    assert "controller.crop_visual(" in source
    assert "controller.crop_visual_pool_asset(" in source
    assert 'aspect_ratio=(9, 16) if crop_is_shorts else None' in source
    assert 'crop_mode=mode_value' in source
    assert 'return_type="both"' in source
    assert 'should_resize_image=False' in source
    assert "Use on slide" in source
    assert "Crop / reframe selected image" in source


def test_repository_does_not_use_deprecated_streamlit_container_width():
    repo_root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in repo_root.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        deprecated_arg = "use_container_" + "width"
        if deprecated_arg in source:
            offenders.append(str(path.relative_to(repo_root)))
    assert offenders == []



def test_dashboard_visual_pool_assignment_locks_image_to_one_slide(monkeypatch, tmp_path):
    from PIL import Image
    from dashboard_runtime import DashboardWorkflowController

    bot = _Bot()
    bot.ASSETS_DIR = str(tmp_path)
    bot.LANGUAGES = {"english": {"font": "arial.ttf"}}
    bot._active_web_config = {"format_mode": "regular", "language": "english"}

    current_paths = []
    for index in (1, 2):
        path = tmp_path / f"current_{index}.jpg"
        Image.new("RGB", (1080, 1920), "white").save(path, "JPEG")
        current_paths.append(path)

    option = tmp_path / "option.jpg"
    Image.new("RGB", (900, 1200), (80, 100, 120)).save(option, "JPEG")

    controller = DashboardWorkflowController(bot)
    controller.state.script_data = {
        "title": "Pool assignment",
        "script": [
            {"primary_entity": "Virat Kohli", "voiceover": "One."},
            {"primary_entity": "BCCI", "voiceover": "Two."},
        ],
    }
    controller._visual_packages = [
        [{"image": str(current_paths[0]), "visual_verified": True}],
        [{"image": str(current_paths[1]), "visual_verified": True}],
    ]
    controller._visual_pool = [{
        "path": str(option),
        "hash": "pool-hash",
        "source": "Commons",
        "query": "Virat Kohli",
        "visual_type": "PERSON",
        "visual_genre": "PERSON_PORTRAIT",
        "provenance": {"url": "https://commons.wikimedia.org/wiki/File:Kohli.jpg"},
        "status": "entity-verified",
        "used": False,
    }]

    monkeypatch.setattr(
        controller,
        "replace_visual_from_bank",
        lambda index, bank_index: (
            controller._visual_packages[index - 1].__setitem__(
                0,
                {
                    **controller._visual_packages[index - 1][0],
                    "image": str(option),
                    "visual_verified": True,
                },
            )
            or (True, "ok")
        ),
    )
    controller.update("visual_approval", 76, "Visuals ready.")

    ok, _ = controller.assign_visual_pool_asset("pool-hash", 2)
    assert ok is True
    assert controller._visual_pool[0]["used"] is True
    assert controller._visual_pool[0]["assigned_slide"] == 2
    assert controller._visual_packages[1][0]["visual_original_path"] == str(option)

    ok, message = controller.assign_visual_pool_asset("pool-hash", 1)
    assert ok is False
    assert "already assigned to slide 2" in message


def test_dashboard_new_visual_search_uses_ten_image_contract(monkeypatch, tmp_path):
    from dashboard_runtime import DashboardWorkflowController

    bot = _Bot()
    bot.ASSETS_DIR = str(tmp_path)
    controller = DashboardWorkflowController(bot)
    controller.state.script_data = {
        "title": "Search test",
        "script": [{"primary_entity": "BCCI", "voiceover": "One."}],
    }
    controller._visual_packages = [[{
        "image": str(tmp_path / "current.jpg"),
        "visual_verified": True,
        "source_image_url": "https://img.example/current.jpg",
    }]]
    controller.update("visual_approval", 76, "Visuals ready.")

    import visual_retrieval_runtime

    monkeypatch.setattr(
        visual_retrieval_runtime,
        "collect_manual_visual_search",
        lambda *args, **kwargs: {
            "assets": [
                {
                    "bytes": b"candidate",
                    "hash": f"h{i}",
                    "source": "Commons",
                    "query": "BCCI logo",
                    "visual_type": "ORGANIZATION",
                    "visual_genre": "ORG_BRANDING",
                    "provenance": {"url": f"https://commons.wikimedia.org/wiki/File:BCCI_{i}.jpg"},
                    "source_image_url": f"https://commons.wikimedia.org/thumb/BCCI_{i}.jpg",
                    "status": "new-search",
                }
                for i in range(1, 6)
            ],
            "target": 10,
        },
    )
    materialized = [
        {
            "path": str(tmp_path / f"candidate_{i}.jpg"),
            "hash": f"h{i}",
            "source": "Commons",
            "query": "BCCI logo",
            "visual_type": "ORGANIZATION",
            "visual_genre": "ORG_BRANDING",
            "provenance": {"url": f"https://commons.wikimedia.org/wiki/File:BCCI_{i}.jpg"},
            "source_image_url": f"https://commons.wikimedia.org/thumb/BCCI_{i}.jpg",
            "status": "new-search",
            "used": False,
        }
        for i in range(1, 6)
    ]
    monkeypatch.setattr(visual_retrieval_runtime, "materialize_manual_visual_pool", lambda *args, **kwargs: materialized)

    ok, message = controller.search_visual_pool("BCCI logo")
    assert ok is True
    assert "Found 5 new" in message
    snapshot = controller.snapshot()
    assert len(snapshot["visual_search_groups"]) == 1
    assert len(snapshot["visual_search_groups"][0]["items"]) == 5



def test_dashboard_has_collapsible_live_powershell_widget():
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

    assert "def render_powershell_widget(snapshot: Dict[str, Any]) -> None:" in source
    assert 'with st.sidebar:' in source
    assert 'st.expander(f"🖥️ PowerShell · {status}"' in source
    assert 'st.code("\\n".join(visible), language="powershell")' in source
    assert 'render_powershell_widget(live_snapshot)' in source
    assert 'render_powershell_output(' not in source


def test_dashboard_contains_generated_text_safety_and_overflow_guards():
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")

    assert 'def _ui_text(value: Any, fallback: str = "") -> str:' in source
    assert 'def _ui_html(value: Any, fallback: str = "") -> str:' in source
    assert '_arrow(?:_(?:right|left|up|down))?' in source
    assert 'overflow-wrap:anywhere' in source
    assert 'word-break:break-word' in source
    assert "f\"<div class='story-title'>{_ui_html(title)}</div>\"" in source
    assert "with st.expander(\"Why this story\", expanded=False)" in source
    assert "section[data-testid=\"stSidebar\"]{" in source


def test_dashboard_css_never_overrides_streamlit_icon_font():
    """Global dashboard typography must not steal Streamlit's icon ligatures."""
    import re

    repo_root = Path(__file__).resolve().parents[1]
    css_sources = [
        repo_root.joinpath("app.py").read_text(encoding="utf-8"),
        repo_root.joinpath("dashboard_theme.py").read_text(encoding="utf-8"),
    ]

    css_blocks = []
    for source in css_sources:
        css_blocks.extend(re.findall(r"<style>(.*?)</style>", source, flags=re.DOTALL))

    assert css_blocks, "Expected injected dashboard CSS"

    unsafe_selectors = []
    font_rule_re = re.compile(
        r"(?P<selectors>[^{}]+)\{(?P<body>[^{}]*font-family\s*:[^{}]+)\}",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for css in css_blocks:
        for match in font_rule_re.finditer(css):
            selectors = match.group("selectors").strip()
            if "[class*=\"css\"]" in selectors:
                unsafe_selectors.append(selectors)
                continue
            if re.search(r"(?<![\w-])\*(?![\w-])", selectors):
                unsafe_selectors.append(selectors)
                continue
            if re.search(r"(?<![\w-])span(?![\w-])", selectors, flags=re.IGNORECASE):
                icon_selector = (
                    "stIconMaterial" in selectors
                    or "stExpanderToggleIcon" in selectors
                    or "span[class*=\"material\"]" in selectors
                )
                if not icon_selector:
                    unsafe_selectors.append(selectors)

    assert unsafe_selectors == []


def test_final_artifact_qc_export_is_available():
    """Upload QC must have the artifact validator expected by final_qc_runtime."""
    from branding_runtime import _artifact_qc

    passed, detail = _artifact_qc("")
    assert passed is False
    assert "missing" in detail.lower()


def test_dashboard_upload_choices_remain_visible_before_metadata_approval():
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    start = source.index("def render_upload_panel")
    end = source.index("\ndef _perform_upload", start)
    panel = source[start:end]
    assert "private_ready = metadata_approved and qc_ready" in panel
    assert "public_ready = private_ready and not public_blocked" in panel
    assert 'if not metadata_approved:' in panel
    assert 'st.info("Approve metadata above to unlock upload.")' in panel
    assert "return" not in panel.split('if not metadata_approved:', 1)[1].split('if metadata_approved:', 1)[0]


def test_public_release_policy_cannot_be_bypassed_by_ui():
    source = Path(__file__).resolve().parents[1].joinpath("workflow_runtime.py").read_text(encoding="utf-8")
    assert 'if str(publish_mode or "").strip().lower() == "public":' in source
    assert 'if bool(gate.get("public_blocked"))' in source


def test_dashboard_progress_uses_latest_known_progress_line():
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert "for latest in reversed(lines[-100:]):" in source
    assert 'operation_label = "YouTube upload"' in source
    assert 'operation_label = "Final video render"' in source


def test_dashboard_output_summary_reports_visual_qc_readiness():
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert 'ready_visuals = sum(1 for item in visuals if item.get("qc_passed"))' in source
    assert 'f"{ready_visuals}/{len(visuals)} ready"' in source


def test_dashboard_crop_editor_has_free_rectangle_mode_and_full_source():
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert '"Rectangle (free)"' in source
    assert 'crop_asset.get("original_path")' in source
    assert 'aspect_ratio=(9, 16) if crop_is_shorts else None' in source


def test_dashboard_free_crop_accepts_non_916_selection(tmp_path):
    from PIL import Image
    from dashboard_runtime import _manual_crop_box_to_shorts

    source = Image.new("RGB", (1600, 1000), (20, 80, 140))
    cropped = _manual_crop_box_to_shorts(
        source,
        {"left": 100, "top": 100, "width": 1000, "height": 400},
        free_size=True,
    )
    assert cropped.size == (1080, 1920)


def test_dashboard_controller_free_crop_writes_renderable_output(tmp_path):
    from PIL import Image
    from dashboard_runtime import DashboardWorkflowController

    source_path = tmp_path / "original.jpg"
    Image.new("RGB", (1600, 1000), (40, 90, 130)).save(source_path, "JPEG")

    bot = _Bot()
    bot.ASSETS_DIR = str(tmp_path)
    bot.LANGUAGES = {"english": {"font": "arial.ttf"}}
    bot._active_web_config = {"format_mode": "regular", "language": "english"}

    controller = DashboardWorkflowController(bot)
    controller.state.script_data = {
        "title": "Free crop",
        "script": [{"voiceover": "A test scene.", "primary_entity": "Subject"}],
    }
    controller._visual_packages = [[{
        "image": str(source_path),
        "visual_original_path": str(source_path),
        "visual_verified": True,
    }]]
    controller.update("visual_approval", 76, "Visuals ready.")

    ok, message = controller.crop_visual(
        1,
        crop_box={"left": 100, "top": 100, "width": 1000, "height": 400},
        crop_mode="free",
    )
    assert ok is True, message

    layer = controller._visual_packages[0][0]
    output_path = Path(layer["image"])
    assert output_path.is_file()
    assert Image.open(output_path).size == (1080, 1920)
    assert layer["visual_crop_mode"] == "free"
    assert Path(layer["visual_original_path"]) == source_path


def test_upload_panel_keeps_public_private_controls_and_comment_override_path():
    app_source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    workflow_source = Path(__file__).resolve().parents[1].joinpath("workflow_runtime.py").read_text(encoding="utf-8")
    uploader_source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert 'key="upload_public"' in app_source
    assert 'key="upload_private"' in app_source
    assert 'publish_mode' in workflow_source
    assert 'comment_override=final_comment' in workflow_source
    assert 'youtube.commentThreads().insert' in uploader_source
    assert 'if privacy == "public":' in uploader_source


def test_dashboard_header_does_not_render_empty_top_band():
    source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert '[data-testid="stHeader"]{background:transparent;border-bottom:none}' in source
