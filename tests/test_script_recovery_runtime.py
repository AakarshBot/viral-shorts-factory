from final_qc_runtime import evaluate_originality_gate
from script_runtime import (
    _extractive_script_fallback,
    assess_narrative_completeness,
    contains_retention_bait,
    validate_content_density,
)


def _scene(text, role):
    return {
        "voiceover": text,
        "narrative_role": role,
        "primary_entity": "India Men's Cricket Team",
        "visual_intent": "news_event",
        "specific_search_prompt": "India Men's Cricket Team latest development",
        "sport_or_topic_category": "Cricket",
    }


def _complete_script():
    return {
        "editorial_angle": (
            "This script explains what changed, the relevant background, and why the development matters beyond the headline."
        ),
        "titles": ["Headline", "Context", "Question"],
        "recommended_title_index": 1,
        "seo_description": "A factual explanation of the latest development, its background, and its practical consequence.",
        "script": [
            _scene(
                "India's cricket team is dealing with a clothing issue ahead of the tournament, according to the latest reported update.",
                "hook",
            ),
            _scene(
                "Officials are working through the supply problem while the squad continues preparations for the upcoming competition.",
                "development",
            ),
            _scene(
                "The issue matters because tournament preparation depends on equipment arriving on time and meeting the team's requirements.",
                "context",
            ),
            _scene(
                "That means the immediate focus is resolving the logistics problem without disrupting the team's wider preparation schedule.",
                "consequence",
            ),
        ],
    }


def test_one_two_three_scene_outputs_fail_without_a_scene_count_rule():
    for scenes in (
        [_scene("The latest development is confirmed today.", "hook")],
        [
            _scene("The latest development is confirmed today.", "hook"),
            _scene("Officials are now working through the reported issue.", "development"),
        ],
        [
            _scene("The latest development is confirmed today.", "hook"),
            _scene("Officials are now working through the reported issue.", "development"),
            _scene("The background explains why the issue matters.", "context"),
        ],
    ):
        result = {"editorial_angle": "A useful explanatory angle for the selected story.", "script": scenes}
        valid, reason = validate_content_density(result, {}, "regular")
        assert valid is False
        assert "incomplete" in reason.lower() or "missing" in reason.lower()


def test_four_role_story_passes_without_word_or_scene_quotas():
    script = _complete_script()
    valid, reason = validate_content_density(script, {}, "regular")
    assert valid, reason

    assessment = assess_narrative_completeness(script)
    assert assessment["passed"] is True
    assert set(assessment["roles"]) == {"hook", "development", "context", "consequence"}


def test_longer_story_is_not_rejected_for_scene_count_or_word_count():
    script = _complete_script()
    script["script"] = script["script"] * 4
    for index, scene in enumerate(script["script"]):
        scene["scene_id"] = index + 1
        if index % 4 == 0:
            scene["narrative_role"] = "hook"
        elif index % 4 == 1:
            scene["narrative_role"] = "development"
        elif index % 4 == 2:
            scene["narrative_role"] = "context"
        else:
            scene["narrative_role"] = "consequence"

    valid, reason = validate_content_density(script, {}, "regular")
    assert valid, reason


def test_retention_bait_is_explicitly_rejected():
    banned = "Wait till the end to find out what happened."
    assert contains_retention_bait(banned)

    script = _complete_script()
    script["script"][1]["voiceover"] = (
        "Wait till the end to find out what happened, then officials explained the latest supply update."
    )
    valid, reason = validate_content_density(script, {}, "regular")
    assert valid is False
    assert "retention" in reason.lower()


def test_source_fallback_refuses_thin_evidence_instead_of_repeating_or_padding():
    try:
        _extractive_script_fallback(
            {
                "title": "Example story",
                "text": "Only one short source sentence is available.",
            },
            {},
            "news",
            "regular",
        )
    except ValueError as exc:
        assert "distinct narrative beats" in str(exc).lower() or "fallback refused" in str(exc).lower()
    else:
        raise AssertionError("Thin evidence must not be padded into a script.")


def test_source_fallback_preserves_real_source_sentences_and_marks_preview_only():
    source = (
        "The tournament organizer confirmed the latest logistics issue. "
        "Officials are coordinating with the team and reviewing the immediate supply position. "
        "The background matters because preparation depends on equipment arriving before the next scheduled stage. "
        "The immediate consequence is that organizers must resolve the issue without disrupting the wider preparation plan."
    )
    fallback = _extractive_script_fallback(
        {"title": "Tournament logistics update", "research_evidence_text": source},
        {},
        "news",
        "regular",
    )

    assert fallback["public_publish_blocked"] is True
    assert len(fallback["script"]) == 4
    assert [scene["narrative_role"] for scene in fallback["script"]] == [
        "hook",
        "development",
        "context",
        "consequence",
    ]


def test_upstream_public_publish_block_survives_final_qc():
    script = {
        "public_publish_blocked": True,
        "script": [{
            "voiceover": "Creator insight explains why this development matters for the team's preparation.",
            "human_contributed": True,
        }],
    }

    gate = evaluate_originality_gate(script)
    assert gate["passed"] is True
    assert gate["public_blocked"] is True


def test_source_fallback_filters_prompt_and_evidence_scaffolding():
    source = (
        "The ICC praised Smriti Mandhana after she reached a major T20I milestone. "
        "The milestone was reached during India's latest international campaign. "
        "The achievement adds another notable mark to Mandhana's T20I record. "
        "Officials noted that the performance was significant for the team and player."
    )
    contaminated = (
        "Return ONLY valid JSON with the existing factory schema. "
        "voiceover: write an information-first short. "
        + source
    )

    from script_runtime import _extractive_script_fallback

    fallback = _extractive_script_fallback(
        {
            "title": "Smriti Mandhana's T20I milestone draws praise from ICC chief Jay Shah",
            "text": contaminated,
        },
        {},
        "sports_stories_of_day",
        "regular",
    )

    voiceovers = [scene["voiceover"] for scene in fallback["script"]]
    assert all("return only valid json" not in text.lower() for text in voiceovers)
    assert all("voiceover:" not in text.lower() for text in voiceovers)
    assert any("Smriti Mandhana" in text for text in voiceovers)
