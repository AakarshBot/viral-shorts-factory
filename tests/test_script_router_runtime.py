from script_router_runtime import _story_source_text, _usable_story_source_fallback


def test_story_source_fallback_requires_real_selected_story_text():
    rich_story = {
        "title": "India announce a major change after a tense rivalry clash",
        "summary": (
            "Officials confirmed the change after the latest match, and the decision will affect "
            "the team's preparation for the upcoming tournament. The selected story contains enough "
            "factual source text to draft a single-source script for human review."
        ),
    }
    thin_story = {"title": "India update", "summary": "Short."}

    assert _usable_story_source_fallback(rich_story) is True
    assert _usable_story_source_fallback(thin_story) is False
    assert len(_story_source_text(rich_story)) >= 220


def test_story_source_fallback_contains_no_generated_editorial_instructions():
    story = {
        "title": "Rashid Khan calls an Indian batter exceptional",
        "text": "Rashid Khan praised the Indian batter after the latest match. "
                "The comment drew attention because of the rivalry between the teams. "
                "The selected report describes what was said and the immediate reaction.",
    }

    text = _story_source_text(story)

    assert "Rashid Khan" in text
    assert "praised" in text.lower()
    assert "return only valid json" not in text.lower()


def test_duration_rewrite_contract_reuses_existing_evidence_and_preserves_valid_original():
    from pathlib import Path
    import ultimate_bot

    source = Path(ultimate_bot.__file__).read_text(encoding="utf-8")
    assert "Re-use the first evidence pack" not in source
    assert "Reuse the first evidence pack during the tightening rewrite." in source
    assert "retaining the original within-limit draft" in source
    assert 'if duration_estimate["seconds"] <= 35.0' in source
