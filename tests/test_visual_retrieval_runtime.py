"""Regression coverage for the bounded content-first visual retrieval boundary."""

import io

from PIL import Image

import visual_retrieval_runtime as retrieval


def test_real_visual_candidate_reaches_verified_source(monkeypatch):
    image_buffer = io.BytesIO()
    Image.new("RGB", (900, 1200), (80, 90, 100)).save(image_buffer, format="JPEG", quality=95)
    image_bytes = image_buffer.getvalue()

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 4

        @staticmethod
        def _build_search_variants(seg, video_title=""):
            return ["India spinner"], "PERSON"

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return image_bytes

        @staticmethod
        def _strict_gate(bot, data, seg, video_title="", source=""):
            return True, "STRICT", 100, False

        @staticmethod
        def get_cached_asset(bot, entity, visual_type, context=""):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type: [("Commons", lambda *args: image_bytes)],
    )

    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        {
            "primary_entity": "India spinner",
            "factual_primary_entity": "India spinner",
            "visual_intent": "person portrait",
            "specific_search_prompt": "India spinner",
            "voiceover": "An Indian spinner is in focus.",
        },
        "cricket",
        set(),
        set(),
        "Cricket update",
    )

    assert image.size == (900, 1200)
    assert used_ai is False
    assert source == "Commons"
