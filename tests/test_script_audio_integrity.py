import inspect

import ultimate_bot
import audio_runtime
import pipeline_integrity_runtime


def test_self_critique_is_not_an_unconditional_pass():
    script = {
        "script": [
            {"voiceover": "Wait for this unbelievable update right now."},
            {"voiceover": "Wait for this unbelievable update right now."},
        ]
    }

    score, reason = ultimate_bot.self_critique_pass(script, "regular")

    assert score < 8
    assert "Issues:" in reason


def test_strict_fallback_top5_has_json_dependency_available():
    story = {
        "title": "Top five technology developments",
        "text": "[{\"title\": \"AI launch\", \"text\": \"A new AI product was launched with several documented features and broad industry coverage. The company described the product in a detailed announcement covering its architecture, availability, pricing, performance, integrations, safety measures, and intended users. Industry observers also noted the launch as a significant technology development with practical implications for developers and businesses.\"}]",
    }

    result = pipeline_integrity_runtime.strict_fallback(
        story,
        genre_key="technology",
        format_mode="top5",
    )

    assert result["fallback_mode"] == "strict_source_only"
    assert result["script"]


def test_audio_duration_is_authoritative_not_timing_padding():
    source = inspect.getsource(audio_runtime.generate_voiceover_and_timestamps)

    assert "duration = actual_duration" in source
    assert "timing_duration" not in source


def test_compile_refuses_synthetic_word_timings():
    source = inspect.getsource(ultimate_bot.compile_video)

    assert "refusing synthetic subtitle timing" in source
    assert "dur_per_word" not in source
