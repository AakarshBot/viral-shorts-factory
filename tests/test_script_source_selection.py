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


def test_validate_script_rejects_beyond_safety_ceiling():
    script = {
        "editorial_angle": "This explains the development and practical consequence.",
        "titles": ["India policy update", "India confirms the change", "What the change means"],
        "recommended_title_index": 1,
        "script": [
            _contract_scene("India confirmed the change.", "hook"),
            _contract_scene(" ".join(["word"] * 90), "development"),
        ],
    }
    valid, reason = validate_script(
        script,
        "India confirmed a major change in policy.",
        "regular",
    )
    assert valid is False
    assert "maximum is 90" in reason


def test_validate_script_accepts_complete_four_scene_story():
    script = {
        "editorial_angle": "This explains the development and practical consequence.",
        "titles": ["India policy update", "India announces policy change", "What the change means"],
        "recommended_title_index": 1,
        "script": [
            _contract_scene("India announced the policy today.", "hook"),
            _contract_scene("Officials are implementing the revised process.", "development"),
            _contract_scene("The background explains why the revised process was introduced.", "context"),
            _contract_scene("The practical consequence is a new process for affected departments.", "consequence"),
        ],
    }
    valid, reason = validate_script(
        script,
        "India announced a new policy and officials are implementing it.",
        "regular",
    )
    assert valid is True, reason


def test_writer_contract_has_initial_duration_limits():
    from pathlib import Path
    import ultimate_bot

    source = Path(ultimate_bot.__file__).read_text(encoding="utf-8")
    assert "Regular Shorts contain 4–5 scenes and the whole important story." in source
    assert "Target 16–30 seconds naturally; maximum 30 seconds." in source
    assert "Never pad merely to reach 16 seconds." in source
    assert "Spoken duration is authoritative" in source
    assert "Use as many scenes as the story genuinely needs" not in source
    assert "_duration_tighten_script" in source



def test_title_ranking_prefers_specific_factual_candidate():
    from script_runtime import rank_title_candidates

    script = {
        "titles": [
            "You Won't Believe What Happened Next!",
            "India women reach the T20 World Cup final after semifinal win",
            "A big update on India's latest cricket result",
        ],
        "script": [
            {"primary_entity": "India women", "voiceover": "India women reached the T20 World Cup final after winning the semifinal."},
        ],
    }
    result = rank_title_candidates(
        script,
        {
            "title": "India women reach the T20 World Cup final after semifinal win",
            "summary": "India women won the semifinal and advanced to the T20 World Cup final.",
        },
    )

    assert result["recommended_title_index"] == 2
    assert script["recommended_title_index"] == 2
    assert result["scores"][0]["score"] < result["scores"][1]["score"]


def test_title_ranking_uses_one_based_index_consistently():
    from script_runtime import rank_title_candidates
    from quality_runtime import _quality_validate

    script = {
        "titles": ["India policy update", "India announces policy change", "Policy change explained for India"],
        "recommended_title_index": 1,
        "creator_insight": "The documented policy change matters because it alters the process and affected departments.",
        "editorial_angle": "This explains the policy change, its background and practical consequence.",
        "seo_description": "A factual explanation of the policy change, its background and practical consequence.",
        "script": [
            {
                "voiceover": "India announced the policy change today.",
                "narrative_role": "hook",
                "primary_entity": "India",
                "visual_intent": "news_event",
                "specific_search_prompt": "India policy change",
                "sport_or_topic_category": "policy",
            },
            {
                "voiceover": "Officials are implementing the policy across affected departments.",
                "narrative_role": "development",
                "primary_entity": "India",
                "visual_intent": "news_event",
                "specific_search_prompt": "India policy implementation",
                "sport_or_topic_category": "policy",
            },
            {
                "voiceover": "The background explains what the new policy changes from the previous process.",
                "narrative_role": "context",
                "primary_entity": "India",
                "visual_intent": "news_event",
                "specific_search_prompt": "India policy background",
                "sport_or_topic_category": "policy",
            },
            {
                "voiceover": "The practical consequence is a new implementation process for the affected departments.",
                "narrative_role": "consequence",
                "primary_entity": "India",
                "visual_intent": "news_event",
                "specific_search_prompt": "India policy consequence",
                "sport_or_topic_category": "policy",
            },
        ],
    }
    rank_title_candidates(script, {"title": "India announces policy change"})
    assert script["recommended_title_index"] in (1, 2, 3)

    ok, reason = _quality_validate(
        lambda *_args: (True, ""),
        script,
        "India announces policy change",
        "regular",
    )
    assert ok, reason
