from visual_semantic_guard_runtime import meaningful_tokens
from visual_strategy_runtime import build_deep_queries, build_scene_visual_brief, classify_scene


def _assert_query_contract(scene, title, expected_type):
    brief = build_scene_visual_brief(scene, title, scene.get("sport_or_topic_category", ""))
    queries, visual_type = build_deep_queries(scene, title)

    assert visual_type == expected_type, (brief, visual_type)
    assert queries, "visual planner returned no query"
    assert brief["subject"] in queries[0], (brief, queries)
    assert len(queries) <= 5, queries
    subject_tokens = set(meaningful_tokens(brief["subject"]))
    assert subject_tokens, brief
    for query in queries:
        query_tokens = set(meaningful_tokens(query))
        assert subject_tokens.issubset(query_tokens), (brief, queries)
    assert all(token not in " ".join(queries).casefold() for token in ("editorial_person", "red carpet")), queries
    return brief, queries


def test_person_subject_uses_explicit_role():
    scene = {
        "primary_entity": "Amina Rahman",
        "voiceover": "Amina Rahman presented the new documentary.",
        "visual_intent": "person portrait",
        "specific_search_prompt": "Amina Rahman documentary press conference editorial_person",
        "sport_or_topic_category": "entertainment",
    }
    brief, queries = _assert_query_contract(scene, "Amina Rahman documentary", "PERSON")
    assert brief["subject"] == "Amina Rahman"
    assert queries[0].startswith("Amina Rahman")


def test_organization_subject_uses_generic_company_role():
    scene = {
        "primary_entity": "Northstar Labs",
        "voiceover": "Northstar Labs announced a new research program.",
        "visual_intent": "company",
        "sport_or_topic_category": "technology",
    }
    brief, _ = _assert_query_contract(scene, "Northstar Labs announcement", "ORGANIZATION")
    assert brief["subject"] == "Northstar Labs"


def test_product_subject_uses_explicit_device_role():
    scene = {
        "primary_entity": "Nova Phone 8",
        "voiceover": "Nova Phone 8 was announced with a redesigned camera.",
        "visual_intent": "product device",
        "sport_or_topic_category": "technology",
    }
    brief, _ = _assert_query_contract(scene, "Nova Phone 8 announcement", "PRODUCT")
    assert brief["subject"] == "Nova Phone 8"


def test_location_subject_uses_explicit_place_role():
    scene = {
        "primary_entity": "Central City",
        "voiceover": "Central City hosted the conference.",
        "visual_intent": "location geography",
        "sport_or_topic_category": "geography",
    }
    brief, _ = _assert_query_contract(scene, "Central City report", "LOCATION")
    assert brief["subject"] == "Central City"


def test_event_subject_uses_explicit_event_role():
    scene = {
        "primary_entity": "Global Climate Summit",
        "voiceover": "The Global Climate Summit opened with a new agreement.",
        "visual_intent": "event",
        "sport_or_topic_category": "climate",
    }
    brief, _ = _assert_query_contract(scene, "Global Climate Summit", "EVENT")
    assert brief["subject"] == "Global Climate Summit"


def test_concept_subject_uses_explicit_concept_role():
    scene = {
        "primary_entity": "quantum computing",
        "voiceover": "Quantum computing uses quantum states to process information.",
        "visual_intent": "scientific concept",
        "sport_or_topic_category": "science",
    }
    brief, _ = _assert_query_contract(scene, "Quantum computing explained", "CONCEPT")
    assert brief["subject"] == "quantum computing"


def test_contextual_collective_role_is_generic_not_domain_specific():
    scene = {
        "primary_entity": "Aurora",
        "voiceover": "The Aurora team members presented the research findings.",
        "visual_intent": "team members",
        "sport_or_topic_category": "research",
    }
    brief, queries = _assert_query_contract(scene, "Aurora research update", "ORGANIZATION")
    assert brief["subject"] == "Aurora"
    assert queries[0] == "Aurora"


def test_contextual_venue_role_is_generic_not_domain_specific():
    scene = {
        "primary_entity": "Central City",
        "voiceover": "The conference venue in Central City hosted the announcement.",
        "visual_intent": "conference venue",
        "sport_or_topic_category": "business",
    }
    brief, queries = _assert_query_contract(scene, "Central City conference", "LOCATION")
    assert brief["subject"] == "Central City"
    assert queries[0] == "Central City"


def test_multilingual_subjects_are_preserved():
    for entity in ("विराट कोहली", "భారతదేశం", "محمد صلاح"):
        scene = {
            "primary_entity": entity,
            "voiceover": f"{entity} is the subject of this slide.",
            "visual_intent": "person portrait",
            "specific_search_prompt": f"{entity} latest news press conference",
            "sport_or_topic_category": "international",
        }
        brief, queries = _assert_query_contract(scene, "Global story", "PERSON")
        assert brief["subject"] == entity
        assert queries[0].startswith(entity)


