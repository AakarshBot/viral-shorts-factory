import visual_query_entities_runtime as query_runtime

from visual_query_entities_runtime import (
    build_candidate_scene,
    extract_slide_search_subjects,
    lock_visual_subject,
    search_slide_visual,
)
from visual_semantic_guard_runtime import build_query_ladder, clean_text, resolve_subject
from visual_strategy_runtime import build_deep_queries, classify_scene


def test_person_subject_is_preserved_without_prompt_padding():
    scene = {
        "primary_entity": "Amina Rahman",
        "voiceover": "Amina Rahman presented the documentary at the festival.",
        "specific_search_prompt": "Amina Rahman latest press conference person",
        "visual_intent": "person portrait",
        "sport_or_topic_category": "entertainment",
    }
    assert lock_visual_subject(scene) == "Amina Rahman"
    queries, visual_type = build_deep_queries(scene, "Amina Rahman documentary")
    assert queries[0] == "Amina Rahman"
    assert len(queries) <= 5
    assert visual_type == "PERSON"
    assert all("press conference" not in query.lower() for query in queries[:1])
    assert all("latest" not in query.lower() for query in queries[:1])
    assert all("entertainment" not in query.lower() for query in queries[:1])


def test_descriptive_visual_subject_gets_bounded_identity_preserving_fallbacks():
    scene = {
        "primary_entity": "Indian athletes",
        "voiceover": "Indian athletes arrived in Nagoya for the Asian Games.",
        "specific_search_prompt": "Indian athletes Nagoya Asian Games arrival",
        "visual_intent": "event",
    }

    resolution = resolve_subject(scene, "Asian Games story")
    assert resolution["subject"] == "Indian athletes Nagoya Asian Games arrival"
    assert resolution["visual_type"] == "EVENT"

    queries, visual_type, _ = build_query_ladder(scene, "Asian Games story")
    assert visual_type == "EVENT"
    assert queries == [
        "Indian athletes Nagoya Asian Games arrival",
        "Indian athletes Nagoya Asian Games",
        "Indian athletes",
    ]
    assert all("sports" not in q.lower() for q in queries)
    assert all("cricket" not in q.lower() for q in queries)
    assert all("event" not in q.lower().split() for q in queries)


def test_exact_logo_subject_keeps_exact_first_query_and_identity_fallback():
    scene = {
        "primary_entity": "ICC logo",
        "voiceover": "The ICC logo represents the International Cricket Council.",
        "specific_search_prompt": "ICC logo",
        "visual_intent": "event",
    }

    resolution = resolve_subject(scene, "ICC logo story")
    assert resolution["subject"] == "ICC logo"
    assert resolution["visual_type"] == "ORGANIZATION"

    queries, visual_type, _ = build_query_ladder(scene, "ICC logo story")
    assert visual_type == "ORGANIZATION"
    assert queries == ["ICC logo", "ICC"]
    assert queries[0] == "ICC logo"
    assert all("sports" not in q.lower() for q in queries)
    assert all("event" not in q.lower().split() for q in queries)


def test_team_identity_overrides_stale_person_type_hint():
    scene = {
        "primary_entity": "India cricket team",
        "voiceover": "India cricket team in action during the match.",
        "specific_search_prompt": "India cricket team in action during match",
        "visual_intent": "team in action during match",
        "visual_type": "PERSON",
    }
    resolution = resolve_subject(scene, "India cricket team story")
    assert resolution["subject"] == "India cricket team"
    assert resolution["visual_type"] == "ORGANIZATION"
    queries, visual_type = build_deep_queries(scene, "India cricket team story")
    assert queries[0] == "India cricket team"
    assert visual_type == "ORGANIZATION"


def test_logo_identity_overrides_stale_event_type_hint():
    scene = {
        "primary_entity": "Deccan Herald logo",
        "voiceover": "The Deccan Herald logo identifies the newspaper.",
        "specific_search_prompt": "Deccan Herald logo",
        "visual_intent": "news_event",
        "visual_type": "EVENT",
    }
    resolution = resolve_subject(scene, "Deccan Herald logo story")
    assert resolution["subject"] == "Deccan Herald logo"
    assert resolution["visual_type"] == "ORGANIZATION"
    queries, visual_type = build_deep_queries(scene, "Deccan Herald logo story")
    assert queries[0] == "Deccan Herald logo"
    assert visual_type == "ORGANIZATION"


