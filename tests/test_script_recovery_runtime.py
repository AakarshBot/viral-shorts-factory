from script_runtime import (
    SCRIPT_MIN_SCENES,
    SCRIPT_MAX_SCENES,
    SCENE_MIN_WORDS,
    SCENE_MAX_WORDS,
    SCRIPT_MIN_TOTAL_WORDS,
    _extractive_script_fallback,
    repair_script_structure,
    validate_content_density,
)
from final_qc_runtime import evaluate_originality_gate


def _scene(text):
    return {
        "voiceover": text,
        "primary_entity": "India Men's Cricket Team",
        "visual_intent": "news_event",
        "specific_search_prompt": "India Men's Cricket Team",
        "sport_or_topic_category": "Cricket",
    }


def test_three_scene_script_can_be_repaired_without_inventing_text():
    script = {
        "script": [
            _scene(
                "India Men's Cricket Team faces a clothing delay before the Asian Games, while officials work through the supply issue and the squad continues detailed preparations for the tournament."
            ),
            _scene(
                "The delay concerns the team's playing clothing and has become a preparation issue ahead of the tournament, with officials reviewing the supply problem and its timing."
            ),
            _scene(
                "The squad continues tournament preparations while organizers address the clothing issue, review the latest position, and assess what the delay means for overall readiness."
            ),

        ]
    }

    repaired, diagnostics = repair_script_structure(script, "cricket")

    assert diagnostics["changed"] is True
    assert SCRIPT_MIN_SCENES <= len(repaired["script"]) <= SCRIPT_MAX_SCENES
    assert all(
        SCENE_MIN_WORDS <= len(scene["voiceover"].split()) <= SCENE_MAX_WORDS
        for scene in repaired["script"]
    )
    assert all(scene["primary_entity"] == "India Men's Cricket Team" for scene in repaired["script"])


def test_overlong_scene_list_is_merged_to_the_production_ceiling():
    script = {
        "script": [
            _scene(
                f"Scene {index} contains factual information about the selected cricket story, including the relevant development and current context for viewers."
            )
            for index in range(1, 10)
        ]
    }

    repaired, diagnostics = repair_script_structure(script, "cricket")

    assert diagnostics["changed"] is True
    assert len(repaired["script"]) == 8
    assert all(
        SCENE_MIN_WORDS <= len(scene["voiceover"].split()) <= SCENE_MAX_WORDS
        for scene in repaired["script"]
    )


def test_repair_refuses_to_duplicate_or_invent_when_evidence_is_too_short():
    script = {
        "script": [
            _scene("India team prepares for Asian Games today."),
            _scene("Officials discuss the clothing delay."),
            _scene("The squad continues tournament preparations."),
        ]
    }

    repaired, diagnostics = repair_script_structure(script, "cricket")

    assert repaired is None
    assert diagnostics["changed"] is False
    assert "safely" in diagnostics["reason"] or "word contract" in diagnostics["reason"]


def test_extractives_refuse_short_source_instead_of_repeating_title():
    try:
        _extractive_script_fallback(
            {
                "title": "India Men's Cricket Team faces clothing delay",
                "text": "Officials discuss a short delay before the tournament.",
            },
            {},
            "cricket",
            "regular",
        )
    except ValueError as exc:
        assert "usable source words" in str(exc)
    else:
        raise AssertionError("Thin source fallback must not manufacture renderable scenes.")


def test_extractives_are_substantive_and_publication_blocked():
    source = " ".join(
        [
            "India's cricket team is dealing with a clothing delay before the Asian Games.",
            "Officials are working through the supply issue while the squad continues preparation.",
            "The delay affects the team's preparations and organizers are reviewing the latest supply position.",
            "Players remain focused on the tournament while the issue is being resolved by officials.",
            "The latest update gives no indication that the overall campaign has been cancelled or suspended.",
            "Further decisions depend on the outcome of the ongoing discussions around the clothing supply.",
        ]
    )
    fallback = _extractive_script_fallback(
        {
            "title": "India Men's Cricket Team faces clothing delay",
            "research_evidence_text": source,
        },
        {},
        "cricket",
        "regular",
    )

    assert fallback["fallback_mode"] == "extractive_source_grounded"
    assert fallback["public_publish_blocked"] is True
    assert len(fallback["script"]) >= SCRIPT_MIN_SCENES
    assert all(
        SCENE_MIN_WORDS <= len(scene["voiceover"].split()) <= SCENE_MAX_WORDS
        for scene in fallback["script"]
    )
    assert sum(len(scene["voiceover"].split()) for scene in fallback["script"]) >= SCRIPT_MIN_TOTAL_WORDS


def test_content_density_rejects_five_tiny_scenes():
    script = {
        "editorial_angle": "This angle explains the confirmed development and why it matters now.",
        "script": [_scene("India team faces delay today.") for _ in range(5)],
    }
    valid, reason = validate_content_density(
        script,
        {"title": "India team faces clothing delay"},
        "regular",
    )
    assert valid is False
    assert "scenes" in reason or "words" in reason


def test_content_density_accepts_substantive_script():
    scenes = [
        _scene(
            "The selected development changes the immediate situation for the people and institutions involved, "
            "while the latest evidence gives useful context about what happens next."
        )
        for _ in range(SCRIPT_MIN_SCENES)
    ]
    script = {
        "editorial_angle": (
            "This script explains the practical consequence and context beyond the headline so the viewer understands why the development matters."
        ),
        "script": scenes,
    }
    valid, reason = validate_content_density(
        script,
        {"title": "India team faces clothing delay"},
        "regular",
    )
    assert valid is True, reason
    assert sum(len(scene["voiceover"].split()) for scene in scenes) >= SCRIPT_MIN_TOTAL_WORDS


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
