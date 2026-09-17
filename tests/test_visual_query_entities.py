from PIL import Image
import io

from visual_strategy_runtime import build_deep_queries


def _jpeg_bytes():
    image = Image.new("RGB", (640, 480), (120, 120, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_slide_search_uses_multiple_clean_entities_from_same_script():
    scene = {
        "primary_entity": "India",
        "voiceover": "India's Indian cricket team selection is being discussed, with Rashid Khan also mentioned in the report.",
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

    assert queries == ["Rishabh Pant", "India", "India ODI squad"]
    assert all(term not in " ".join(queries).lower() for term in ("press", "conference", "2024", "person"))
    assert "odi" in queries[2].lower()


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


def test_runtime_verifies_the_subject_that_generated_the_query(monkeypatch):
    import visual_runtime

    scene = {
        "primary_entity": "India",
        "voiceover": "India's Indian cricket team selection is being discussed, with Rashid Khan also mentioned.",
        "visual_intent": "press conference person",
        "specific_search_prompt": "India Indian cricket team Rashid Khan interview press conference",
        "sport_or_topic_category": "cricket",
    }
    fake = _jpeg_bytes()
    seen = []

    monkeypatch.setattr(visual_runtime, "_build_search_variants", lambda *_args, **_kwargs: (["India", "Indian cricket team", "Rashid Khan"], "LOCATION"))
    monkeypatch.setattr(visual_runtime, "_source_plan", lambda *_args, **_kwargs: [("DDG", object())])
    monkeypatch.setattr(visual_runtime, "_call_fetcher_with_timeout", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(visual_runtime, "_verification_tier", lambda *_args, **_kwargs: "STRICT")
    monkeypatch.setattr(visual_runtime, "save_to_cache", lambda *_args, **_kwargs: None)

    outcomes = iter([
        (False, "STRICT", 50, True),
        (True, "STRICT", 100, False),
    ])

    def fake_gate(*_args, **kwargs):
        seen.append(kwargs.get("subject_override"))
        return next(outcomes)

    monkeypatch.setattr(visual_runtime, "_strict_gate", fake_gate)
    bot = type("Bot", (), {"get_image_hash": staticmethod(lambda data: str(len(data)))})()

    result = visual_runtime._relevant_asset(bot, scene, "cricket", set(), set(), "India story")

    assert result[2] == "DDG"
    assert seen == ["India", "Indian cricket team"]
