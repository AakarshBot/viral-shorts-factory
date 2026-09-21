from pipeline_integrity_runtime import (
    _clean_script_result,
    clean_narration,
    is_noise,
    strict_fallback,
)


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
        "India announced a new policy today, according to the ministry. "
        "Officials said the measure will begin next month after the published timetable is finalized. "
        "The first phase covers major cities while agencies prepare implementation guidance. "
        "The latest documents explain the administrative process and the responsibilities of affected departments."
    )
    result = strict_fallback(
        {"title": "India announces new policy", "text": source},
        genre_key="national_global_affairs",
        format_mode="regular",
    )
    assert result["fallback_mode"] == "strict_source_only"
    assert result["public_publish_blocked"] is True
    assert len(result["script"]) == 4
    assert [scene["narrative_role"] for scene in result["script"]] == [
        "hook", "development", "context", "consequence"
    ]

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
        return {
            "editorial_angle": "The script explains the practical consequence and context beyond the headline.",
            "titles": ["Headline", "Context", "Question"],
            "recommended_title_index": 1,
            "seo_description": "A factual explanation of the development, its background, and practical consequence.",
            "script": [
                {
                    "voiceover": "India&nbsp;announced <b>a new plan</b> today. Officials explained the immediate implementation details.",
                    "narrative_role": "hook",
                    "primary_entity": "India",
                    "specific_search_prompt": "India new plan",
                },
                {
                    "voiceover": "Officials are coordinating the rollout while departments prepare for the announced change.",
                    "narrative_role": "development",
                    "primary_entity": "India",
                    "specific_search_prompt": "India rollout",
                },
                {
                    "voiceover": "The background matters because the change affects several agencies and the published implementation process.",
                    "narrative_role": "context",
                    "primary_entity": "India",
                    "specific_search_prompt": "India implementation process",
                },
                {
                    "voiceover": "The practical consequence is that departments must align their preparation with the timetable already announced.",
                    "narrative_role": "consequence",
                    "primary_entity": "India",
                    "specific_search_prompt": "India policy timetable",
                },
            ],
        }

    bot.write_script = dirty_writer
    result = _clean_script_result(
        dirty_writer({}, {}, "news", None, "regular"),
        {},
        "regular",
    )

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
        "India announced a new policy today. "
        "The ministry said the measure will begin next month after the published timetable is finalized. "
        "Officials described the change as a response to recent developments and outlined the first phase for major cities. "
        "The latest documents explain the administrative process and the responsibilities of affected departments."
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
    assert result["public_publish_blocked"] is True
    assert all("Groq" not in scene["voiceover"] for scene in result["script"])
    assert all("JSON schema" not in scene["voiceover"] for scene in result["script"])
    assert all(scene["narration_source"] == "validated_script" for scene in result["script"])






