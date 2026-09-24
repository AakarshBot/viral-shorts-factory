from pathlib import Path

from script_router_runtime import _validate_script_result, _estimate_script_duration
from script_runtime import check_script_originality, validate_content_density


def _valid_script(scene_count=4):
    scenes = [
        {
            "voiceover": "India confirms a major squad change after the latest review.",
            "narrative_role": "hook",
            "primary_entity": "India cricket team",
            "visual_intent": "news_event",
            "specific_search_prompt": "India cricket team squad change",
            "sport_or_topic_category": "Cricket",
        },
        {
            "voiceover": "Officials say the decision changes preparation for the upcoming tournament.",
            "narrative_role": "development",
            "primary_entity": "India cricket team",
            "visual_intent": "news_event",
            "specific_search_prompt": "India cricket team preparation",
            "sport_or_topic_category": "Cricket",
        },
        {
            "voiceover": "The move follows the latest assessment and alters the team's immediate plans.",
            "narrative_role": "context",
            "primary_entity": "India cricket team",
            "visual_intent": "news_event",
            "specific_search_prompt": "India cricket team assessment",
            "sport_or_topic_category": "Cricket",
        },
        {
            "voiceover": "The revised plan now affects the team's next assignment.",
            "narrative_role": "consequence",
            "primary_entity": "India cricket team",
            "visual_intent": "news_event",
            "specific_search_prompt": "India cricket team next assignment",
            "sport_or_topic_category": "Cricket",
        },
    ]
    return {
        "titles": ["India squad change", "India changes plans", "What changes next"],
        "recommended_title_index": 1,
        "seo_description": "This explains the confirmed event and its immediate consequence for the team.",
        "script": scenes[:scene_count],
    }


def test_regular_contract_is_four_or_five_scenes():
    source = Path(__file__).resolve().parents[1].joinpath("script_runtime.py").read_text(encoding="utf-8")
    assert "Regular Short must contain exactly 4 or 5 scenes." in source


def test_three_beat_structure_is_retained_without_forcing_three_scenes():
    result, reason = _validate_script_result(_valid_script(), {"title": "India squad change"}, "regular")
    assert result is not None, reason
    assert result["narrative_structure"]["passed"] is True
    assert {"hook", "context", "consequence"} <= set(result["narrative_structure"]["roles"])


def test_incomplete_regular_script_is_rejected():
    result, reason = _validate_script_result(_valid_script(3), {"title": "India squad change"}, "regular")
    assert result is None
    assert "4 or 5 scenes" in reason


def test_five_scene_regular_script_is_allowed():
    script = _valid_script()
    extra = dict(script["script"][-2])
    extra["voiceover"] = "The next selection decision will depend on how the team responds."
    extra["narrative_role"] = "context"
    script["script"].insert(3, extra)
    result, reason = _validate_script_result(script, {"title": "India squad change"}, "regular")
    assert result is not None, reason


def test_duration_repair_does_not_become_a_loop():
    source = Path(__file__).resolve().parents[1].joinpath("script_router_runtime.py").read_text(encoding="utf-8")
    assert source.count("_duration_rewrite(") == 2
    assert "for provider_name,call in attempts" in source


def test_originality_gate_rejects_near_verbatim_source_wording():
    story = {
        "research_evidence_text": (
            "Officials confirmed the major squad change after the latest review. "
            "The decision affects preparation for the next assignment."
        )
    }
    script = _valid_script()
    script["script"][0]["voiceover"] = "Officials have now confirmed the major squad change after the latest review."
    result = check_script_originality(script, story)
    assert result["passed"] is False


def test_originality_gate_allows_genuine_rephrasing():
    story = {"research_evidence_text": "Officials confirmed the major squad change after the latest review."}
    script = _valid_script()
    script["script"][0]["voiceover"] = "A new squad change was confirmed after officials completed their review."
    result = check_script_originality(script, story)
    assert result["passed"] is True


def test_duration_estimate_uses_delivery_profile():
    script = {"script": [{"voiceover": " ".join(["word"] * 50)}], "persona_used": "HYPE COMMENTATOR", "delivery_profile": "CYNICAL CRITIC"}
    estimate = _estimate_script_duration(script)
    assert estimate["effective_wpm"] == 144.0


def test_provider_contract_has_no_creator_insight_requirement():
    source = Path(__file__).resolve().parents[1].joinpath("script_runtime.py").read_text(encoding="utf-8")
    assert '"creator_insight"' not in source
    assert '"editorial_angle"' not in source
