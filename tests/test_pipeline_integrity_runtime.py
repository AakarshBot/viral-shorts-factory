from pipeline_integrity_runtime import clean_narration, is_noise, strict_fallback


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