def test_entity_types_remain_stable_across_genres():
    cases = [
        ({"primary_entity": "City Hall", "voiceover": "City Hall announced the decision", "visual_intent": "institution"}, "ORGANIZATION"),
        ({"primary_entity": "Mars", "voiceover": "Mars is the geographic focus of the report", "visual_intent": "location geography"}, "LOCATION"),
        ({"primary_entity": "Dr. Lena Park", "voiceover": "Dr. Lena Park addressed the audience", "visual_intent": "person portrait"}, "PERSON"),
        ({"primary_entity": "The Solar Forum", "voiceover": "The Solar Forum opened today", "visual_intent": "event"}, "EVENT"),
        ({"primary_entity": "Gene therapy", "voiceover": "Gene therapy is the scientific concept discussed here", "visual_intent": "scientific concept"}, "CONCEPT"),
        ({"primary_entity": "Orion Laptop", "voiceover": "Orion Laptop was unveiled", "visual_intent": "product device"}, "PRODUCT"),
    ]
    for scene, expected_type in cases:
        brief, queries = _assert_query_contract(scene, "cross genre story", expected_type)
        assert brief["subject"] == scene["primary_entity"]
        assert queries[0] == scene["primary_entity"]


def test_query_ladder_is_bounded_and_never_degrades_identity():
    scene = {
        "primary_entity": "Amina Rahman",
        "voiceover": "Amina Rahman addressed the audience during the documentary premiere.",
        "visual_intent": "person portrait",
        "specific_search_prompt": "Amina Rahman documentary premiere editorial_person red carpet",
        "sport_or_topic_category": "entertainment",
    }
    brief, queries = _assert_query_contract(scene, "Noisy title that must never become the search query", "PERSON")
    assert queries[0].startswith(brief["subject"])
    assert len(queries) <= 5
    assert all("Noisy title".casefold() not in q.casefold() for q in queries)


def test_automatic_initial_query_never_uses_noisy_search_prompt():
    from visual_search_intent_runtime import resolve_visual_search_intent

    scene = {
        "primary_entity": "Rishabh Pant",
        "voiceover": "Rishabh Pant was omitted from India's ODI squad.",
        "specific_search_prompt": "Rishabh Pant ODI players press conference editorial_person latest news",
        "visual_intent": "press conference person",
    }

    intent = resolve_visual_search_intent(scene, "Rishabh Pant omission story")
    assert intent.query.startswith("Rishabh Pant")
    assert "press" not in intent.query.lower()
    assert "latest" not in intent.query.lower()
    assert "editorial" not in intent.query.lower()


def test_automatic_retry_is_one_compact_evidence_based_refinement():
    from visual_search_intent_runtime import resolve_visual_search_intent, reformulate_visual_query

    scene = {
        "primary_entity": "India",
        "voiceover": "India's cricket team trained in New Delhi before the final.",
        "visual_intent": "cricket team",
        "specific_search_prompt": "India cricket team New Delhi final press conference",
    }

    intent = resolve_visual_search_intent(scene)
    retry = reformulate_visual_query(intent, "no candidates")

    assert retry.startswith("India cricket")
    assert "press" not in retry.lower()
    assert "conference" not in retry.lower()
    assert "story" not in retry.lower()


def test_manual_visual_query_never_gets_automatic_retry():
    from visual_search_intent_runtime import resolve_visual_search_intent, reformulate_visual_query

    intent = resolve_visual_search_intent(
        {
            "primary_entity": "Pakistan Cricket Board",
            "manual_visual_query": "Mohammad Rizwan",
            "voiceover": "Pakistan Cricket Board announced the squad.",
        }
    )

    assert intent.manual is True
    assert intent.query == "Mohammad Rizwan"
    assert reformulate_visual_query(intent, "no candidates") == ""


def test_same_entity_gets_scene_specific_queries():
    from visual_search_intent_runtime import resolve_visual_search_intent

    slide_one = {
        "primary_entity": "Vaibhav Sooryavanshi",
        "visual_intent": "young batsman batting",
        "visual_context": "cricket match action",
    }
    slide_two = {
        "primary_entity": "Vaibhav Sooryavanshi",
        "visual_intent": "young player receiving award",
        "visual_context": "trophy presentation ceremony",
    }

    first = resolve_visual_search_intent(slide_one)
    second = resolve_visual_search_intent(slide_two)

    assert first.subject == second.subject == "Vaibhav Sooryavanshi"
    assert first.query != second.query
    assert "batting" in first.query.lower()
    assert "award" in second.query.lower()
    assert first.queries[-1] == first.subject
    assert second.queries[-1] == second.subject


def test_manual_visual_query_stays_exact():
    from visual_search_intent_runtime import resolve_visual_search_intent

    intent = resolve_visual_search_intent({
        "primary_entity": "Vaibhav Sooryavanshi",
        "manual_visual_query": "Vaibhav Sooryavanshi batting",
        "visual_intent": "trophy presentation",
    })

    assert intent.manual is True
    assert intent.query == "Vaibhav Sooryavanshi batting"
    assert intent.queries == ("Vaibhav Sooryavanshi batting",)
