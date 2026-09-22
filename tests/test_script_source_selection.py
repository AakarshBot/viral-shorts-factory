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


def test_validate_script_rejects_one_or_two_scene_stubs():
    for scenes in (
        [_contract_scene("India announced the policy today.", "hook")],
        [
            _contract_scene("India announced the policy today.", "hook"),
            _contract_scene("Officials are implementing the policy.", "development"),
        ],
    ):
        script = {
            "editorial_angle": "This explains the development, its context, and the practical consequence.",
            "script": scenes,
        }
        valid, reason = validate_script(
            script,
            "India announced a new policy and officials are implementing it.",
            "regular",
        )
        assert valid is False
        assert "incomplete" in reason.lower() or "missing" in reason.lower()


def test_validate_script_accepts_compact_three_scene_story():
    script = {
        "editorial_angle": "This explains the development, its context, and the practical consequence.",
        "script": [
            _contract_scene("India announced the policy today.", "hook"),
            _contract_scene("Officials are implementing the policy and preparing the affected departments.", "development"),
            _contract_scene("The practical consequence is that departments must now prepare for the new process.", "consequence"),
        ],
    }
    valid, reason = validate_script(
        script,
        "India announced a new policy and officials are implementing it.",
        "regular",
    )
    assert valid is True, reason


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


def test_writer_contract_has_no_numeric_scene_or_word_limits():
    from pathlib import Path
    import ultimate_bot

    source = Path(ultimate_bot.__file__).read_text(encoding="utf-8")
    assert "Use as many scenes as the story genuinely needs" in source
    assert "there is no target scene count" not in source.lower()
    assert "target scene count" not in source.lower()
    assert "between 8 and 30 words" not in source.lower()
    assert "strictly between 5 and 8 scenes" not in source.lower()


def test_writer_contract_explicitly_bans_retention_bait():
    from pathlib import Path
    import ultimate_bot

    source = Path(ultimate_bot.__file__).read_text(encoding="utf-8").lower()
    for phrase in (
        "wait till the end",
        "wait until the end",
        "wait for it",
        "stay tuned",
        "you won't believe",
        "what happens next",
        "don't go anywhere",
    ):
        assert phrase in source



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
        "editorial_angle": "This explains the policy change, its background and practical consequence.",
        "seo_description": "A factual explanation of the policy change, its background and practical consequence.",
        "script": [
            {
                "voiceover": "India announced the policy change today.",
                "narrative_role": "hook",
                "primary_entity": "India",
                "specific_search_prompt": "India policy change",
            },
            {
                "voiceover": "Officials are implementing the policy across affected departments.",
                "narrative_role": "development",
                "primary_entity": "India",
                "specific_search_prompt": "India policy implementation",
            },
            {
                "voiceover": "The background explains what the new policy changes from the previous process.",
                "narrative_role": "context",
                "primary_entity": "India",
                "specific_search_prompt": "India policy background",
            },
            {
                "voiceover": "The practical consequence is a new implementation process for the affected departments.",
                "narrative_role": "consequence",
                "primary_entity": "India",
                "specific_search_prompt": "India policy consequence",
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
