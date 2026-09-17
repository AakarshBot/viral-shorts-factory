from visual_strategy_runtime import build_deep_queries, build_scene_visual_brief, classify_scene


def test_india_cricket_entity_resolution_and_progressive_queries():
    scene = {
        "primary_entity": "India",
        "voiceover": "India's white-ball stars rocket up the latest T20I rankings",
        "visual_intent": "cricket team",
        "specific_search_prompt": "India India India's white-ball stars rocket up latest T20I rankings nbsp nbsp ICC India's",
        "sport_or_topic_category": "cricket",
    }
    queries, visual_type = build_deep_queries(scene, "India T20I rankings")
    brief = build_scene_visual_brief(scene, "India T20I rankings", "cricket")

    assert visual_type == "LOCATION"
    assert brief["subject"] == "India cricket team"
    assert 1 <= len(queries) <= 3
    assert len({q.lower() for q in queries}) == len(queries)
    assert all("nbsp" not in q.lower() for q in queries)
    assert all(len(q.split()) <= 10 for q in queries)
    assert all("india cricket team" in q.lower() for q in queries)
    assert not any("india india" in q.lower() for q in queries)
    assert not any("white-ball stars rocket up latest" in q.lower() for q in queries)
    assert queries[0].lower() == "india cricket team"
    assert any("t20" in q.lower() for q in queries[1:])
    assert not any("rankings" in q.lower() for q in queries)


def test_entity_types_remain_stable():
    assert classify_scene({"primary_entity": "BCCI", "voiceover": "BCCI announced the decision", "visual_intent": "organization"}, "cricket") == "ORGANIZATION"
    assert classify_scene({"primary_entity": "Delhi", "voiceover": "Delhi hosted the event", "visual_intent": "location"}, "news") == "LOCATION"
    assert classify_scene({"primary_entity": "Lionel Messi", "voiceover": "Messi scored the winning goal", "visual_intent": "player"}, "football") == "PERSON"


def test_planner_never_returns_internal_labels_or_duplicates():
    scene = {
        "primary_entity": "Lionel Messi",
        "voiceover": "Lionel Messi scored the winning goal in the final",
        "visual_intent": "player portrait",
        "specific_search_prompt": "Lionel Messi World Cup final editorial_person",
        "sport_or_topic_category": "football",
    }
    queries, visual_type = build_deep_queries(scene, "Messi World Cup final")
    assert visual_type == "PERSON"
    assert all("editorial_person" not in q.lower() for q in queries)
    assert len({q.lower() for q in queries}) == len(queries)
    assert all("lionel messi" in q.lower() for q in queries)
    assert queries[0].lower() == "lionel messi"
