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



def _jpeg_bytes(size=(900, 1200)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (80, 90, 100)).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_canonical_person_source_bypasses_strict_semantic_false_positive(monkeypatch):
    image_bytes = _jpeg_bytes()

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 8

        @staticmethod
        def _build_search_variants(seg, video_title=""):
            return ["Sanju Samson"], "PERSON"

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT(person)"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

        @staticmethod
        def _strict_gate(*args, **kwargs):
            # Simulate the exact failure that used to throw away the real image.
            return False, "STRICT(person)", 50, True

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type: [("Wikipedia", lambda *args: [image_bytes])],
    )

    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        {
            "primary_entity": "Sanju Samson",
            "factual_primary_entity": "Sanju Samson",
            "visual_intent": "person portrait",
            "specific_search_prompt": "Sanju Samson",
            "voiceover": "Sanju Samson is in focus.",
        },
        "cricket",
        set(),
        set(),
        "Sanju Samson story",
    )

    assert image.size == (900, 1200)
    assert used_ai is False
    assert source == "Wikipedia"


def test_commons_logo_source_bypasses_strict_semantic_false_positive(monkeypatch):
    image_bytes = _jpeg_bytes((900, 900))

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 8

        @staticmethod
        def _build_search_variants(seg, video_title=""):
            return ["BCCI logo"], "ORGANIZATION"

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

        @staticmethod
        def _strict_gate(*args, **kwargs):
            return False, "STRICT", 45, True

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type: [("Commons", lambda *args: [image_bytes])],
    )

    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        {
            "primary_entity": "BCCI logo",
            "factual_primary_entity": "BCCI logo",
            "visual_intent": "logo",
            "specific_search_prompt": "BCCI logo",
            "voiceover": "The BCCI logo appears on screen.",
        },
        "cricket",
        set(),
        set(),
        "BCCI logo story",
    )

    assert image.size == (900, 900)
    assert used_ai is False
    assert source == "Commons"


def test_generic_provider_semantic_no_is_deprioritized_not_hard_rejected(monkeypatch):
    image_bytes = _jpeg_bytes()

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 2

        @staticmethod
        def _build_search_variants(seg, video_title=""):
            return ["Sanju Samson"], "PERSON"

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

        @staticmethod
        def _strict_gate(*args, **kwargs):
            return False, "STRICT", 70, True

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type: [("DDG", lambda *args: [image_bytes])],
    )

    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        {
            "primary_entity": "Sanju Samson",
            "factual_primary_entity": "Sanju Samson",
            "visual_intent": "person portrait",
            "specific_search_prompt": "Sanju Samson",
            "voiceover": "Sanju Samson is in focus.",
        },
        "cricket",
        set(),
        set(),
        "Sanju Samson story",
    )

    assert image.size == (900, 1200)
    assert used_ai is False
    assert source == "DDG"
