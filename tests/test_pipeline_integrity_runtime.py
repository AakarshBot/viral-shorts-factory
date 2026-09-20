from pipeline_integrity_runtime import (
    _wrap_compile,
    _wrap_script_writer,
    _write_endpoint_srt,
    clean_narration,
    is_noise,
    strict_fallback,
)
from script_runtime import wrap_write_script


class _Bot:
    def __init__(self):
        def run_robot():
            return None

        self.run_robot = run_robot

        def write_script(story_data, language_cfg, genre_key, conn, format_mode):
            return {
                "script": [
                    {
                        "voiceover": "India announced a new policy today.",
                        "primary_entity": "India",
                    }
                ]
            }

        self.write_script = write_script


def test_html_entities_and_nbsp_are_removed_from_narration():
    text = "India&nbsp;announced a new plan &amp; the details matter."
    assert clean_narration(text) == "India announced a new plan & the details matter."
    assert "nbsp" not in clean_narration(text).lower()


def test_prompt_and_provider_noise_is_rejected():
    assert is_noise("EDITORIAL SCRIPT CONTRACT — DO NOT OUTPUT THIS BLOCK.")
    assert is_noise("[!] Groq API error status 429")
    assert not is_noise("India announced a new policy today.")


def test_strict_fallback_uses_only_source_words():
    source = (
        "India announced a new policy today. The ministry said the measure will begin next month. "
        "Officials described the change as a response to recent developments. The first phase covers major cities. "
        "The government said more details will be published before implementation. Independent agencies are reviewing "
        "the policy's expected administrative effects, while officials publish additional guidance for affected regions. "
        "The latest documents describe how the change will be introduced and what agencies must prepare before implementation. "
        "The decision remains subject to the published rules and the timetable already announced by the responsible ministry. "
        "Implementation teams are expected to publish additional guidance and clarify responsibilities before the policy takes effect. "
        "The current documents describe the immediate administrative process but do not establish any broader outcome beyond the published timetable."
    )
    result = strict_fallback(
        {"title": "India announces new policy", "text": source},
        genre_key="national_global_affairs",
        format_mode="regular",
    )
    assert result["fallback_mode"] == "strict_source_only"
    assert 6 <= len(result["script"]) <= 8
    assert result["editorial_angle"]
    for scene in result["script"]:
        assert 12 <= len(scene["voiceover"].split()) <= 48
        assert scene["scene_source"] == "validated_source_fallback"

def test_strict_fallback_refuses_thin_source_instead_of_inventing_text():
    try:
        strict_fallback({"title": "Short story", "text": "Only a few words."})
    except ValueError as exc:
        assert "refused to invent narration" in str(exc)
    else:
        raise AssertionError("Thin source should not be padded with invented narration")


def test_generated_script_is_cleaned_and_marked_authoritative():
    bot = _Bot()

    def dirty_writer(story_data, language_cfg, genre_key, conn, format_mode):
        base = (
            "Officials explained the immediate implementation details and described how the new measure "
            "will affect several regions while departments prepare for the rollout."
        )
        return {
            "editorial_angle": "The script explains the practical consequence and context beyond the headline.",
            "script": [
                {
                    "voiceover": (
                        "India&nbsp;announced <b>a new plan</b> today. https://example.com​ "
                        "Officials explained the immediate implementation details and described its effect on regions."
                    ),
                    "primary_entity": "India&nbsp;",
                }
            ] + [
                {
                    "voiceover": base,
                    "primary_entity": "India",
                }
                for _ in range(5)
            ]
        }

    bot.write_script = dirty_writer
    _wrap_script_writer(bot)
    result = bot.write_script({}, {}, "news", None, "regular")

    scene = result["script"][0]
    assert result["authoritative_narration"] is True
    assert result["integrity_version"]
    assert scene["narration_source"] == "validated_script"
    assert scene["voiceover"].startswith("India announced a new plan today.")
    assert "http" not in scene["voiceover"].lower()
    assert "<b>" not in scene["voiceover"].lower()
    assert "nbsp" not in scene["voiceover"].lower()

