import threading
import time

from dashboard_runtime import DashboardWorkflowController


def test_dashboard_manual_script_gate_is_core_and_waits():
    controller = DashboardWorkflowController(object())
    config = controller._prepare_production_config({})
    assert config["_dashboard_manual_control"] is True
    assert callable(config["_manual_script_review_hook"])

    script = {
        "title": "Test",
        "script": [
            {
                "voiceover": "A factual first scene with enough words for the review contract.",
                "primary_entity": "India",
                "visual_intent": "team",
                "specific_search_prompt": "India",
                "sport_or_topic_category": "sports",
            },
            {
                "voiceover": "A factual second scene with enough words for the review contract.",
                "primary_entity": "India",
                "visual_intent": "team",
                "specific_search_prompt": "India",
                "sport_or_topic_category": "sports",
            },
            {
                "voiceover": "A factual third scene with enough words for the review contract.",
                "primary_entity": "India",
                "visual_intent": "team",
                "specific_search_prompt": "India",
                "sport_or_topic_category": "sports",
            },
        ],
    }

    result = {}

    def worker():
        result["value"] = config["_manual_script_review_hook"](script)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    for _ in range(100):
        if controller.snapshot()["stage"] == "script_review":
            break
        time.sleep(0.01)

    assert thread.is_alive()
    assert controller.submit_script_visual_queries(
        ["India cricket team", "", "India cricket team"],
    )

    thread.join(timeout=2)
    assert not thread.is_alive()
    assert "value" in result
    assert len(result["value"]["script"]) == 3
    assert result["value"]["script"][0]["manual_visual_query"] == "India cricket team"
    assert "human_contributed" not in result["value"]["script"][-1]


def test_dashboard_manual_visual_gate_is_core_and_blocks_render_until_approved(tmp_path):
    controller = DashboardWorkflowController(object())
    config = controller._prepare_production_config({})

    image_path = tmp_path / "scene.jpg"
    image_path.write_bytes(b"image")
    packages = [[{"image": str(image_path), "visual_verified": True, "visual_qc_blocked": False}]]
    result = {}

    def worker():
        result["value"] = config["_manual_visual_review_hook"](packages)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    for _ in range(100):
        if controller.snapshot()["stage"] == "visual_approval":
            break
        time.sleep(0.01)

    assert thread.is_alive()
    assert controller.snapshot()["visual_review_required"] is True
    assert controller.approve_visual(1)[0] is True
    assert controller.approve_visuals() is True

    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result["value"][0][0]["human_visual_approved"] is True

def test_manual_script_review_can_correct_ambiguous_entity_before_audio():
    controller = DashboardWorkflowController(type("Bot", (), {
        "PERSONA_PROFILES": {"LISTICLE HOST": {"gender": "male", "rate": "+0%", "pitch": "+0Hz"}},
    })())
    controller.state.script_data = {
        "title": "Rinku Singh story",
        "titles": ["Rinku Singh story", "Rinku Singh cricket update", "Rinku Singh latest"],
        "recommended_title_index": 1,
        "script": [
            {"voiceover": "Rinku Singh featured in the latest cricket development.", "narrative_role": "hook", "primary_entity": "Rinku Singh", "specific_search_prompt": "Rinku Singh latest", "visual_intent": "person_action"},
            {"voiceover": "The latest announcement changes the team's immediate plans.", "narrative_role": "development", "primary_entity": "India cricket team", "specific_search_prompt": "India cricket team plans", "visual_intent": "news_event"},
            {"voiceover": "The background explains the decision and its context.", "narrative_role": "context", "primary_entity": "India cricket team", "specific_search_prompt": "India cricket team context", "visual_intent": "news_event"},
            {"voiceover": "The consequence affects the team's next assignment.", "narrative_role": "consequence", "primary_entity": "India cricket team", "specific_search_prompt": "India cricket team assignment", "visual_intent": "news_event"},
        ],
    }
    controller.update("script_review", 40, "review")
    controller._ensure_manual_gate_state()

    reviewed = {
        **controller.state.script_data,
        "script": [dict(scene) for scene in controller.state.script_data["script"]],
    }
    reviewed["script"][0]["primary_entity"] = "Rinku Singh (cricketer)"
    reviewed["script"][0]["specific_search_prompt"] = "Rinku Singh cricketer batting India"

    ok, message = controller.submit_script_review(reviewed)
    assert ok, message
    approved = controller.snapshot()["script_data"]["script"][0]
    assert approved["primary_entity"] == "Rinku Singh (cricketer)"
    assert approved["specific_search_prompt"] == "Rinku Singh cricketer batting India"
    assert approved["manual_visual_query"] == "Rinku Singh cricketer batting India"
    assert approved["visual_entity_grounding"] == "MANUAL_LOCK"

