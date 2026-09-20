import io

from PIL import Image

import visual_retrieval_runtime as retrieval
import visual_qa_runtime as visual_qa
from visual_query_entities_runtime import search_slide_visual


def _jpeg_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (900, 1200), (80, 90, 100)).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_automatic_visual_search_fails_closed_without_grounding():
    class FakeVisualRuntime:
        pass

    scene = {
        "primary_entity": "Indian Men's Cricket Suffer",
        "visual_entity_grounded": False,
        "specific_search_prompt": "Indian Men's Cricket Suffer",
        "visual_intent": "person portrait",
        "voiceover": "Unsupported identity.",
    }

    try:
        search_slide_visual(
            FakeVisualRuntime(),
            object(),
            scene,
            "cricket",
            set(),
            set(),
            "Test story",
        )
    except RuntimeError as exc:
        assert "not grounded" in str(exc).lower()
    else:
        raise AssertionError("Ungrounded automatic visual identity was allowed to search")


def test_manual_visual_query_remains_authoritative_without_grounding():
    calls = []

    class FakeVisualRuntime:
        @staticmethod
        def _relevant_asset(bot, scene, category, used_urls, used_hashes, video_title):
            calls.append(scene)
            return _jpeg_bytes(), False, "manual"

    scene = {
        "primary_entity": "Unsupported generated identity",
        "visual_entity_grounded": False,
        "manual_visual_query": "Rishabh Pant press conference",
        "specific_search_prompt": "Unsupported generated identity",
        "visual_intent": "person portrait",
        "voiceover": "Manual query test.",
    }

    result = search_slide_visual(
        FakeVisualRuntime(),
        object(),
        scene,
        "cricket",
        set(),
        set(),
        "Test story",
    )

    assert result[2] == "manual"
    assert calls[0]["manual_visual_query"] == "Rishabh Pant press conference"


def test_visual_batch_qc_is_the_active_retrieval_boundary(monkeypatch):
    image = _jpeg_bytes()
    calls = {"qa": 0}

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 8

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher()

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    def fake_batch(images, *args, **kwargs):
        calls["qa"] += 1
        return {index: None for index in range(len(images))}

    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", fake_batch)
    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [
            ("Commons", lambda *args: [{"bytes": image, "provenance": {
                "provider": "Commons",
                "url": "https://commons.wikimedia.org/wiki/File:Test.jpg",
                "author": "Test",
                "license": "cc0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            }} for _ in range(4)])
        ],
    )

    scene = {
        "primary_entity": "India cricket team",
        "factual_primary_entity": "India cricket team",
        "visual_intent": "team",
        "specific_search_prompt": "India cricket team",
        "voiceover": "India cricket team update.",
    }

    _image_out, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        scene,
        "cricket",
        set(),
        set(),
        "India cricket team",
    )

    assert calls["qa"] <= 2
    assert used_ai is False
    assert source == "visual-rescue"
    assert scene["visual_rejection_counts"]["final_rescue"] == 1
