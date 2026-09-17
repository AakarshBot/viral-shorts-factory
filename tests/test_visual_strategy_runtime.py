from visual_strategy_runtime import build_deep_queries, build_scene_visual_brief, classify_scene


def test_search_uses_exact_slide_entity_only():
    scene = {
        "primary_entity": "Rishabh Pant",
        "voiceover": "Rishabh Pant was omitted from India's ODI squad after the selection meeting.",
        "visual_intent": "press conference person",
        "specific_search_prompt": "Rishabh Pant ODI players press conference editorial_person",
        "sport_or_topic_category": "cricket",
    }
    queries, visual_type = build_deep_queries(scene, "Rishabh Pant omission from ODI squad")

    assert visual_type == "PERSON"
    assert queries == ["Rishabh Pant"]
    assert all("odi" not in q.lower() for q in queries)
    assert all("press" not in q.lower() for q in queries)
    assert all("conference" not in q.lower() for q in queries)
    assert all("editorial" not in q.lower() for q in queries)
    assert all("squad" not in q.lower() for q in queries)


def test_exact_search_preserves_multilingual_subject_text():
    for entity in ("विराट कोहली", "భారతదేశం", "محمد صلاح"):
        scene = {
            "primary_entity": entity,
            "voiceover": f"{entity} is the subject of this slide.",
            "visual_intent": "person",
            "specific_search_prompt": f"{entity} latest news press conference",
            "sport_or_topic_category": "sports",
        }
        queries, _ = build_deep_queries(scene, "Global sports headline")
        assert queries == [entity], (entity, queries)


def test_search_query_does_not_synthesize_context_for_non_person_subjects():
    cases = [
        ({"primary_entity": "BCCI", "visual_intent": "organization", "voiceover": "BCCI announced the decision", "sport_or_topic_category": "cricket"}, "BCCI"),
        ({"primary_entity": "2022 FIFA World Cup Final", "visual_intent": "actual match event", "voiceover": "The final went to penalties", "sport_or_topic_category": "football"}, "2022 FIFA World Cup Final"),
        ({"primary_entity": "quantum computing", "visual_intent": "technical process", "voiceover": "Quantum computers process information using quantum states", "sport_or_topic_category": "technology"}, "quantum computing"),
    ]
    for scene, expected in cases:
        queries, _ = build_deep_queries(scene, "A noisy global story title")
        assert queries == [expected], (scene, queries)


def test_india_cricket_brief_remains_available_but_is_not_a_search_rewrite():
    scene = {
        "primary_entity": "India",
        "voiceover": "India's white-ball stars move up the latest T20I rankings",
        "visual_intent": "cricket team",
        "specific_search_prompt": "India India India's white-ball stars rocket up latest T20I rankings nbsp ICC",
        "sport_or_topic_category": "cricket",
    }
    queries, visual_type = build_deep_queries(scene, "India T20I rankings")
    brief = build_scene_visual_brief(scene, "India T20I rankings", "cricket")

    assert visual_type == "LOCATION"
    assert brief["subject"] == "India cricket team"
    assert queries == ["India"]


def test_entity_types_remain_stable():
    assert classify_scene({"primary_entity": "BCCI", "voiceover": "BCCI announced the decision", "visual_intent": "organization"}, "cricket") == "ORGANIZATION"
    assert classify_scene({"primary_entity": "Delhi", "voiceover": "Delhi hosted the event", "visual_intent": "location"}, "news") == "LOCATION"
    assert classify_scene({"primary_entity": "Lionel Messi", "voiceover": "Messi scored the winning goal", "visual_intent": "player"}, "football") == "PERSON"


def test_planner_compatibility_surface_stays_clean():
    scene = {
        "primary_entity": "Lionel Messi",
        "voiceover": "Lionel Messi scored the winning goal in the final",
        "visual_intent": "player portrait",
        "specific_search_prompt": "Lionel Messi World Cup final editorial_person",
        "sport_or_topic_category": "football",
    }
    queries, visual_type = build_deep_queries(scene, "Messi World Cup final")
    assert visual_type == "PERSON"
    assert queries == ["Lionel Messi"]
