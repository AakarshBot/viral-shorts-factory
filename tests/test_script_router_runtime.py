from pathlib import Path

from script_router_runtime import (
    _story_source_text,
    _usable_story_source_fallback,
    _validate_script_result,
    assess_story_source_sufficiency,
)

from script_runtime import (
    check_script_originality,
    estimate_narration_duration,
    validate_content_density,
)


def test_story_source_fallback_requires_real_factual_structure():
    rich_story = {
        "title": "India announce a major change after a tense rivalry clash",
        "summary": (
            "Officials confirmed the change after the latest match. "
            "The decision will affect the team's preparation for the upcoming tournament. "
            "The selected story contains enough factual source text to draft a single-source script for human review."
        ),
    }
    thin_story = {"title": "India update", "summary": "Short."}

    assert _usable_story_source_fallback(rich_story) is True
    assert _usable_story_source_fallback(thin_story) is False
    assert len(_story_source_text(rich_story)) >= 220


def test_story_source_sufficiency_is_not_a_raw_character_cutoff():
    compact_but_structured = {
        "title": "Rashid Khan praises an Indian batter",
        "text": (
            "Rashid Khan praised the Indian batter after the latest match. "
            "The comment drew attention because of the rivalry and the timing. "
            "His remarks also created a clear player-focused angle for a short explanation of what was said and why it mattered."
        ),
    }
    result = assess_story_source_sufficiency(compact_but_structured)
    assert result["passed"] is True
    assert result["checks"]["enough_words"] is True


def _valid_script():
    return {
        "editorial_angle": "Explain the confirmed event, key evidence and immediate consequence.",
        "titles": ["A", "B", "C"],
        "recommended_title_index": 1,
        "seo_description": "This explains the confirmed event, the evidence and the immediate consequence.",
        "script": [
            {
                "voiceover": "India confirms a major squad change after the latest review.",
                "narrative_role": "hook",
            },
            {
                "voiceover": "The decision changes preparation, while officials say it followed the latest assessment.",
                "narrative_role": "development",
            },
            {
                "voiceover": "The revised plan now affects the team's next assignment.",
                "narrative_role": "consequence",
            },
        ],
    }


def test_initial_script_contract_is_compact():
    source = Path(__file__).resolve().parents[1].joinpath("script_runtime.py").read_text(encoding="utf-8")
    assert "INITIAL_SCRIPT_MAX_WORDS = 60" in source
    assert "SCENE_1_MAX_WORDS = 14" in source
    assert "def tighten_script_for_duration_once(" not in source


def test_initial_script_rejects_overlong_provider_output():
    script = _valid_script()
    script["script"][1]["voiceover"] = " ".join(["word"] * 60)
    ok, reason = validate_content_density(script, {}, "regular")
    assert ok is False
    assert "maximum is 60" in reason


def test_initial_script_rejects_overlong_first_scene():
    script = _valid_script()
    script["script"][0]["voiceover"] = " ".join(["word"] * 15)
    ok, reason = validate_content_density(script, {}, "regular")
    assert ok is False
    assert "Scene 1 is too long" in reason


def test_router_rejects_overlong_provider_output_before_acceptance():
    script = _valid_script()
    script["script"][1]["voiceover"] = " ".join(["word"] * 60)
    result, reason = _validate_script_result(script, {"title": "India squad change"}, "regular")
    assert result is None
    assert "maximum is 60" in reason


def test_exact_source_sentence_is_rejected_but_rephrasing_is_allowed():
    story = {
        "research_evidence_text": (
            "Former CSK player made a major claim about the investigation. "
            "Officials have not publicly confirmed the allegation."
        )
    }
    copied = {
        "script": [
            {
                "voiceover": "Former CSK player made a major claim about the investigation.",
                "narrative_role": "hook",
            }
        ]
    }
    rephrased = {
        "script": [
            {
                "voiceover": "A former CSK player has made a new allegation tied to the investigation.",
                "narrative_role": "hook",
            }
        ]
    }

    assert check_script_originality(copied, story)["passed"] is False
    assert check_script_originality(rephrased, story)["passed"] is True


def test_primary_writer_uses_hard_initial_word_contract():
    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "Voiceover total: 55–60 words." in source
    assert "Scene 1: 8–14 words" in source
    assert "The entire narration must naturally fit below 30 seconds" in source
    assert "tighten_script_for_duration_once" not in source


def test_router_error_contract_contains_provider_reasons():
    source = Path(__file__).resolve().parents[1].joinpath("script_router_runtime.py").read_text(encoding="utf-8")
    assert 'attempt_reasons = []' in source
    assert 'unknown failure' not in source
    assert 'returned no script candidate.' in source