def test_provider_garbage_falls_back_without_leaking_into_script():
    bot = _Bot()

    def broken_writer(story_data, language_cfg, genre_key, conn, format_mode):
        return {
            "script": [
                {
                    "voiceover": "[!] Groq API error 429 — return only a valid JSON schema.",
                }
            ]
        }

    bot.write_script = broken_writer
    _wrap_script_writer(bot)
    source = (
        "India announced a new policy today. The ministry said the measure will begin next month. "
        "Officials described the change as a response to recent developments. The first phase covers major cities. "
        "The government said more details will be published before implementation. Independent agencies are reviewing "
        "the policy's expected administrative effects, while officials publish additional guidance for affected regions. "
        "The latest documents describe how the change will be introduced and what agencies must prepare before implementation."
    )
    result = bot.write_script(
        {"title": "India announces new policy", "text": source},
        {},
        "news",
        None,
        "regular",
    )

    assert result["fallback_mode"] == "strict_source_only"
    assert result["authoritative_narration"] is True
    assert all("Groq" not in scene["voiceover"] for scene in result["script"])
    assert all("JSON schema" not in scene["voiceover"] for scene in result["script"])
    assert all(scene["narration_source"] == "validated_script" for scene in result["script"])
    assert all(12 <= len(scene["voiceover"].split()) <= 48 for scene in result["script"])

def test_content_density_marker_survives_integrity_wrapper_order():
    bot = _Bot()
    wrap_write_script(bot)
    assert getattr(bot.write_script, "_content_dense_bound", False) is True

    _wrap_script_writer(bot)
    assert getattr(bot.write_script, "_pipeline_integrity_wrapped", False) is True
    assert getattr(bot.write_script, "_content_dense_bound", False) is False

    # This mirrors the repair in patch_audio_direction: re-apply the
    # content-density wrapper around the integrity wrapper so the final public
    # binding retains both contracts.
    wrap_write_script(bot)
    assert getattr(bot.write_script, "_content_dense_bound", False) is True


def test_endpoint_subtitle_srt_uses_word_timings():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "endpoint.srt"
        ok = _write_endpoint_srt(
            str(path),
            [
                {"word": "The", "start": 0.0, "end": 0.3},
                {"word": "headline", "start": 0.3, "end": 0.8},
                {"word": "is", "start": 0.8, "end": 1.0},
                {"word": "confirmed", "start": 1.0, "end": 1.5},
                {"word": "today", "start": 1.5, "end": 1.9},
                {"word": "officially", "start": 1.9, "end": 2.2},
            ],
        )
        assert ok is True
        text = path.read_text(encoding="utf-8")
        assert "00:00:00,000 --> 00:00:02,200" in text
        assert "The headline is confirmed today officially" in text


def test_compile_integrity_does_not_add_duplicate_endpoint_subtitles(monkeypatch):
    captured = {}

    class CompileBot:
        def __init__(self):
            def run_robot():
                return None

            self.run_robot = run_robot

            def compile_video(scene_visual_packages, audio_paths, word_timings, language_cfg, format_mode):
                captured["args"] = (
                    scene_visual_packages,
                    audio_paths,
                    word_timings,
                    language_cfg,
                    format_mode,
                )
                return "final.mp4"

            self.compile_video = compile_video

    bot = CompileBot()
    _wrap_compile(bot)
    timings = [
        [{"word": "hook"}],
        [{"word": "body"}],
        [{"word": "body2"}],
        [{"word": "outro"}],
    ]
    result = bot.compile_video(
        ["s1", "s2", "s3", "s4"],
        ["a1", "a2", "a3", "a4"],
        timings,
        {},
        "regular",
    )

    assert result == "final.mp4"
    assert captured["args"][1] == ["a1", "a2", "a3", "a4"]
    assert captured["args"][2] == timings
    assert len(captured["args"][2]) == 4
