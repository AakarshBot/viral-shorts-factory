"""Regression coverage for the bounded content-first visual retrieval boundary."""

import io

import visual_qa_runtime as visual_qa

import visual_provider_boundary_runtime as provider_boundary

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





def test_license_is_checked_before_semantic_qa(monkeypatch):
    image_bytes = _jpeg_bytes()

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 4
        qa_calls = 0

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher()

        @staticmethod
        def _strict_gate(*args, **kwargs):
            FakeRuntime.qa_calls += 1
            return True, "STRICT", 100, False

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    bad = {
        "bytes": image_bytes,
        "provenance": {
            "provider": "Openverse",
            "url": "https://example.test/bad.jpg",
            "author": "Example",
            "license": "cc-by-nc",
            "license_url": "",
        },
    }
    good = _licensed_candidate(image_bytes, "cc0")

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [("Commons", lambda *args: [bad, good])],
    )

    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", lambda images, *args, **kwargs: {index: True for index in range(len(images))})

    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        {
            "primary_entity": "India",
            "factual_primary_entity": "India",
            "visual_intent": "match",
            "specific_search_prompt": "India match",
            "voiceover": "India match update.",
            "manual_visual_query": "India match",
        },
        "news",
        set(),
        set(),
        "India match",
    )

    assert image.size == (900, 1200)
    assert used_ai is False
    assert source == "Commons"
    assert FakeRuntime.qa_calls == 0


def test_failed_semantic_candidates_never_become_final_visual(monkeypatch):
    image_bytes = _jpeg_bytes()

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 2

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher()

        @staticmethod
        def _strict_gate(*args, **kwargs):
            return False, "STRICT:SEMANTIC_NO", 50, True

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [
            ("Wikipedia", lambda *args: [{
                "bytes": image_bytes,
                "provenance": {
                    "provider": "Wikipedia",
                    "url": "https://commons.wikimedia.org/wiki/File:Test.jpg",
                    "author": "Test",
                    "license": "cc0",
                    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                },
            }]),
            ("Pexels", lambda *args: [{
                "bytes": image_bytes,
                "provenance": {
                    "provider": "Pexels",
                    "url": "https://www.pexels.com/photo/test/",
                    "author": "Test",
                    "license": "Pexels License",
                    "license_url": "https://www.pexels.com/license/",
                },
            }]),
        ],
    )

    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", lambda images, *args, **kwargs: {index: False for index in range(len(images))})

    scene = {
        "primary_entity": "Sanju Samson",
        "factual_primary_entity": "Sanju Samson",
        "visual_intent": "person portrait",
        "specific_search_prompt": "Sanju Samson",
        "voiceover": "Sanju Samson is in focus.",
    }
    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        scene,
        "cricket",
        set(),
        set(),
        "Sanju Samson story",
    )

    assert image.size == (1080, 1920)
    assert used_ai is False
    assert source == "visual-rescue"
    assert scene["visual_qc_blocked"] is False
    assert scene["visual_qc_block_reason"] == ""
    assert int(scene["visual_rejection_counts"].get("semantic_no") or 0) >= 1


