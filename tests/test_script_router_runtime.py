from pathlib import Path

from script_router_runtime import (
    _story_source_text,
    _usable_story_source_fallback,
    assess_story_source_sufficiency,
)

import script_runtime
from script_runtime import estimate_narration_duration, tighten_script_for_duration_once


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


def test_duration_compression_contract_uses_the_actual_validated_draft():
    source = Path(__file__).resolve().parents[1].joinpath("script_runtime.py").read_text(encoding="utf-8")
    assert "def tighten_script_for_duration_once(" in source
    assert "PREVIOUS VALIDATED SCRIPT:" in source
    assert "Preserve every supported essential fact" in source
    assert "the central hook, editorial angle and factual order" in source


def test_obsolete_duration_rewrite_architecture_is_removed():
    root = Path(__file__).resolve().parents[1]
    for filename in ("ultimate_bot.py", "research_runtime.py"):
        source = root.joinpath(filename).read_text(encoding="utf-8")
        assert "previous_script" not in source
        assert "PREVIOUS DRAFT TO TIGHTEN:" not in source
        assert "duration_story" not in source


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


def test_primary_writer_has_explicit_25_to_28_second_runtime_target():
    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "25–28 seconds" in source
    assert "20–30 seconds" in source
    assert "35-second mark is an absolute safety ceiling" in source
    assert "Do not aim for 35 seconds" in source


def test_primary_writer_contains_freshfeed_selection_context():
    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "FRESHFEED SELECTION CONTEXT" in source
    assert "pattern_reasons" in source
    assert "rivalry_signal" in source


def test_duration_rewrite_uses_run_robot_language_config():
    source = Path(__file__).resolve().parents[1].joinpath("script_runtime.py").read_text(encoding="utf-8")
    assert "def tighten_script_for_duration_once(" in source
    assert "language_cfg," in source
    assert 'language_cfg.get("script_instruction")' in source
    assert "Language: {language_instruction}" in source


def test_duration_compression_is_a_single_lightweight_pass():
    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "tighten_script_for_duration_once(" in source
    assert "write_script(\n                story_payload" not in source
    assert 'duration_story["research_evidence_pack"]' not in source


def test_duration_compression_has_a_provider_free_fallback(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    script = {
        "editorial_angle": "The injury changes the player's immediate tournament plans.",
        "script": [
            {"voiceover": "Anisimova withdrew from the Singapore Open in order to protect her left wrist.", "narrative_role": "hook"},
            {"voiceover": "It is important to note that the withdrawal came before her scheduled match.", "narrative_role": "development"},
            {"voiceover": "The consequence is that her tournament plans now change because of the injury.", "narrative_role": "consequence"},
        ],
    }
    story = {"title": "Anisimova withdraws from Singapore Open with left wrist injury"}
    persona = {"rate": 0}

    result = tighten_script_for_duration_once(
        script,
        story,
        {},
        "regular",
        target_seconds=30.0,
        persona_profile=persona,
    )

    assert result is not None
    assert result["duration_compression_provider"] == "deterministic_local"
    assert estimate_narration_duration(result, persona)["seconds"] < estimate_narration_duration(script, persona)["seconds"]



def test_duration_compression_has_sentence_level_fallback_without_phrase_matches(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    script = {
        "editorial_angle": "A late cricket decision changes the team's immediate plans.",
        "script": [
            {
                "voiceover": (
                    "The board confirmed a late squad change after a meeting on Tuesday. "
                    "The decision affects the team's next assignment."
                ),
                "narrative_role": "hook",
            },
            {
                "voiceover": (
                    "Officials reviewed the latest information before informing the player. "
                    "The board then approved the change later that day."
                ),
                "narrative_role": "development",
            },
            {
                "voiceover": (
                    "The immediate consequence is that the squad must adjust its plans for the next match. "
                    "The replacement will take a different role."
                ),
                "narrative_role": "consequence",
            },
        ],
    }
    story = {"title": "Cricket board confirms late squad change"}

    original = estimate_narration_duration(script, {"rate": 0})
    assert original["seconds"] > 35.0

    result = tighten_script_for_duration_once(
        script,
        story,
        {},
        "regular",
        target_seconds=30.0,
        persona_profile={"rate": 0},
    )

    assert result is not None
    assert result["duration_compression_provider"] == "deterministic_sentence_trim"
    assert estimate_narration_duration(result, {"rate": 0})["seconds"] <= 35.0


def test_duration_compression_falls_back_when_groq_http_fails(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    def fail_request(*args, **kwargs):
        raise script_runtime.requests.RequestException("simulated outage")

    monkeypatch.setattr(script_runtime.requests, "post", fail_request)
    script = {
        "editorial_angle": "The injury changes the player's immediate tournament plans.",
        "script": [
            {"voiceover": "Anisimova withdrew from the Singapore Open in order to protect her left wrist.", "narrative_role": "hook"},
            {"voiceover": "It is important to note that the withdrawal came before her scheduled match.", "narrative_role": "development"},
            {"voiceover": "The consequence is that her tournament plans now change because of the injury.", "narrative_role": "consequence"},
        ],
    }
    story = {"title": "Anisimova withdraws from Singapore Open with left wrist injury"}
    persona = {"rate": 0}

    result = tighten_script_for_duration_once(
        script,
        story,
        {},
        "regular",
        target_seconds=30.0,
        persona_profile=persona,
    )

    assert result is not None
    assert result["duration_compression_provider"] == "deterministic_local"
