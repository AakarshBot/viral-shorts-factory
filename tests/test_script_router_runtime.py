from pathlib import Path

from script_router_runtime import (
    _story_source_text,
    _usable_story_source_fallback,
    assess_story_source_sufficiency,
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
    assert "checks" in result
    assert "enough_words" in result["checks"]


def test_duration_rewrite_contract_passes_the_actual_previous_draft():
    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert 'duration_story["previous_script"]' in source
    assert "PREVIOUS DRAFT TO TIGHTEN:" in source
    assert "Preserve every supported essential fact" in source


def test_duration_fallbacks_receive_the_previous_draft_too():
    source = Path(__file__).resolve().parents[1].joinpath("research_runtime.py").read_text(encoding="utf-8")
    assert "def _previous_draft_text" in source
    assert "PREVIOUS DRAFT TO TIGHTEN:" in source


def test_script_router_reuses_existing_evidence_pack():
    source = Path(__file__).resolve().parents[1].joinpath("script_router_runtime.py").read_text(encoding="utf-8")
    assert "Reusing existing evidence pack." in source
    assert "No second research pass" in source


def test_script_validation_does_not_require_visual_search_metadata():
    from script_runtime import validate_content_density

    script = {
        "editorial_angle": "The result explains the immediate change and why it matters.",
        "script": [
            {"voiceover": "India were called arrogant after the latest cricket clash.", "narrative_role": "hook"},
            {"voiceover": "The comment triggered a direct response and put the dispute back in focus.", "narrative_role": "development"},
            {"voiceover": "The immediate consequence is a renewed debate around the rivalry.", "narrative_role": "consequence"},
        ],
    }

    valid, reason = validate_content_density(
        script,
        {},
        "regular",
        require_visual_metadata=False,
    )
    assert valid, reason


def test_primary_writer_contains_freshfeed_selection_context():
    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "FRESHFEED SELECTION CONTEXT" in source
    assert "pattern_reasons" in source
    assert "rivalry_signal" in source



def test_duration_rewrite_uses_run_robot_language_config():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "duration_story, lang_cfg, genre_key=cat_choice" in source
    assert "duration_story, language_cfg, genre_key=cat_choice" not in source

def test_duration_compression_is_a_single_lightweight_pass():
    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "tighten_script_for_duration_once(" in source
    assert "write_script(\n                story_payload" not in source
    assert 'duration_story["research_evidence_pack"]' not in source