def test_malformed_leading_negation_is_removed_and_context_grounded():
    scene = {
        "primary_entity": "Not Northstar Research Summit",
        "voiceover": "Not just a one-off thing — Northstar Research Summit could continue hosting in Berlin next year.",
        "specific_search_prompt": "news_event",
        "visual_intent": "news_event",
        "sport_or_topic_category": "business",
    }

    resolution = resolve_subject(scene, "Northstar Research Summit")
    assert resolution["visual_type"] == "EVENT"
    assert "not" not in resolution["subject"].lower().split()
    assert "nbsp" not in resolution["subject"].lower()
    assert "one-off" not in resolution["subject"].lower()
    assert "thing" not in resolution["subject"].lower()
    assert "northstar" in resolution["subject"].lower()
    assert "summit" in resolution["subject"].lower()

    queries, visual_type, _ = build_query_ladder(scene, "Northstar Research Summit")
    assert visual_type == "EVENT"
    assert queries
    assert queries[0] == resolution["subject"]
    assert all("business" not in q.lower() for q in queries)
    assert all("event" not in q.lower() or "summit" in q.lower() for q in queries)
    assert all("not" not in q.lower().split() for q in queries)


def test_html_noise_is_removed_from_visual_subject_and_query():
    scene = {
        "primary_entity": "Not Aurora",
        "voiceover": "Not just a one-off thing — Aurora returned to the exhibition hall in Berlin &nbsp;.",
        "specific_search_prompt": "news_event",
        "visual_intent": "news_event",
    }
    resolution = resolve_subject(scene, "Aurora exhibition")
    assert clean_text("Aurora &nbsp; &amp;") == "Aurora &"
    assert "nbsp" not in resolution["subject"].lower()
    queries, _type, _ = build_query_ladder(scene, "Aurora exhibition")
    assert all("nbsp" not in q.lower() for q in queries)
    assert all("&nbsp;" not in q.lower() for q in queries)
    assert all("not" not in q.lower().split() for q in queries)


def test_candidate_scene_keeps_provenance_but_locks_clean_visual_subject():
    source = {
        "primary_entity": "Not Northstar Research Summit",
        "voiceover": "Not just a one-off thing — Northstar Research Summit could return to Berlin &nbsp;.",
        "specific_search_prompt": "news_event",
        "visual_intent": "news_event",
    }

    candidate = build_candidate_scene(source, source["primary_entity"], "Northstar Research Summit")
    assert candidate["primary_entity"] != source["primary_entity"]
    assert candidate["primary_entity"] == candidate["visual_search_subject"]
    assert candidate["factual_primary_entity"] == "Northstar Research Summit"
    assert candidate["original_primary_entity"] == source["primary_entity"]
    assert candidate["specific_search_prompt"] == clean_text(source["specific_search_prompt"])
    assert candidate["factual_voiceover"] == clean_text(source["voiceover"])
    assert "&nbsp;" not in candidate["voiceover"]
    assert candidate["visual_subject_locked"] is True


def test_visual_search_does_not_fall_back_to_raw_narration_or_category(monkeypatch):
    calls = []

    class FakeVisualRuntime:
        pass

    def fake_retrieval(runtime, bot, seg, category, used_urls, used_hashes, video_title):
        calls.append(
            {
                "entity": seg["primary_entity"],
                "query_prompt": seg["specific_search_prompt"],
                "voiceover": seg["voiceover"],
            }
        )
        raise RuntimeError("locked subject rejected")

    monkeypatch.setattr(query_runtime, "run_visual_retrieval", fake_retrieval)

    scene = {
        "primary_entity": "Not Northstar Research Summit",
        "voiceover": "Not just a one-off thing — Northstar Research Summit could continue hosting in Berlin &nbsp;.",
        "specific_search_prompt": "news_event",
        "visual_intent": "news_event",
    }

    try:
        search_slide_visual(FakeVisualRuntime(), object(), scene, "business", set(), set(), "Northstar Research Summit")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Visual search should fail after the locked subject is rejected")

    assert len(calls) == 1
    call = calls[0]
    assert "not" not in call["entity"].lower().split()
    assert "not" not in call["query_prompt"].lower().split()
    assert "&nbsp;" not in call["query_prompt"].lower()


def test_unicode_primary_subject_is_preserved():
    for entity in ("विराट कोहली", "భారతదేశం", "محمد صلاح"):
        scene = {
            "primary_entity": entity,
            "voiceover": f"{entity} is the subject of this slide.",
            "specific_search_prompt": f"{entity} latest interview",
            "visual_intent": "person",
        }
        assert extract_slide_search_subjects(scene) == [entity]
        assert build_deep_queries(scene)[0][0] == entity
        assert classify_scene(scene) == "PERSON"
