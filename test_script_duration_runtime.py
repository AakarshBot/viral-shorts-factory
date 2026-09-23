from script_runtime import (
    classify_narration_duration,
    estimate_narration_duration,
    tighten_script_for_duration_once,
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



def _long_valid_script():
    return {
        "editorial_angle": "Explain the concrete change, the supporting development, and the immediate consequence.",
        "script": [
            {
                "voiceover": (
                    "India confirmed a major squad change before the next assignment, with the latest decision affecting the team immediately."
                ),
                "narrative_role": "hook",
            },
            {
                "voiceover": (
                    "Officials reviewed the latest information, which followed a detailed assessment, and the board confirmed the decision after the meeting."
                ),
                "narrative_role": "development",
            },
            {
                "voiceover": (
                    "The change matters because the original plan had already been communicated, while the new decision alters preparation and the role of the replacement before the next official team review."
                ),
                "narrative_role": "context",
            },
            {
                "voiceover": (
                    "The immediate consequence is that the squad must adjust its plans, which affects preparation for the next match and leaves the replacement with a different role."
                ),
                "narrative_role": "consequence",
            },
        ],
    }


def test_over_35_second_script_has_a_provider_free_hard_ceiling(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    script = _long_valid_script()
    before = estimate_narration_duration(script, {"rate": "0%"})

    assert before["seconds"] > 35.0

    result = tighten_script_for_duration_once(
        script,
        {"title": "India confirms major squad change"},
        {},
        "regular",
        target_seconds=30.0,
        persona_profile={"rate": "0%"},
    )

    assert result is not None
    after = estimate_narration_duration(result, {"rate": "0%"})
    assert after["seconds"] <= 35.0
    assert after["seconds"] < before["seconds"]
    assert result["duration_compression_only"] is True


def test_groq_overlong_rewrite_finishes_with_deterministic_ceiling(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    script = _long_valid_script()

    class _Response:
        status_code = 200

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": __import__("json").dumps({
                            "script": [
                                {"index": 1, "voiceover": script["script"][0]["voiceover"]},
                                {"index": 2, "voiceover": script["script"][1]["voiceover"]},
                                {"index": 3, "voiceover": script["script"][2]["voiceover"]},
                                {"index": 4, "voiceover": script["script"][3]["voiceover"]},
                            ]
                        })
                    }
                }]
            }

    monkeypatch.setattr("script_runtime.requests.post", lambda *args, **kwargs: _Response())

    result = tighten_script_for_duration_once(
        script,
        {"title": "India confirms major squad change"},
        {},
        "regular",
        target_seconds=30.0,
        persona_profile={"rate": "0%"},
    )

    assert result is not None
    assert estimate_narration_duration(result, {"rate": "0%"})["seconds"] <= 35.0
    assert result["duration_compression_provider"] in {"groq", "groq_then_deterministic", "groq_then_hard_ceiling"}


def test_extremely_long_valid_script_has_last_resort_hard_ceiling(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    script = {
        "editorial_angle": "Explain the confirmed development and its immediate consequence.",
        "script": [
            {
                "voiceover": "India confirmed the squad change before the next assignment, and officials explained the decision during the latest review.",
                "narrative_role": "hook",
            },
            {
                "voiceover": "Officials reviewed medical information, selection reports, preparation data, player availability, training requirements, replacement options, travel plans, scheduling details, and the wider tournament context before confirming the change.",
                "narrative_role": "development",
            },
            {
                "voiceover": "The decision matters because the original squad plan had already been communicated, the preparation schedule had been built around it, the replacement has a different role, and the coaching staff now has to adjust several parts of the plan.",
                "narrative_role": "context",
            },
            {
                "voiceover": "The immediate consequence is that the squad must change its preparation, the replacement must adapt quickly, and the team's next assignment now begins with a different combination of players and responsibilities.",
                "narrative_role": "consequence",
            },
        ],
    }

    result = tighten_script_for_duration_once(
        script,
        {"title": "India confirms squad change"},
        {},
        "regular",
        target_seconds=30.0,
        persona_profile={"rate": "0%"},
    )

    assert result is not None
    assert estimate_narration_duration(result, {"rate": "0%"})["seconds"] <= 35.0
    assert result.get("duration_compression_provider") in {
        "deterministic_duration_fallback",
        "deterministic_sentence_trim",
        "deterministic_hard_ceiling",
    }
