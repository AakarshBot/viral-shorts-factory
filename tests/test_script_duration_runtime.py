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


def test_tts_duration_qc_uses_actual_duration_as_the_production_gate():
    result = validate_tts_duration(25.0, 31.0)
    assert result["passed"] is False
    assert result["material_variance"] is True
    assert result["delta_seconds"] == 6.0


def test_tts_duration_qc_allows_material_estimate_error_when_actual_audio_is_valid():
    result = validate_tts_duration(18.3, 23.14)
    assert result["passed"] is True
    assert result["material_variance"] is True
    assert result["actual_seconds"] == 23.14


def test_local_tts_duration_repair_scales_audio_and_word_timings(monkeypatch, tmp_path):
    import audio_runtime
    from pathlib import Path

    source_paths = []
    for index in range(2):
        path = tmp_path / f"scene_{index + 1}.mp3"
        path.write_bytes(b"source-audio")
        source_paths.append(str(path))

    commands = []
    durations = {}

    def fake_run(command, **kwargs):
        commands.append(command)
        output_path = command[-1]
        Path(output_path).write_bytes(b"compressed-audio")
        return type("Completed", (), {"stderr": "", "stdout": ""})()

    def fake_duration(path):
        if "tts_compressed_" in str(path):
            return 14.0
        return 16.0

    monkeypatch.setattr(audio_runtime.subprocess, "run", fake_run)
    monkeypatch.setattr(audio_runtime, "get_audio_duration", fake_duration)

    timings = [
        [{"word": "One", "start": 0.0, "end": 0.5}],
        [{"word": "Two", "start": 0.0, "end": 0.5}],
    ]
    repaired_timings, repaired_durations, total, factor = audio_runtime._repair_total_audio_duration(
        source_paths,
        timings,
        [16.0, 16.0],
    )

    assert factor > 1.0
    assert factor <= audio_runtime.TTS_MAX_COMPRESSION_FACTOR
    assert total <= audio_runtime.TTS_MAX_DURATION_SECONDS
    assert repaired_durations == [14.15, 14.15]
    assert repaired_timings[0][0]["end"] < 0.5
    assert all(command[command.index("-filter:a") + 2].startswith("atempo=") for command in commands)
    assert all(Path(path).read_bytes() == b"compressed-audio" for path in source_paths)


def test_local_tts_duration_repair_refuses_excessive_speed_change(monkeypatch, tmp_path):
    import audio_runtime

    paths = []
    for index in range(2):
        path = tmp_path / f"scene_{index + 1}.mp3"
        path.write_bytes(b"audio")
        paths.append(str(path))

    import pytest

    with pytest.raises(RuntimeError, match="above the 1.10x safety limit"):
        audio_runtime._repair_total_audio_duration(
            paths,
            [[], []],
            [20.0, 14.0],
        )

def test_production_has_no_raw_post_tts_duration_abort():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "TTS duration materially differs from the pre-TTS estimate" not in source
    assert 'if audio_duration["total_seconds"] > 30.0:' not in source
    assert "post-TTS duration contract" in source


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


def test_rephrased_source_sentence_still_passes_near_verbatim_guard():
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


def test_unsupported_numeric_metadata_is_rejected():
    from quality_runtime import validate_deterministic_script_quality

    script = {
        "creator_insight": "The documented change includes 99 affected players in this update.",
        "editorial_angle": "Explain the confirmed change and immediate consequence.",
        "seo_description": "This explains the confirmed event and the evidence behind it.",
        "titles": ["India squad change", "India confirms the change", "What changes now"],
        "recommended_title_index": 1,
        "script": [
            {
                "voiceover": "India confirmed the squad change.",
                "narrative_role": "hook",
                "primary_entity": "India",
                "visual_intent": "news_event",
                "specific_search_prompt": "India squad change",
                "sport_or_topic_category": "Cricket",
            },
            {
                "voiceover": "Officials explained the decision after review.",
                "narrative_role": "development",
                "primary_entity": "India",
                "visual_intent": "news_event",
                "specific_search_prompt": "India squad review",
                "sport_or_topic_category": "Cricket",
            },
            {
                "voiceover": "The change affects preparation for the next assignment.",
                "narrative_role": "consequence",
                "primary_entity": "India",
                "visual_intent": "news_event",
                "specific_search_prompt": "India squad consequence",
                "sport_or_topic_category": "Cricket",
            },
        ],
    }
    ok, reason = validate_deterministic_script_quality(
        script,
        "regular",
        {
            "title": "India squad change",
            "research_evidence_text": (
                "India confirmed the squad change. "
                "Officials explained the decision after review."
            ),
        },
    )
    assert ok is False
    assert "Creator Insight" in reason
    assert "99" in reason


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


def test_final_video_qc_fails_closed_when_duration_cannot_be_read(monkeypatch, tmp_path):
    import moviepy
    import branding_runtime
    import final_qc_runtime

    path = tmp_path / "final_video.mp4"
    path.write_bytes(b"synthetic-video")

    monkeypatch.setattr(branding_runtime, "_artifact_qc", lambda _path: (True, "artifact ok"))

    class BrokenClip:
        def __enter__(self):
            raise RuntimeError("decode failure")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(moviepy, "VideoFileClip", lambda _path: BrokenClip())

    with __import__("pytest").raises(RuntimeError, match="duration could not be validated"):
        final_qc_runtime.validate_final_video(str(path))


def test_final_video_qc_rejects_rendered_video_over_production_limit(monkeypatch, tmp_path):
    import moviepy
    import branding_runtime
    import final_qc_runtime

    path = tmp_path / "final_video.mp4"
    path.write_bytes(b"synthetic-video")

    monkeypatch.setattr(branding_runtime, "_artifact_qc", lambda _path: (True, "artifact ok"))

    class Clip:
        duration = 30.01

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(moviepy, "VideoFileClip", lambda _path: Clip())

    with __import__("pytest").raises(RuntimeError, match="exceeds the 30.0s production limit"):
        final_qc_runtime.validate_final_video(str(path))


def test_production_post_render_validation_is_fail_closed():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    start = source.index("        video_size = os.path.getsize(video_path)")
    end = source.index("        post_render_hook =", start)
    block = source[start:end]

    assert "from final_qc_runtime import validate_final_video" in block
    assert "validate_final_video(video_path)" in block
    assert "Could not validate video duration" not in block
    assert '_mark_run_status("FAILED", reason)' in block
