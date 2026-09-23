from script_runtime import (
    check_script_originality,
    classify_narration_duration,
    estimate_narration_duration,
    validate_content_density,
    validate_tts_duration,
)


def _script(texts):
    return {
        "script": [
            {"voiceover": text, "narrative_role": "hook" if i == 0 else "development"}
            for i, text in enumerate(texts)
        ]
    }


def test_duration_estimate_uses_persona_rate():
    base = estimate_narration_duration(_script([" ".join(["word"] * 50)]), {"rate": "+0%"})
    faster = estimate_narration_duration(_script([" ".join(["word"] * 50)]), {"rate": "+10%"})

    assert base["seconds"] > faster["seconds"]
    assert base["word_count"] == 50
    assert faster["effective_wpm"] == 165.0


def test_duration_bands_match_initial_writer_policy():
    assert classify_narration_duration(20) == "ideal_or_acceptable"
    assert classify_narration_duration(28) == "ideal_or_acceptable"
    assert classify_narration_duration(30) == "too_long"
    assert classify_narration_duration(30.01) == "too_long"
    assert classify_narration_duration(19.9) == "short_but_valid"


def test_tts_duration_qc_allows_normal_provider_variance():
    result = validate_tts_duration(25.0, 27.0)
    assert result["passed"] is True
    assert result["delta_seconds"] == 2.0


def test_tts_duration_qc_stops_on_material_mismatch():
    result = validate_tts_duration(25.0, 31.0)
    assert result["passed"] is False
    assert result["delta_seconds"] == 6.0


def test_initial_script_uses_a_safety_ceiling_above_the_normal_duration_target():
    script = _script(["Hook words only."] + [" ".join(["word"] * 75)])
    ok, reason = validate_content_density(script, {}, "regular")

    assert ok is True, reason


def test_initial_script_rejects_only_beyond_the_safety_ceiling():
    script = _script(["Hook words only."] + [" ".join(["word"] * 90)])
    ok, reason = validate_content_density(script, {}, "regular")

    assert ok is False
    assert "maximum is 90" in reason


def test_initial_script_rejects_a_long_first_scene():
    script = _script([
        " ".join(["word"] * 15),
        " ".join(["word"] * 20),
    ])
    ok, reason = validate_content_density(script, {}, "regular")

    assert ok is False
    assert "Scene 1 is too long" in reason


def test_initial_script_scene_one_cap_is_independent_of_later_length():
    script = _script([
        "one two three four five six seven eight nine ten eleven twelve",
        "one two three four five six seven eight nine ten",
    ])
    ok, reason = validate_content_density(script, {}, "regular")

    assert ok is True, reason


def test_exact_source_sentence_is_rejected():
    source = {
        "research_evidence_text": (
            "Former CSK player made a major claim about the investigation. "
            "Officials have not publicly confirmed the allegation."
        )
    }
    script = _script([
        "Former CSK player made a major claim about the investigation.",
        "Officials have not publicly confirmed the allegation today.",
    ])

    result = check_script_originality(script, source)

    assert result["passed"] is False
    assert result["failures"][0]["scene"] == 1


def test_rephrased_source_sentence_is_allowed():
    source = {
        "research_evidence_text": (
            "Former CSK player made a major claim about the investigation."
        )
    }
    script = _script([
        "A former CSK player has made a new allegation tied to the investigation.",
        "The claim is still awaiting public confirmation.",
    ])

    result = check_script_originality(script, source)

    assert result["passed"] is True
