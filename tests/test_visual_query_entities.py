from visual_strategy_runtime import build_deep_queries


def test_slide_search_uses_multiple_clean_entities_from_same_script():
    scene = {
        "primary_entity": "India",
        "voiceover": (
            "India's Indian cricket team selection is being discussed, with Rashid Khan also mentioned in the report."
        ),
        "visual_intent": "press conference person",
        "specific_search_prompt": "India Indian cricket team Rashid Khan interview press conference 2024 person",
        "sport_or_topic_category": "cricket",
    }

    queries, _ = build_deep_queries(scene, "India cricket update")

    assert queries == ["India", "Rashid Khan", "Indian cricket team"]


def test_slide_search_does_not_turn_context_into_queries():
    scene = {
        "primary_entity": "Rishabh Pant",
        "voiceover": "Rishabh Pant was omitted from India's ODI squad after a selection meeting.",
        "visual_intent": "press conference person",
        "specific_search_prompt": "Rishabh Pant ODI players press conference 2024 person",
        "sport_or_topic_category": "cricket",
    }

    queries, _ = build_deep_queries(scene, "Rishabh Pant ODI omission")

    assert queries == ["Rishabh Pant", "India"]
    assert all(term not in " ".join(queries).lower() for term in ("odi", "press", "conference", "2024", "person"))


def test_multilingual_primary_subject_stays_available():
    for entity in ("विराट कोहली", "భారతదేశం", "محمد صلاح"):
        scene = {
            "primary_entity": entity,
            "voiceover": f"{entity} is discussed in this slide.",
            "visual_intent": "person",
            "specific_search_prompt": f"{entity} latest news press conference",
            "sport_or_topic_category": "sports",
        }
        queries, _ = build_deep_queries(scene, "Noisy title")
        assert queries[0] == entity
