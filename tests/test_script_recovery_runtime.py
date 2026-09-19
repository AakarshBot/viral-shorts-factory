from script_runtime import repair_script_structure, _extractive_script_fallback
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
            _scene("India Men's Cricket Team faces a clothing delay before the Asian Games as officials work through the issue."),
            _scene("The delay concerns the team's playing clothing and has become a preparation issue ahead of the tournament."),
            _scene("Officials are working through the supply problem while the squad prepares for the Asian Games campaign."),
        ]
    }

    repaired, diagnostics = repair_script_structure(script, "cricket")

    assert diagnostics["changed"] is True
    assert 5 <= len(repaired["script"]) <= 8
    assert all(8 <= len(scene["voiceover"].split()) <= 30 for scene in repaired["script"])
    assert all(scene["primary_entity"] == "India Men's Cricket Team" for scene in repaired["script"])


def test_overlong_scene_list_is_merged_to_the_production_ceiling():
    script = {
        "script": [
            _scene(f"Scene {index} contains factual information about the selected cricket story.")
            for index in range(1, 10)
        ]
    }

    repaired, diagnostics = repair_script_structure(script, "cricket")

    assert diagnostics["changed"] is True
    assert len(repaired["script"]) == 8
    assert all(8 <= len(scene["voiceover"].split()) <= 30 for scene in repaired["script"])


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


def test_extractives_are_explicitly_publication_blocked():
    fallback = _extractive_script_fallback(
        {
            "title": "India Men's Cricket Team faces clothing delay",
            "text": (
                "India Men's Cricket Team faces a clothing delay before the Asian Games. "
                "Officials are working through the supply issue while the squad prepares for the tournament."
            ),
        },
        {},
        "cricket",
        "cricket",
    )

    assert fallback["fallback_mode"] == "extractive_source_grounded"
    assert fallback["public_publish_blocked"] is True


def test_upstream_public_publish_block_survives_final_qc():
    script = {
        "public_publish_blocked": True,
        "script": [{
            "voiceover": "Creator insight explains why this development matters for the team's preparation.",
            "human_contributed": True,
        }],
    }

    gate = evaluate_originality_gate(script)

    assert gate["passed"] is False
    assert gate["public_blocked"] is True
