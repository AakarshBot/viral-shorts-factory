from script_runtime import (
    classify_narration_duration,
    estimate_narration_duration,
    validate_tts_duration,
)


def _script(text):
    return {"script": [{"voiceover": text}]}


def test_duration_estimate_uses_persona_rate():
    base = estimate_narration_duration(_script(" ".join(["word"] * 50)), {"rate": "+0%"})
    faster = estimate_narration_duration(_script(" ".join(["word"] * 50)), {"rate": "+10%"})

    assert base["seconds"] > faster["seconds"]
    assert base["word_count"] == 50
    assert faster["effective_wpm"] == 165.0


def test_duration_bands_match_controller_policy():
    assert classify_narration_duration(20) == "ideal_or_acceptable"
    assert classify_narration_duration(30) == "ideal_or_acceptable"
    assert classify_narration_duration(35) == "ideal_or_acceptable"
    assert classify_narration_duration(35.01) == "too_long"
    assert classify_narration_duration(19.9) == "short_but_valid"


def test_tts_duration_qc_allows_normal_provider_variance():
    result = validate_tts_duration(25.0, 27.0)
    assert result["passed"] is True
    assert result["delta_seconds"] == 2.0


def test_tts_duration_qc_stops_on_material_mismatch():
    result = validate_tts_duration(25.0, 31.0)
    assert result["passed"] is False
    assert result["delta_seconds"] == 6.0
