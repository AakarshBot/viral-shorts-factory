"""Regression coverage for the bounded content-first visual retrieval boundary."""

import io

from PIL import Image

import visual_retrieval_runtime as retrieval


def test_image_hash_deduplicates_different_file_encodings():
    from PIL.PngImagePlugin import PngInfo

    image = Image.new("RGB", (320, 240), (120, 140, 160))

    first = io.BytesIO()
    image.save(first, format="PNG")

    metadata = PngInfo()
    metadata.add_text("provider", "alternate")
    second = io.BytesIO()
    image.save(second, format="PNG", pnginfo=metadata)

    assert retrieval._hash_image(object(), first.getvalue()) == retrieval._hash_image(
        object(), second.getvalue()
    )





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
            return _licensed_candidate(image_bytes, "cc0")

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
        lambda bot, visual_type: [("Commons", lambda *args: [_licensed_candidate(image_bytes, "cc0")])],
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



def _licensed_candidate(image_bytes, license_name="cc0"):
    return {
        "bytes": image_bytes,
        "provenance": {
            "provider": "Commons",
            "url": "https://commons.wikimedia.org/wiki/File:Test.jpg",
            "author": "Test Author",
            "license": license_name,
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/" if license_name == "cc0" else "https://creativecommons.org/licenses/by/4.0/",
        },
    }


def _jpeg_bytes(size=(900, 1200)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (80, 90, 100)).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def test_canonical_person_source_still_passes_visual_qc(monkeypatch):
    image_bytes = _jpeg_bytes()

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 8
        calls = 0

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT(person)"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

        @staticmethod
        def _strict_gate(*args, **kwargs):
            FakeRuntime.calls += 1
            return True, "STRICT(person)", 100, False

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [("Wikipedia", lambda *args: [_licensed_candidate(image_bytes, "by")])],
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
    assert FakeRuntime.calls == 1


def test_person_action_canonical_source_and_cache_require_semantic_qa(monkeypatch):
    image_bytes = _jpeg_bytes()

    class FakeBot:
        pass

    calls = {"qa": 0}

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 8

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT(person)"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

        @staticmethod
        def _strict_gate(*args, **kwargs):
            calls["qa"] += 1
            return False, "STRICT(person)", 35, True

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return retrieval.Image.open(io.BytesIO(image_bytes)).convert("RGB"), "cached"

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [("Commons", lambda *args: [_licensed_candidate(image_bytes, "cc0")])],
    )

    _image, _used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        {
            "primary_entity": "Pat Cummins",
            "factual_primary_entity": "Pat Cummins",
            "visual_intent": "interview",
            "specific_search_prompt": "Pat Cummins interview",
            "voiceover": "Pat Cummins is speaking in an interview.",
        },
        "cricket",
        set(),
        set(),
        "Pat Cummins interview",
    )

    assert calls["qa"] >= 1
    assert source == "visual-rescue"
    assert retrieval._trusted_source_evidence(
        "Commons", "PERSON", "Pat Cummins interview", "PERSON_ACTION"
    )[0] is False


def test_commons_logo_still_passes_visual_qc(monkeypatch):
    from visual_taxonomy_runtime import classify_visual_genre

    assert classify_visual_genre({"visual_intent": "logo", "voiceover": "The BCCI logo appears on screen."}, "BCCI logo", "ORGANIZATION") == "ORG_BRANDING"

    image_bytes = _jpeg_bytes((900, 900))

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 8
        calls = 0

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

        @staticmethod
        def _strict_gate(*args, **kwargs):
            FakeRuntime.calls += 1
            return True, "STRICT", 100, False

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [("Commons", lambda *args: [image_bytes])],
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
    assert FakeRuntime.calls == 1


def test_generic_provider_semantic_no_is_hard_rejected(monkeypatch):
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

    assert image.size == (1080, 1920)
    assert used_ai is False
    assert source == "visual-rescue"



def test_retrieval_rejects_strict_gate_exception_instead_of_using_uncertain_candidate(monkeypatch):
    image_bytes = _jpeg_bytes()

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 2

        @staticmethod
        def _build_search_variants(seg, video_title=""):
            return ["BCCI headquarters"], "ORGANIZATION"

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

        @staticmethod
        def _strict_gate(*args, **kwargs):
            raise TypeError("simulated QA bridge mismatch")

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [("DDG", lambda *args: [image_bytes])],
    )

    _image, _used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        {
            "primary_entity": "BCCI",
            "factual_primary_entity": "BCCI",
            "visual_intent": "headquarters",
            "specific_search_prompt": "BCCI headquarters",
            "voiceover": "The BCCI headquarters is shown.",
        },
        "cricket",
        set(),
        set(),
        "BCCI headquarters story",
    )

    assert source == "visual-rescue"


def test_strict_gemini_bridge_accepts_visual_genre_argument():
    import inspect
    import visual_runtime

    params = inspect.signature(visual_runtime._strict_gemini_check).parameters
    assert "visual_genre" in params
