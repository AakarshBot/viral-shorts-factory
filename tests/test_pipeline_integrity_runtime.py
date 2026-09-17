from pipeline_integrity_runtime import _wrap_script_writer, clean_narration, is_noise, strict_fallback
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
        "The government said more details will be published before implementation."
    )
    result = strict_fallback({"title": "India announces new policy", "text": source}, genre_key="national_global_affairs")
    assert result["fallback_mode"] == "strict_source_only"
    assert len(result["script"]) == 5
    for scene in result["script"]:
        assert 8 <= len(scene["voiceover"].split()) <= 30
        assert "nbsp" not in scene["voiceover"].lower()
        assert scene["narration_source"] == "validated_source_fallback"


def test_strict_fallback_refuses_thin_source_instead_of_inventing_text():
    try:
        strict_fallback({"title": "Short story", "text": "Only a few words."})
    except ValueError as exc:
        assert "refused to invent narration" in str(exc)
    else:
        raise AssertionError("Thin source should not be padded with invented narration")


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