def test_provider_plan_skips_unconfigured_optional_providers(monkeypatch):
    import visual_provider_boundary_runtime as boundary

    for name in ("PIXABAY_API_KEY", "PEXELS_API_KEY", "UNSPLASH_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)

    plan = boundary.build_raw_source_plan("GENERAL_CONTEXT", "PLACE_SCENE")
    names = {name for name, _fetcher in plan}
    assert "Openverse" in names
    assert "Pixabay" not in names
    assert "Pexels" not in names
    assert "Unsplash" not in names


def test_rejection_accounting_preserves_entity_qc_reasons():
    scene = {}
    retrieval._record_visual_rejection(scene, "semantic_no", "ENTITY_NO")
    retrieval._record_visual_rejection(scene, "semantic_uncertain", "ENTITY_UNCERTAIN")
    assert scene["visual_rejection_counts"] == {
        "semantic_no": 1,
        "semantic_uncertain": 1,
    }


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

    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", lambda images, *args, **kwargs: {index: True for index in range(len(images))})

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



def test_retrieval_spreads_semantic_qa_across_providers(monkeypatch):
    candidates_one = []
    candidates_two = []
    for index in range(4):
        image = Image.new("RGB", (900, 1200), (40 + index * 20, 70, 100))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        candidates_one.append(_licensed_candidate(buffer.getvalue(), "cc0"))
        image = Image.new("RGB", (900, 1200), (100, 70 + index * 20, 40))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        candidates_two.append(_licensed_candidate(buffer.getvalue(), "cc0"))

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 4

        @staticmethod
        def _verification_tier(seg, visual_type, source):
            return "STRICT"

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher()

        @staticmethod
        def _strict_gate(bot, data, seg, video_title="", source=""):
            # The second provider has the acceptable first candidate. Under the
            # old serial candidate loop, the first provider could consume all
            # four QA checks before this provider was considered.
            return (
                source == "ProviderTwo",
                "STRICT",
                100 if source == "ProviderTwo" else 0,
                source != "ProviderTwo",
            )

        @staticmethod
        def get_cached_asset(bot, entity, visual_type, context=""):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [
            ("ProviderOne", lambda *args: list(candidates_one)),
            ("ProviderTwo", lambda *args: list(candidates_two)),
        ],
    )

    batch_payloads = []
    def fake_batch(images, *args, **kwargs):
        batch_payloads.append(list(images))
        return {index: False for index in range(len(images))}
    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", fake_batch)

    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        {
            "primary_entity": "India",
            "factual_primary_entity": "India",
            "visual_intent": "match",
            "specific_search_prompt": "India match",
            "voiceover": "India match update.",
        },
        "news",
        set(),
        set(),
        "India match",
    )

    assert image.size == (1080, 1920)
    assert used_ai is False
    assert batch_payloads
    assert len(batch_payloads[0]) == 8
    provider_one_bytes = candidates_one[0]["bytes"]
    provider_two_bytes = candidates_two[0]["bytes"]
    assert any(candidate == provider_one_bytes for candidate in batch_payloads[0])
    assert any(candidate == provider_two_bytes for candidate in batch_payloads[0])
    assert source == "visual-rescue"

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

    batch_calls = {"count": 0}
    def fake_batch(images, *args, **kwargs):
        batch_calls["count"] += 1
        return {index: True for index in range(len(images))}
    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", fake_batch)

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
    assert batch_calls["count"] >= 1


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

    scene = {
        "primary_entity": "Pat Cummins",
        "factual_primary_entity": "Pat Cummins",
        "visual_intent": "interview",
        "specific_search_prompt": "Pat Cummins interview",
        "voiceover": "Pat Cummins is speaking in an interview.",
    }
    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        scene,
        "cricket",
        set(),
        set(),
        "Pat Cummins interview",
    )

    assert calls["qa"] >= 1
    assert image.size == (1080, 1920)
    assert used_ai is False
    assert source == "visual-rescue"
    assert scene["visual_qc_blocked"] is False
    assert scene["visual_qc_block_reason"] == ""
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
        lambda bot, visual_type, visual_genre="": [("Commons", lambda *args: [_licensed_candidate(image_bytes, "cc0")])],
    )

    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", lambda images, *args, **kwargs: {index: True for index in range(len(images))})

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
    assert FakeRuntime.calls == 0


def test_generic_provider_semantic_no_is_rejected_safely(monkeypatch):
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
            return False, "STRICT:SEMANTIC_NO", 70, True

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type: [("DDG", lambda *args: [_licensed_candidate(image_bytes, "cc0")])],
    )

    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", lambda images, *args, **kwargs: {index: False for index in range(len(images))})

    scene = {
        "primary_entity": "Sanju Samson",
        "factual_primary_entity": "Sanju Samson",
        "visual_intent": "person portrait",
        "specific_search_prompt": "Sanju Samson",
        "voiceover": "Sanju Samson is in focus.",
    }
    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        FakeBot(),
        scene,
        "cricket",
        set(),
        set(),
        "Sanju Samson story",
    )

    assert image.size == (1080, 1920)
    assert used_ai is False
    assert source == "visual-rescue"
    rejection_counts = scene["visual_rejection_counts"]
    assert int(rejection_counts.get("semantic_no") or 0) >= 1


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


def test_provider_search_metadata_survives_provenance_wrapping():
    from visual_licensing_runtime import licensed_candidate

    candidate = licensed_candidate(
        b"image-bytes",
        {
            "provider": "Pexels",
            "url": "https://www.pexels.com/photo/test/",
            "author": "Test",
            "license": "Pexels License",
            "license_url": "https://www.pexels.com/license/",
            "search_title": "Rishabh Pant press conference",
            "search_tags": ["Rishabh Pant", "press conference"],
            "search_position": 1,
        },
    )

    assert candidate["search_title"] == "Rishabh Pant press conference"
    assert candidate["search_tags"] == ["Rishabh Pant", "press conference"]
    assert candidate["search_position"] == 1


def test_candidate_metadata_outweighs_resolution_in_qa_ordering():
    from visual_retrieval_runtime import _candidate_priority

    image_buffer = io.BytesIO()
    Image.new("RGB", (1600, 900), (80, 90, 100)).save(image_buffer, format="JPEG", quality=95)
    strong = _candidate_priority(
        "Pexels",
        image_buffer.getvalue(),
        "PERSON",
        "Rishabh Pant press conference",
        "PERSON_ACTION",
        data={
            "search_title": "Rishabh Pant press conference",
            "search_description": "Rishabh Pant speaks to reporters",
            "search_position": 1,
        },
    )

    weak_buffer = io.BytesIO()
    Image.new("RGB", (1080, 1920), (80, 90, 100)).save(weak_buffer, format="JPEG", quality=95)
    weak = _candidate_priority(
        "Pexels",
        weak_buffer.getvalue(),
        "PERSON",
        "Rishabh Pant press conference",
        "PERSON_ACTION",
        data={
            "search_title": "generic sports stadium",
            "search_description": "crowd at a sports venue",
            "search_position": 2,
        },
    )

    assert strong > weak


