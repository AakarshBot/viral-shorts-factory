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
    assert queries == ["Amina Rahman"]
    assert visual_type == "PERSON"
    assert all(
        token not in queries[0].lower()
        for token in ("press", "conference", "latest", "person", "entertainment")
    )


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


def test_candidate_scene_rewrites_only_visual_runtime_metadata():
    source = {
        "primary_entity": "Not Northstar Research Summit",
        "voiceover": "Not just a one-off thing — Northstar Research Summit could return to Berlin &nbsp;.",
        "specific_search_prompt": "news_event",
        "visual_intent": "news_event",
    }

    candidate = build_candidate_scene(source, source["primary_entity"], "Northstar Research Summit")
    assert candidate["primary_entity"] != source["primary_entity"]
    assert candidate["specific_search_prompt"] == candidate["primary_entity"]
    assert candidate["voiceover"] == candidate["primary_entity"]
    assert candidate["factual_voiceover"] == source["voiceover"]
    assert "&nbsp;" not in candidate["voiceover"]
    assert candidate["visual_subject_locked"] is True


def test_visual_search_does_not_fall_back_to_raw_narration_or_category():
    calls = []

    class FakeVisualRuntime:
        VISUAL_MAX_SEARCH_QUERIES = 3

        @staticmethod
        def _relevant_asset(bot, scene, category, used_urls, used_hashes, video_title):
            calls.append(
                {
                    "entity": scene["primary_entity"],
                    "query_prompt": scene["specific_search_prompt"],
                    "voiceover": scene["voiceover"],
                }
            )
            raise RuntimeError("locked subject rejected")

    scene = {
        "primary_entity": "Not Northstar Research Summit",
        "voiceover": "Not just a one-off thing — Northstar Research Summit could continue hosting in Berlin &nbsp;.",
        "specific_search_prompt": "news_event",
        "visual_intent": "news_event",
    }

    try:
        search_slide_visual(FakeVisualRuntime, object(), scene, "business", set(), set(), "Northstar Research Summit")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Visual search should fail after the locked subject is rejected")

    assert len(calls) == 1
    call = calls[0]
    assert "not" not in call["entity"].lower().split()
    assert "not" not in call["query_prompt"].lower().split()
    assert "&nbsp;" not in call["voiceover"]
    assert "one-off" not in call["voiceover"].lower()
    assert "thing" not in call["voiceover"].lower()


def test_unicode_primary_subject_is_preserved():
    for entity in ("विराट कोहली", "భారతదేశం", "محمد صلاح"):
        scene = {
            "primary_entity": entity,
            "voiceover": f"{entity} is the subject of this slide.",
            "specific_search_prompt": f"{entity} latest interview",
            "visual_intent": "person",
        }
        assert extract_slide_search_subjects(scene) == [entity]
        assert build_deep_queries(scene)[0] == [entity]
        assert classify_scene(scene) == "PERSON"
