from ultimate_bot import validate_script
import json

from research_runtime import _prepare_primary_writer_data


def test_cricket_mode_preserves_selected_story_evidence_for_legacy_writer():
    story = {
        "title": "Pakistan hit with dual World Test Championship sanction - ICC",
        "text": "Pakistan were sanctioned twice in the World Test Championship after the latest ICC decision. The penalties affect the team's points position and competition campaign.",
        "research_bundle": "SOURCE 1 Reported detail: The ICC recorded two sanctions affecting Pakistan's championship campaign.",
    }

    prepared = _prepare_primary_writer_data(story, "cricket")

    assert prepared is not story
    assert prepared["text"] != story["text"]
    payload = json.loads(prepared["text"])
    assert isinstance(payload, list) and len(payload) == 1
    assert payload[0]["title"] == story["title"]
    assert "Pakistan were sanctioned twice" in payload[0]["text"]
    assert "World Test Championship" in payload[0]["text"]
    assert "SOURCE 1" in payload[0]["text"]


def test_non_cricket_modes_keep_original_story_payload():
    story = {
        "title": "Example story",
        "text": "Original article body",
    }

    prepared = _prepare_primary_writer_data(story, "regular")

    assert prepared["text"] == story["text"]


def test_primary_script_validator_rejects_thin_five_scene_output():
    scenes = [
        {
            "voiceover": "India team faces delay today.",
            "primary_entity": "India Men's Cricket Team",
            "visual_intent": "news_event",
            "specific_search_prompt": "India Men's Cricket Team",
            "sport_or_topic_category": "Cricket",
        }
        for _ in range(5)
    ]
    valid, reason = validate_script(
        {
            "editorial_angle": "This explains the confirmed development and why it matters to the team's preparation.",
            "script": scenes,
        },
        "India team faces clothing delay before the Asian Games.",
        "regular",
    )
    assert valid is False
    assert "6" in reason or "12" in reason


def test_primary_script_validator_accepts_six_substantive_scenes_without_bridge_keyword_rule():
    scenes = [
        {
            "voiceover": (
                "The latest development affects the team's preparation, while officials work through the immediate issue and organizers assess the wider implications."
            ),
            "primary_entity": "India Men's Cricket Team",
            "visual_intent": "news_event",
            "specific_search_prompt": "India Men's Cricket Team",
            "sport_or_topic_category": "Cricket",
        }
        for _ in range(6)
    ]
    valid, reason = validate_script(
        {
            "editorial_angle": (
                "The script adds context about the practical consequence rather than simply repeating the source article's sequence."
            ),
            "script": scenes,
        },
        "India team faces clothing delay before the Asian Games.",
        "regular",
    )
    assert valid is True, reason