def test_retrieval_uses_multiple_candidates_from_one_provider_before_next_query(monkeypatch):
    image_bytes = _jpeg_bytes()
    calls = []

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 3

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            calls.append((source, query))
            return fetcher(*args)

        @staticmethod
        def _strict_gate(*args, **kwargs):
            return False, "STRICT:SEMANTIC_NO", 0, True

        @staticmethod
        def get_cached_asset(*args, **kwargs):
            return None, None

        @staticmethod
        def save_to_cache(*args, **kwargs):
            return None

    candidates = []
    for value in (40, 100, 160):
        buffer = io.BytesIO()
        Image.new("RGB", (900, 1200), (value, 70, 100)).save(buffer, format="JPEG", quality=95)
        candidates.append(_licensed_candidate(buffer.getvalue(), "cc0"))
    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [("ProviderOne", lambda *inner: candidates)],
    )

    image, used_ai, source = retrieval.run_visual_retrieval(
        FakeRuntime(),
        object(),
        {
            "primary_entity": "India",
            "factual_primary_entity": "India",
            "visual_intent": "match",
            "specific_search_prompt": "India match",
            "voiceover": "India match update.",
        },
        "news",
        set(),
        set(),
        "India match",
    )

    assert image.size == (1080, 1920)
    assert used_ai is False
    assert source == "visual-rescue"
    assert calls == [
        ("ProviderOne", "India match"),
        ("ProviderOne", "India match update"),
    ]


def test_strict_gemini_bridge_accepts_visual_genre_argument():
    import inspect
    import visual_runtime

    params = inspect.signature(visual_runtime._strict_gemini_check).parameters
    assert "visual_genre" in params

def test_manual_queries_build_one_shared_twenty_image_pool(monkeypatch):
    query_values = ["Rishabh Pant", "BCCI logo", "India cricket team", "New Delhi stadium"]
    image_candidates = {}
    for query_index, query in enumerate(query_values):
        items = []
        for image_index in range(5):
            buffer = io.BytesIO()
            Image.new(
                "RGB",
                (900, 1200),
                (20 + query_index * 40, 40 + image_index * 20, 80),
            ).save(buffer, format="JPEG", quality=95)
            items.append(
                _licensed_candidate(
                    buffer.getvalue(),
                    "cc0",
                ) | {
                    "search_title": f"{query} image {image_index + 1}",
                    "search_position": image_index + 1,
                }
            )
        image_candidates[query] = items

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [
            ("Commons", lambda *args, query=image_candidates.get(args[0], []): query)
        ],
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {
            index: True for index in range(len(images))
        },
    )

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [{"primary_entity": "Rishabh Pant", "voiceover": "Rishabh Pant press conference."}],
        query_values,
        "Test story",
    )

    assert len(result["assets"]) == 20
    assert [item["verified"] for item in result["query_stats"]] == [5, 5, 5, 5]
    assert all(stat["qa_requests"] == 1 for stat in result["query_stats"])


def test_manual_pool_retains_entity_verified_soft_resolution_candidate(monkeypatch):
    low = io.BytesIO()
    Image.new("RGB", (400, 600), (50, 60, 70)).save(low, format="JPEG", quality=95)

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [
            ("Commons", lambda *args: [
                _licensed_candidate(low.getvalue(), "cc0"),
            ])
        ],
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {index: True for index in range(len(images))},
    )

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [{"primary_entity": "Test Subject", "voiceover": "Test Subject is visible."}],
        ["Test Subject"],
        "Test story",
    )

    assert len(result["assets"]) == 1
    assert result["assets"][0]["status"] == "factory-rejected-resolution"
    assert result["rejection_counts"]["resolution_soft"] == 1


def test_manual_pool_scene_selection_prefers_scene_relevant_candidate():
    assets = [
        {
            "hash": "press",
            "query": "Rishabh Pant",
            "search_text": "Rishabh Pant press conference",
            "priority": 60,
            "status": "entity-verified",
        },
        {
            "hash": "match",
            "query": "Rishabh Pant",
            "search_text": "Rishabh Pant cricket match",
            "priority": 70,
            "status": "entity-verified",
        },
    ]
    selected = retrieval.select_manual_visual_candidate(
        assets,
        {
            "factual_primary_entity": "Rishabh Pant",
            "visual_intent": "press conference",
            "specific_search_prompt": "Rishabh Pant press conference",
            "voiceover": "Rishabh Pant speaks at a press conference.",
        },
        set(),
    )
    assert selected["hash"] == "press"
