from visual_query_entities_runtime import (
    build_candidate_scene,
    extract_slide_search_subjects,
    lock_visual_subject,
    search_slide_visual,
)
from visual_strategy_runtime import build_deep_queries


def test_slide_subject_is_locked_and_script_noise_is_ignored():
    scene = {
        "primary_entity": "India",
        "voiceover": "India is being discussed, with Rashid Khan also mentioned. The Indian cricket team remains part of the story.",
        "specific_search_prompt": "India cricket team Rashid Khan interview press conference 2024 person",
        "visual_intent": "press conference person",
        "sport_or_topic_category": "cricket",
    }

    assert lock_visual_subject(scene) == "India"
    assert extract_slide_search_subjects(scene) == ["India"]
    queries, visual_type = build_deep_queries(scene, "India Rashid Khan noisy title")
    assert queries == ["India"]
    assert visual_type == "LOCATION"


def test_search_query_contains_no_context_noise():
    scene = {
        "primary_entity": "Rishabh Pant",
        "voiceover": "Rishabh Pant was omitted from India's ODI squad after a selection meeting.",
        "specific_search_prompt": "Rishabh Pant ODI players press conference 2024 person",
        "visual_intent": "press conference person",
        "sport_or_topic_category": "cricket",
    }

    assert extract_slide_search_subjects(scene) == ["Rishabh Pant"]
    assert build_deep_queries(scene)[0] == ["Rishabh Pant"]
    query = build_deep_queries(scene)[0][0].lower()
    assert all(noise not in query for noise in ("odi", "players", "press", "conference", "2024", "person"))


def test_candidate_scene_locks_subject_without_rewriting_narration():
    source = {
        "primary_entity": "India",
        "voiceover": "India and Rashid Khan are part of the same cricket story.",
        "specific_search_prompt": "India Rashid Khan press conference",
        "visual_intent": "press conference person",
        "sport_or_topic_category": "cricket",
    }

    candidate = build_candidate_scene(source, "Rashid Khan")
    assert candidate["primary_entity"] == "Rashid Khan"
    assert candidate["specific_search_prompt"] == "Rashid Khan"
    assert candidate["voiceover"] == source["voiceover"]
    assert candidate["visual_subject_locked"] is True
    assert candidate["visual_subject_lock"] == "Rashid Khan"
    assert candidate["visual_type"] == "PERSON"


def test_visual_search_does_not_fall_back_to_another_subject():
    calls = []

    class FakeVisualRuntime:
        @staticmethod
        def _relevant_asset(bot, scene, category, used_urls, used_hashes, video_title):
            calls.append(scene["primary_entity"])
            raise RuntimeError("locked subject rejected")

    scene = {
        "primary_entity": "India",
        "voiceover": "India is discussed with Rashid Khan in the same slide.",
        "specific_search_prompt": "India Rashid Khan press conference",
        "visual_intent": "press conference person",
        "sport_or_topic_category": "cricket",
    }

    try:
        search_slide_visual(FakeVisualRuntime, object(), scene, "cricket", set(), set(), "noisy title")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Visual search should fail after the locked subject is rejected")

    assert calls == ["India"]


def test_unicode_primary_subject_is_preserved():
    for entity in ("विराट कोहली", "భారతదేశం", "محمد صلاح"):
        scene = {
            "primary_entity": entity,
            "voiceover": f"{entity} is the subject of this slide.",
            "specific_search_prompt": f"{entity} latest press conference",
            "visual_intent": "person",
        }
        assert extract_slide_search_subjects(scene) == [entity]
        assert build_deep_queries(scene)[0] == [entity]
