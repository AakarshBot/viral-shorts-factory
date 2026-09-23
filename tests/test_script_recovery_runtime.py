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
        "specific_search_prompt": "India cricket latest development",
        "sport_or_topic_category": "Cricket",
    }


def test_compact_two_scene_story_can_pass():
    script = {
        "editorial_angle": "Explain the confirmed event and immediate consequence.",
        "script": [
            _scene("India confirms a major squad change.", "hook"),
            _scene(
                "The decision changes preparation for the next assignment and affects the replacement's role.",
                "consequence",
            ),
        ],
    }
    valid, reason = validate_content_density(script, {}, "regular")
    assert valid, reason


def test_four_scene_compact_story_passes():
    script = {
        "editorial_angle": "Explain what changed, the key evidence and the immediate consequence.",
        "script": [
            _scene("India confirms a major squad change.", "hook"),
            _scene("Officials say the decision followed the latest review.", "development"),
            _scene("The change affects preparation for the next assignment.", "context"),
            _scene("The replacement now takes a different role.", "consequence"),
        ],
    }
    valid, reason = validate_content_density(script, {}, "regular")
    assert valid, reason

    assessment = assess_narrative_completeness(script)
    assert assessment["passed"] is True


def test_overlong_story_is_rejected_by_word_budget():
    script = {
        "editorial_angle": "A compact explanation of the selected development.",
        "script": [
            _scene("India confirms a major squad change.", "hook"),
            _scene(" ".join(["word"] * 30), "development"),
            _scene(" ".join(["word"] * 30), "consequence"),
        ],
    }
    valid, reason = validate_content_density(script, {}, "regular")
    assert valid is False
    assert "maximum is 65" in reason


def test_scene_one_must_be_shorter_than_following_scenes():
    script = {
        "editorial_angle": "Explain the event and consequence.",
        "script": [
            _scene(" ".join(["word"] * 12), "hook"),
            _scene(" ".join(["word"] * 10), "development"),
        ],
    }
    valid, reason = validate_content_density(script, {}, "regular")
    assert valid is False
    assert "Scene 1 must remain strictly shorter" in reason


def test_retention_bait_is_explicitly_rejected():
    banned = "Wait till the end to find out what happened."
    assert contains_retention_bait(banned)

    script = {
        "editorial_angle": "Explain the development without withholding information.",
        "script": [
            _scene("India confirms a major squad change.", "hook"),
            _scene("Wait till the end to find out what happened.", "consequence"),
        ],
    }
    valid, reason = validate_content_density(script, {}, "regular")
    assert valid is False
    assert "retention" in reason.lower()


def test_source_fallback_refuses_thin_evidence_instead_of_padding():
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
