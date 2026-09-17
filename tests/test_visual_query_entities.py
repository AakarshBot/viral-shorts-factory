from visual_query_entities_runtime import (
    build_candidate_scene,
    extract_slide_search_subjects,
    search_slide_visual,
)
from visual_strategy_runtime import build_deep_queries


def test_slide_subjects_are_clean_and_script_derived():
    scene = {
        "primary_entity": "India",
        "voiceover": (
            "India's cricket team is being discussed, with Rashid Khan also mentioned in the report. "
            "The Indian cricket team remains part of the story."
        ),
        "specific_search_prompt": "India cricket team Rashid Khan interview press conference 2024 person",
        "visual_intent": "press conference person",
        "sport_or_topic_category": "cricket",
    }

    assert extract_slide_search_subjects(scene) == ["India", "Rashid Khan", "Indian cricket team"]
    queries, visual_type = build_deep_queries(scene, "India Rashid Khan noisy title")
    assert queries == ["India", "Rashid Khan", "Indian cricket team"]
    assert visual_type == "LOCATION"


def test_search_subjects_do_not_include_context_noise():
    scene = {
        "primary_entity": "Rishabh Pant",
        "voiceover": "Rishabh Pant was omitted from India's ODI squad after a selection meeting.",
        "specific_search_prompt": "Rishabh Pant ODI players press conference 2024 person",
        "visual_intent": "press conference person",
        "sport_or_topic_category": "cricket",
    }

    assert extract_slide_search_subjects(scene) == ["Rishabh Pant", "India"]
    assert build_deep_queries(scene)[0] == ["Rishabh Pant", "India"]
    joined = " ".join(build_deep_queries(scene)[0]).lower()
    assert all(noise not in joined for noise in ("odi", "players", "press", "conference", "2024", "person"))


def test_candidate_scene_makes_each_query_the_qa_subject():
    source = {
        "primary_entity": "India",
        "voiceover": "India and Rashid Khan are part of this cricket story.",
        "specific_search_prompt": "India Rashid Khan press conference",
        "visual_intent": "press conference person",
        "sport_or_topic_category": "cricket",
    }

    candidate = build_candidate_scene(source, "Rashid Khan")
    assert candidate["primary_entity"] == "Rashid Khan"
    assert candidate["specific_search_prompt"] == "Rashid Khan"
    assert candidate["voiceover"] == "Rashid Khan"
    assert candidate["visual_type"] == "PERSON"


def test_visual_search_falls_back_to_next_clean_subject(monkeypatch):
    calls = []

    class FakeVisualRuntime:
        @staticmethod
        def _relevant_asset(bot, scene, category, used_urls, used_hashes, video_title):
            calls.append({
                "subject": scene["primary_entity"],
                "prompt": scene["specific_search_prompt"],
                "voice": scene["voiceover"],
                "visual_type": scene["visual_type"],
            })
            if scene["primary_entity"] == "India":
                raise RuntimeError("India candidate rejected")
            return "IMAGE", False, "Wikipedia"

    scene = {
        "primary_entity": "India",
        "voiceover": "India is discussed with Rashid Khan in the same slide.",
        "specific_search_prompt": "India Rashid Khan press conference",
        "visual_intent": "press conference person",
        "sport_or_topic_category": "cricket",
    }

    result = search_slide_visual(FakeVisualRuntime, object(), scene, "cricket", set(), set(), "noisy title")
    assert result == ("IMAGE", False, "Wikipedia")
    assert calls[0]["subject"] == "India"
    assert calls[1] == {
        "subject": "Rashid Khan",
        "prompt": "Rashid Khan",
        "voice": "Rashid Khan",
        "visual_type": "PERSON",
    }


def test_unicode_primary_subject_is_preserved():
    for entity in ("विराट कोहली", "భారతదేశం", "محمد صلاح"):
        scene = {
            "primary_entity": entity,
            "voiceover": f"{entity} is the subject of this slide.",
            "specific_search_prompt": f"{entity} latest press conference",
            "visual_intent": "person",
        }
        assert extract_slide_search_subjects(scene)[0] == entity
