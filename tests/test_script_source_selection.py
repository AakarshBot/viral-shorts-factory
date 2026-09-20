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


def _contract_scene(text, role):
    return {
        "voiceover": text,
        "narrative_role": role,
        "primary_entity": "India",
        "visual_intent": "news_event",
        "specific_search_prompt": "India latest policy",
        "sport_or_topic_category": "News",
    }


def test_validate_script_rejects_collapsed_narrative_without_scene_or_word_quota():
    script = {
        "editorial_angle": "This explains the development, its context, and the practical consequence.",
        "script": [
            _contract_scene("India announced the policy today.", "hook"),
            _contract_scene("Officials are implementing the policy.", "development"),
            _contract_scene("The background explains why the policy was introduced.", "context"),
        ],
    }
    valid, reason = validate_script(
        script,
        "India announced a new policy and officials are implementing it.",
        "regular",
    )
    assert valid is False
    assert "incomplete" in reason.lower() or "missing" in reason.lower()


def test_validate_script_accepts_complete_story_without_numeric_limits():
    script = {
        "editorial_angle": "This adds context and explains the practical consequence beyond the headline.",
        "script": [
            _contract_scene("India announced the policy today.", "hook"),
            _contract_scene("Officials are implementing the policy across the affected departments.", "development"),
            _contract_scene("The background explains why the change was introduced and what it replaces.", "context"),
            _contract_scene("The practical consequence is that departments must now prepare for the new process.", "consequence"),
        ],
    }
    valid, reason = validate_script(
        script,
        "India announced a new policy and officials are implementing it.",
        "regular",
    )
    assert valid is True, reason
