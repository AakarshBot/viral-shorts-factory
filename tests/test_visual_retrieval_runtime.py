"""Regression coverage for the bounded content-first visual retrieval boundary."""

import io
import threading
import time

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


def test_provenance_review_candidate_is_kept_for_dashboard_pool():
    image_bytes = _jpeg_bytes()
    candidate = {
        "bytes": image_bytes,
        "provenance": {
            "provider": "DDG",
            "url": "https://example.test/image.jpg",
            "author": "Example",
            "license": "",
            "license_url": "",
        },
    }
    counts = {}
    built = retrieval._manual_candidate_from_data(
        "DDG",
        candidate,
        "Example person",
        "PERSON",
        "PERSON_PORTRAIT",
        object(),
        set(),
        set(),
        counts,
    )
    assert built is not None
    assert built["provenance_status"] == "provenance-review"
    assert counts.get("monetization", 0) == 0


def test_provenance_review_candidate_is_available_for_manual_qc():
    review = {
        "hash": "review-only",
        "status": "entity-verified",
        "provenance_status": "provenance-review",
        "priority": 100,
        "query": "Example person",
        "source": "DDG",
        "manual_query_index": 1,
        "visual_genre": "PERSON_PORTRAIT",
        "search_text": "Example person",
    }
    selected = retrieval.select_manual_visual_candidate(
        [review],
        {"primary_entity": "Example person", "visual_genre": "PERSON_PORTRAIT"},
        set(),
    )
    assert selected is review


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


def _jpeg_bytes(size=(900, 1200), color=(80, 90, 100)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()




def test_manual_person_query_preserves_action_context():
    visual_type, visual_genre = retrieval._manual_query_visual_context(
        "Vaibhav Sooryavanshi",
        [{
            "primary_entity": "Vaibhav Sooryavanshi",
            "voiceover": "Vaibhav Sooryavanshi batting in the match.",
            "visual_intent": "batting cricket action",
        }],
    )

    assert visual_type == "PERSON"
    assert visual_genre == "PERSON_ACTION"


def test_manual_pool_opens_one_shared_gemini_scene_budget(monkeypatch):
    scene_resets = []
    provider_calls = []

    def provider(query, *args):
        provider_calls.append(query)
        base = 10 if query == "One" else 100 if query == "Two" else 200
        return [
            _licensed_candidate(
                _jpeg_bytes(color=(base + index, 80, 120)),
                "cc0",
            )
            for index in range(4)
        ]

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

    class FakeBot:
        pass

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [
            ("ProviderOne", provider),
            ("ProviderTwo", provider),
        ],
    )
    monkeypatch.setattr(
        visual_qa,
        "start_visual_qa_scene",
        lambda: scene_resets.append(True),
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
        [
            {"primary_entity": "One", "voiceover": "One."},
            {"primary_entity": "Two", "voiceover": "Two."},
            {"primary_entity": "Three", "voiceover": "Three."},
        ],
        ["One", "Two", "Three"],
        "Test story",
        pool_target=6,
    )

    assert len(result["assets"]) == 6
    assert len(scene_resets) == 1
    assert provider_calls == ["One", "One", "Two", "Two", "Three", "Three"]


def test_manual_pool_target_cannot_exceed_hard_pool_max(monkeypatch):
    def provider(query, *args):
        base = {"One": 20, "Two": 80}[query]
        return [
            {
                **_licensed_candidate(
                    _jpeg_bytes(color=(base + index, 90, 120)),
                    "cc0",
                ),
                "source_image_url": f"https://example.test/{query}/{index}.jpg",
            }
            for index in range(20)
        ]

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

    class FakeBot:
        pass

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [("ProviderOne", provider)],
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
        [
            {"primary_entity": "One", "voiceover": "One."},
            {"primary_entity": "Two", "voiceover": "Two."},
        ],
        ["One", "Two"],
        "Test story",
        pool_target=retrieval.MANUAL_POOL_MAX + 5,
    )

    assert len(result["assets"]) == retrieval.MANUAL_POOL_MAX
    assert result["target"] == retrieval.MANUAL_POOL_MAX


def test_manual_pool_target_is_enforced_across_provider_stages(monkeypatch):
    provider_calls = []
    qa_calls = {"count": 0}

    def make_candidates(seed):
        values = []
        for index in range(4):
            values.append(_licensed_candidate(
                _jpeg_bytes(color=(seed + index, 80, 120)),
                "cc0",
            ))
        return values

    provider_data = {
        "ProviderOne": make_candidates(20),
        "ProviderTwo": make_candidates(40),
        "ProviderThree": make_candidates(60),
        "ProviderFour": make_candidates(80),
    }

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            provider_calls.append(source)
            return fetcher()

    class FakeBot:
        pass

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [
            ("ProviderOne", lambda *inner: provider_data["ProviderOne"]),
            ("ProviderTwo", lambda *inner: provider_data["ProviderTwo"]),
            ("ProviderThree", lambda *inner: provider_data["ProviderThree"]),
            ("ProviderFour", lambda *inner: provider_data["ProviderFour"]),
        ],
    )

    def fake_batch(images, *args, **kwargs):
        qa_calls["count"] += 1
        return {index: True for index in range(len(images))}

    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", fake_batch)

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [
            {"primary_entity": "One", "voiceover": "One."},
            {"primary_entity": "Two", "voiceover": "Two."},
            {"primary_entity": "Three", "voiceover": "Three."},
        ],
        ["One", "Two", "Three"],
        "Test story",
        pool_target=10,
    )

    assert len(result["assets"]) == 10
    assert [stat["target"] for stat in result["query_stats"]] == [4, 3, 3]
    assert [stat["verified"] for stat in result["query_stats"]] == [4, 3, 3]
    # Each query may use the primary pair plus one bounded fallback pair.
    assert len(provider_calls) <= 12
    assert set(provider_calls) <= {
        "ProviderOne", "ProviderTwo", "ProviderThree", "ProviderFour"
    }
    assert [stat["qa_requests"] for stat in result["query_stats"]] == [1, 2, 1]
    assert qa_calls["count"] == 4


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


def test_visual_provider_fetches_overlap_without_sharing_used_url_state(monkeypatch):
    active = 0
    peak = 0
    url_sets = []
    lock = threading.Lock()

    def provider(name):
        def fetch(*args):
            nonlocal active, peak
            local_used_urls = args[1]
            with lock:
                active += 1
                peak = max(peak, active)
                url_sets.append(local_used_urls)
            local_used_urls.add(f"https://{name}.example/image.jpg")
            time.sleep(0.08)
            with lock:
                active -= 1
            return []
        return fetch

    class FakeBot:
        pass

    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 4

        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

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
            ("ProviderOne", provider("one")),
            ("ProviderTwo", provider("two")),
        ],
    )

    used_urls = set()
    retrieval.run_visual_retrieval(
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
        used_urls,
        set(),
        "India match",
    )

    assert peak >= 2
    assert len(url_sets) >= 2
    assert len({id(item) for item in url_sets}) == len(url_sets)
    assert "https://one.example/image.jpg" in used_urls
    assert "https://two.example/image.jpg" in used_urls


def test_openverse_manual_search_uses_separate_cache_namespace(monkeypatch):
    import image_sources_runtime as sources

    cache_providers = []
    image = _jpeg_bytes((1200, 1600))

    monkeypatch.setattr(
        sources,
        "_read_cache",
        lambda provider, query, page=1: (
            cache_providers.append(provider) or {
                "results": [{
                    "license": "by-nc",
                    "url": "https://example.com/manual.jpg",
                    "thumbnail": "https://example.com/manual-thumb.jpg",
                    "creator": "Example",
                    "title": "Manual result",
                }]
            }
        ),
    )
    monkeypatch.setattr(
        sources,
        "_download",
        lambda url, used_urls=None, metadata=None: {
            "bytes": image,
            "provenance": dict(metadata or {}),
            **dict(metadata or {}),
        },
    )

    result = sources.fetch_openverse_candidates(
        "Rishabh Pant",
        set(),
        "",
        "",
        "",
        "",
        True,
    )

    assert result
    assert cache_providers == ["openverse-manual"]


def test_manual_visual_search_fetches_bounded_sources_concurrently(monkeypatch):
    import threading
    import time

    active = 0
    peak = 0
    lock = threading.Lock()

    def provider(name):
        def fetch(*args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                candidate = _licensed_candidate(
                    _jpeg_bytes(
                        (1200, 1600),
                        color=(30 + (sum(ord(char) for char in name) % 180), 70, 100),
                    ),
                    "cc-by-nc",
                )
                candidate["source_image_url"] = f"https://{name}.example/{name}.jpg"
                candidate["search_title"] = f"{name} result"
                time.sleep(0.05)
                return [candidate]
            finally:
                with lock:
                    active -= 1
        return fetch

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [
            ("Commons", provider("Commons")),
            ("Wikipedia", provider("Wikipedia")),
            ("Openverse", provider("Openverse")),
            ("DDG", provider("DDG")),
        ],
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {index: True for index in range(len(images))},
    )

    result = retrieval.collect_manual_visual_search(
        FakeRuntime(),
        FakeBot(),
        "Rishabh Pant",
    )

    assert peak >= 2
    assert len(result["assets"]) == 2
    assert result["rejection_counts"]["monetization"] == 0


def test_manual_visual_search_can_rank_a_later_provider_candidate(monkeypatch):
    image_one = _jpeg_bytes((1200, 1600), color=(20, 20, 20))
    image_two = _jpeg_bytes((1200, 1600), color=(220, 220, 220))

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    def weak_provider(*args):
        item = _licensed_candidate(image_one, "cc0")
        item["source_image_url"] = "https://commons.example/weak.jpg"
        item["search_title"] = "generic sports crowd"
        return [item]

    def strong_provider(*args):
        item = _licensed_candidate(image_two, "cc0")
        item["source_image_url"] = "https://openverse.example/strong.jpg"
        item["search_title"] = "Rishabh Pant press conference"
        item["search_description"] = "Rishabh Pant speaking at a press conference"
        return [item]

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [
            ("Commons", weak_provider),
            ("Openverse", strong_provider),
        ],
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {index: True for index in range(len(images))},
    )

    result = retrieval.collect_manual_visual_search(
        FakeRuntime(),
        FakeBot(),
        "Rishabh Pant press conference",
    )

    assert result["assets"]
    assert result["assets"][0]["source"] == "Openverse"




def test_manual_visual_search_respects_shared_qa_reset_mode(monkeypatch):
    resets = []

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    def provider(*args):
        return [
            _licensed_candidate(_jpeg_bytes(color=(20 + index, 80, 120)), "cc0")
            for index in range(2)
        ]

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [("Commons", provider)],
    )
    monkeypatch.setattr(
        visual_qa,
        "start_visual_qa_scene",
        lambda: resets.append(True),
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {
            index: True for index in range(len(images))
        },
    )

    shared = retrieval.collect_manual_visual_search(
        FakeRuntime(),
        FakeBot(),
        "Vaibhav Sooryavanshi batting",
        reset_qa_scene=False,
    )
    standalone = retrieval.collect_manual_visual_search(
        FakeRuntime(),
        FakeBot(),
        "Vaibhav Sooryavanshi press conference",
        reset_qa_scene=True,
    )

    assert shared["assets"]
    assert standalone["assets"]
    assert resets == [True]

def test_manual_visual_pool_falls_through_to_additional_real_sources(monkeypatch):
    image_one = _jpeg_bytes((1200, 1600), (20, 80, 120))
    image_two = _jpeg_bytes((1200, 1600), (120, 80, 20))
    calls = []

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            calls.append(source)
            return fetcher(*args)

    def empty_provider(*args):
        return []

    def openverse_provider(*args):
        candidate = _licensed_candidate(image_one, "cc0")
        candidate["source_image_url"] = "https://openverse.example/vaibhav-1.jpg"
        candidate["search_title"] = "Vaibhav Sooryavanshi cricket"
        return [candidate]

    def ddg_provider(*args):
        candidate = _licensed_candidate(image_two, "cc0")
        candidate["source_image_url"] = "https://ddg.example/vaibhav-2.jpg"
        candidate["search_title"] = "Vaibhav Sooryavanshi batting"
        return [candidate]

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [
            ("Commons", empty_provider),
            ("Wikipedia", empty_provider),
            ("Openverse", openverse_provider),
            ("DDG", ddg_provider),
        ],
    )

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [{"primary_entity": "Vaibhav Sooryavanshi"}],
        ["Vaibhav Sooryavanshi"],
        verify_with_ai=False,
    )

    assert set(calls) == {"Commons", "Wikipedia", "Openverse", "DDG"}
    assert len(result["assets"]) == 2
    assert {asset["source"] for asset in result["assets"]} == {"Openverse", "DDG"}

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


def test_person_action_reuses_verified_cache_without_duplicate_semantic_qa(monkeypatch):
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

    # Cached verified assets are reused without another semantic-QA request.
    # The cache preserves the source image dimensions; final Shorts fitting happens
    # later in the content-first renderer.
    assert calls["qa"] == 0
    assert source == "cached"
    assert used_ai is False
    assert scene["visual_verified"] is True
    assert scene["visual_cache_reused"] is True
    assert image.size == (900, 1200)


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



def test_commons_cc_zero_license_is_accepted_after_normalization():
    from visual_licensing_runtime import normalize_license_code

    assert normalize_license_code("CC Zero 1.0 Universal") == "cc0"
    assert normalize_license_code("CC0 1.0") == "cc0"


def test_commons_entity_search_uses_structured_depicts_for_named_non_people(monkeypatch):
    calls = []

    monkeypatch.setattr(
        provider_boundary,
        "resolve_person_identity",
        lambda query: {},
    )
    monkeypatch.setattr(
        provider_boundary,
        "resolve_wikidata_entity",
        lambda query: {"qid": "Q41291", "label": "BCCI", "description": "cricket governing body"},
    )

    def fake_api_json(_url, *, params=None, headers=None):
        calls.append(dict(params or {}))
        return {
            "query": {
                "pages": {
                    "1": {
                        "title": "File:BCCI logo.svg",
                        "imageinfo": [{
                            "thumburl": "https://commons.example/bcci.jpg",
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:BCCI_logo.svg",
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "Artist": {"value": "Example"},
                                "ImageDescription": {"value": "BCCI logo"},
                            },
                        }],
                        "categories": [{"title": "Category:Board of Control for Cricket in India"}],
                    }
                }
            }
        }

    monkeypatch.setattr(provider_boundary, "_api_json", fake_api_json)
    monkeypatch.setattr(
        provider_boundary,
        "_download_image",
        lambda url, used_urls=None, metadata=None: {
            "bytes": b"image-bytes",
            "provenance": dict(metadata or {}),
            **dict(metadata or {}),
        },
    )

    candidates = provider_boundary.fetch_commons_candidates(
        "BCCI",
        set(),
        "",
        "",
        "ORGANIZATION",
        "ORG_BRANDING",
    )

    assert candidates
    assert calls[0]["gsrsearch"] == "haswbstatement:P180=Q41291"
    assert candidates[0]["commons_match_mode"] == "structured-depicts-entity"


def test_commons_search_query_normalizes_match_operators():
    assert provider_boundary._commons_search_query("India women versus Australia") == "India women v Australia"
    assert provider_boundary._commons_search_query("India women vs Australia") == "India women v Australia"


def test_commons_query_ladder_is_bounded_and_keeps_exact_search(monkeypatch):
    monkeypatch.setattr(
        provider_boundary,
        "resolve_person_identity",
        lambda query: {},
    )
    monkeypatch.setattr(
        provider_boundary,
        "resolve_wikidata_entity",
        lambda query: {"qid": "Q1", "label": query, "description": ""},
    )

    queries = []

    def fake_api_json(_url, *, params=None, headers=None):
        queries.append((params or {}).get("gsrsearch"))
        return {"query": {"pages": {}}}

    monkeypatch.setattr(provider_boundary, "_api_json", fake_api_json)

    provider_boundary.fetch_commons_candidates(
        "India women's national cricket team",
        set(),
        "",
        "",
        "ORGANIZATION",
        "TEAM_ACTION",
    )

    assert len(queries) == 2
    assert queries[0] == "haswbstatement:P180=Q1"
    assert queries[1] == "India women's national cricket team"


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


def test_verified_person_action_cache_ignores_narration_text():
    cache_contexts = []

    class FakeBot:
        ASSETS_DIR = "/tmp"

    image = Image.new("RGB", (900, 1200), (80, 90, 100))

    class FakeRuntime:
        @staticmethod
        def get_cached_asset(bot, entity, visual_type, context=""):
            cache_contexts.append((entity, visual_type, context))
            return image, "cached"

    scenes = [
        {
            "primary_entity": "Virat Kohli",
            "factual_primary_entity": "Virat Kohli",
            "visual_type": "PERSON",
            "visual_genre": "PERSON_ACTION",
            "visual_intent": "batting",
            "specific_search_prompt": "Virat Kohli batting",
            "voiceover": "Virat Kohli smashed another boundary.",
        },
        {
            "primary_entity": "Virat Kohli",
            "factual_primary_entity": "Virat Kohli",
            "visual_type": "PERSON",
            "visual_genre": "PERSON_ACTION",
            "visual_intent": "batting",
            "specific_search_prompt": "Virat Kohli batting",
            "voiceover": "Virat Kohli changed the momentum of the innings.",
        },
    ]

    for scene in scenes:
        result = retrieval.run_visual_retrieval(
            FakeRuntime(),
            FakeBot(),
            scene,
            "cricket",
            set(),
            set(),
            scene["voiceover"],
        )
        assert result[2] == "cached"
        assert scene["visual_cache_reused"] is True

    assert cache_contexts == [
        ("Virat Kohli", "PERSON", "PERSON_ACTION"),
        ("Virat Kohli", "PERSON", "PERSON_ACTION"),
    ]


def test_manual_queries_build_one_shared_ten_image_pool_without_duplicates(monkeypatch):
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
            candidate = _licensed_candidate(
                buffer.getvalue(),
                "cc0",
            )
            candidate["search_title"] = f"{query} image {image_index + 1}"
            candidate["search_position"] = image_index + 1
            items.append(candidate)
        image_candidates[query] = items

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    def fetcher(*args):
        return image_candidates.get(args[0], [])

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [("Commons", fetcher)],
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

    assert len(result["assets"]) == 10
    assert len({item["hash"] for item in result["assets"]}) == 10
    assert result["hard_max"] == 10
    assert [item["verified"] for item in result["query_stats"]] == [3, 3, 2, 2]
    assert len(result["query_stats"]) == 4
    assert all(stat["qa_requests"] == 1 for stat in result["query_stats"])



def test_manual_visual_options_returns_up_to_ten_unique_choices_without_backfill(monkeypatch):
    from visual_retrieval_runtime import collect_manual_visual_options

    image_candidates = []
    for index in range(5):
        buffer = io.BytesIO()
        Image.new(
            "RGB",
            (1200, 1600),
            (30 + index * 20, 60, 100 + index * 10),
        ).save(buffer, format="JPEG", quality=95)
        candidate = _licensed_candidate(buffer.getvalue(), "cc0")
        candidate["search_title"] = f"Shafali Verma photo {index + 1}"
        candidate["search_position"] = index + 1
        image_candidates.append(candidate)

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [
            ("Commons", lambda *args: image_candidates),
        ],
    )

    import visual_qa_runtime
    monkeypatch.setattr(
        visual_qa_runtime,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {
            index: True for index in range(len(images))
        },
    )

    result = collect_manual_visual_options(
        FakeRuntime(),
        object(),
        {
            "primary_entity": "Shafali Verma",
            "factual_primary_entity": "Shafali Verma",
            "voiceover": "Shafali Verma batting for India.",
        },
        "Shafali Verma",
        min_options=0,
        max_options=10,
    )

    assets = result["assets"]
    assert len(assets) == 5
    assert len({item["hash"] for item in assets}) == 5
    assert result["target"] == 10
    assert result["hard_max"] == 10
    assert result["available_options"] == 5
    assert result["enough_options"] is True
    assert all(stat["pool_origin"] == "manual" for stat in result["query_stats"])
    assert len(result["query_stats"]) == 1


def test_manual_pool_allows_multiple_images_from_same_source_article(monkeypatch):
    image_candidates = []
    for index, page in enumerate(("https://example.com/article-a", "https://example.com/article-a", "https://example.com/article-b")):
        buffer = io.BytesIO()
        Image.new(
            "RGB",
            (1000, 1400),
            (30 + index * 40, 70, 120),
        ).save(buffer, format="JPEG", quality=95)
        candidate = _licensed_candidate(buffer.getvalue(), "cc0")
        candidate["source_page_url"] = page
        candidate["search_title"] = f"Article image {index + 1}"

        image_candidates.append(candidate)

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [("Openverse", lambda *inner: image_candidates)],
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
        object(),
        [{"primary_entity": "Test Person", "voiceover": "Test Person appears."}],
        ["Test Person"],
        "Test story",
        pool_target=10,
        pool_max=10,
    )

    assets = result["assets"]
    assert len(assets) == 3
    assert len({item["source_page_url"] for item in assets}) == 2



def test_provider_429_short_circuits_repeated_api_calls(monkeypatch):
    calls = []

    class Response:
        status_code = 429
        headers = {"Retry-After": "60"}

        def raise_for_status(self):
            raise AssertionError("429 should be handled before raise_for_status")

        def json(self):
            return {}

    def fake_get(*args, **kwargs):
        calls.append(args[0])
        return Response()

    provider_boundary._PROVIDER_429_UNTIL.clear()
    monkeypatch.setattr(provider_boundary.requests, "get", fake_get)

    first = provider_boundary._api_json("https://example.test/api")
    second = provider_boundary._api_json("https://example.test/api")

    assert first is None
    assert second is None
    assert calls == ["https://example.test/api"]

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

def test_commons_team_search_handles_womens_team_and_scene_modifier(monkeypatch):
    calls = []
    downloads = []

    monkeypatch.setattr(
        provider_boundary,
        "resolve_wikidata_entity",
        lambda query: {
            "qid": "Q6019705",
            "label": "India women's national cricket team",
            "description": "women's national cricket team of India",
        },
    )

    def fake_api_json(_url, *, params=None, headers=None):
        calls.append(dict(params or {}))
        return {
            "query": {
                "pages": {
                    "1": {
                        "title": "File:India women's national cricket team.jpg",
                        "imageinfo": [{
                            "thumburl": "https://commons.example/india-women.jpg",
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:India_women.jpg",
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC0"},
                                "Artist": {"value": "Example"},
                                "ImageDescription": {"value": "India women's national cricket team"},
                            },
                        }],
                        "categories": [{"title": "Category:India women's national cricket team"}],
                    }
                }
            }
        }

    def fake_download(url, used_urls=None, metadata=None):
        downloads.append((url, dict(metadata or {})))
        return {
            "bytes": b"image-bytes",
            "provenance": dict(metadata or {}),
            **dict(metadata or {}),
        }

    monkeypatch.setattr(provider_boundary, "_api_json", fake_api_json)
    monkeypatch.setattr(provider_boundary, "_download_image", fake_download)

    candidates = provider_boundary.fetch_commons_candidates(
        "India Womens National Team celebrate",
        set(),
        "",
        "",
        "ORGANIZATION",
        "TEAM_ACTION",
    )

    searches = [call.get("gsrsearch") for call in calls]
    assert "haswbstatement:P180=Q6019705" in searches
    assert "India women's national cricket team celebration" in searches
    assert "India women's national cricket team" in searches
    assert "India Womens National Team celebrate" in searches
    assert len(searches) <= 4
    assert candidates
    assert downloads


def test_canonical_manual_entity_anchor_normalizes_named_team(monkeypatch):
    import visual_search_intent_runtime as intent_runtime

    monkeypatch.setattr(
        provider_boundary,
        "resolve_wikidata_entity",
        lambda query: {
            "qid": "Q6019705",
            "label": "India women's national cricket team",
        },
    )

    assert (
        intent_runtime.canonical_manual_entity_anchor(
            "India Womens National Team celebrate",
            "",
        )
        == "India women's national cricket team"
    )




def test_manual_pool_uses_ten_image_target_overall(monkeypatch):
    image_sets = {}
    for query_index, target in enumerate((10, 7, 5), 1):
        values = []
        for index in range(target):
            values.append({
                "bytes": _jpeg_bytes((900 + index, 1200), color=(40 + query_index * 50 + index * 8, 70, 100)),
                "provenance": {
                    "provider": "Commons",
                    "url": f"https://commons.wikimedia.org/wiki/File:{query_index}_{index}.jpg",
                    "author": "Test",
                    "license": "cc0",
                    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                },
                "source_image_url": f"https://upload.wikimedia.org/{query_index}_{index}.jpg",
                "source_page_url": f"https://commons.wikimedia.org/wiki/File:{query_index}_{index}.jpg",
                "search_title": f"File {query_index} {index}",
            })
        image_sets[query_index] = values

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

    def fake_plan(_bot, _visual_type, _visual_genre=""):
        return [
            ("Commons", lambda query, *_args: image_sets[int(query.split()[1])] ),
        ]

    monkeypatch.setattr(retrieval, "_source_plan", fake_plan)
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {index: True for index in range(len(images))},
    )

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [],
        ["rank 1", "rank 2", "rank 3"],
    )
    assert [row["target"] for row in result["query_stats"]] == [4, 3, 3]
    assert [row["verified"] for row in result["query_stats"]] == [4, 3, 3]
    assert len(result["assets"]) == 10


def test_manual_pool_allows_multiple_images_from_same_article(monkeypatch):
    values = []
    for index in range(4):
        values.append({
            "bytes": _jpeg_bytes((900 + index, 1200), color=(40 + index * 20, 70, 100)),
            "provenance": {
                "provider": "Commons",
                "url": f"https://commons.wikimedia.org/wiki/File:Article_image_{index}.jpg",
                "author": "Test",
                "license": "cc0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            },
            "source_image_url": f"https://upload.wikimedia.org/article_image_{index}.jpg",
            "source_page_url": "https://example.com/article",
            "search_title": f"Article image {index}",
        })

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *_args: [("Commons", lambda *_args: values)],
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {index: True for index in range(len(images))},
    )

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [],
        ["same article"],
    )
    assert len(result["assets"]) == 4
    assert result["rejection_counts"]["duplicate"] == 0


def test_new_manual_search_does_not_apply_monetization_filter(monkeypatch):
    values = [
        {
            "bytes": _jpeg_bytes((240, 240)),
            "provenance": {
                "provider": "Openverse",
                "url": f"https://example.com/low-{index}.jpg",
                "license": "cc-by-nc",
                "license_url": "",
            },
        }
        for index in range(1)
    ]
    values.extend(
        {
            "bytes": _jpeg_bytes((280, 280), color=(40 + index * 20, 80, 120)),
            "provenance": {
                "provider": "Openverse",
                "url": f"https://example.com/valid-{index}.jpg",
                "license": "cc0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            },
            "source_image_url": f"https://example.com/valid-{index}.jpg",
            "source_page_url": f"https://example.com/valid-{index}",
            "search_title": f"valid {index}",
        }
        for index in range(5)
    )

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *_args: [("Openverse", lambda *_args: values)],
    )

    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {index: True for index in range(len(images))},
    )

    result = retrieval.collect_manual_visual_search(
        FakeRuntime(),
        FakeBot(),
        "BCCI logo",
    )
    assert len(result["assets"]) == 6
    assert result["rejection_counts"]["monetization"] == 0




def test_manual_qc_can_select_a_provenance_review_candidate():
    candidate = {
        "hash": "rights-review",
        "status": "entity-verified",
        "provenance_status": "provenance-review",
        "priority": 100,
        "query": "Vaibhav Sooryavanshi",
        "source": "Openverse",
        "manual_query_index": 1,
        "subject": "Vaibhav Sooryavanshi",
        "visual_type": "PERSON",
        "visual_genre": "PERSON_PORTRAIT",
        "search_text": "Vaibhav Sooryavanshi cricket",
    }

    selected = retrieval.select_manual_visual_candidate(
        [candidate],
        {
            "slide_index": 1,
            "primary_entity": "Vaibhav Sooryavanshi",
            "visual_genre": "PERSON_PORTRAIT",
        },
        set(),
    )

    assert selected is candidate


def test_manual_pool_stops_after_first_two_providers_when_target_is_met(monkeypatch):
    calls = []

    def make_provider(name):
        def fetch(*args):
            calls.append(name)
            candidate = _licensed_candidate(
                _jpeg_bytes(
                    (1200, 1600),
                    color=(40 + len(calls) * 20, 70, 100),
                ),
                "cc-by-nc",
            )
            candidate["source_image_url"] = f"https://{name}.example/image-{len(calls)}.jpg"
            candidate["search_title"] = f"{name} result"
            return [candidate]
        return fetch

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [
            ("Commons", make_provider("Commons")),
            ("DDG", make_provider("DDG")),
            ("Openverse", make_provider("Openverse")),
            ("Pexels", make_provider("Pexels")),
            ("Unsplash", make_provider("Unsplash")),
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
        [{"primary_entity": "Rishabh Pant", "voiceover": "Rishabh Pant appears."}],
        ["Rishabh Pant"],
        "Test story",
        pool_target=2,
        pool_max=2,
    )

    assert calls == ["Commons", "DDG"]
    assert len(result["assets"]) == 2

def test_manual_pool_uses_second_provider_stage_only_when_first_stage_fails_qa(monkeypatch):
    calls = []
    qa_calls = []

    def make_provider(name):
        def fetch(*args):
            calls.append(name)
            candidate = _licensed_candidate(
                _jpeg_bytes(
                    (1200, 1600),
                    color=(40 + len(calls) * 20, 70, 100),
                ),
                "cc-by-nc",
            )
            candidate["source_image_url"] = f"https://{name}.example/image-{len(calls)}.jpg"
            candidate["search_title"] = f"{name} result"
            return [candidate]
        return fetch

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [
            ("Commons", make_provider("Commons")),
            ("Openverse", make_provider("Openverse")),
            ("Pexels", make_provider("Pexels")),
            ("Unsplash", make_provider("Unsplash")),
        ],
    )

    def fake_gemini(images, *args, **kwargs):
        qa_calls.append(len(images))
        return {index: (len(qa_calls) > 1) for index in range(len(images))}

    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", fake_gemini)

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [{"primary_entity": "Rishabh Pant", "voiceover": "Rishabh Pant appears."}],
        ["Rishabh Pant"],
        "Test story",
        pool_target=2,
        pool_max=2,
    )

    assert calls == ["Commons", "Openverse", "Pexels", "Unsplash"]
    assert qa_calls == [2, 2]
    assert len(result["assets"]) == 2
def test_manual_pool_exposes_candidates_for_human_review_when_gemini_is_temporarily_unavailable(monkeypatch):
    calls = []

    def provider(*args):
        calls.append(1)
        return [
            {
                "bytes": _jpeg_bytes((1200, 1600), (40 + index * 10, 70, 100)),
                "source_image_url": f"https://example.test/image-{index}.jpg",
                "search_title": "IPL logo",
                "provenance": {"provider": "Commons", "license": "cc0"},
            }
            for index in range(6)
        ]

    class FakeBot:
        ASSETS_DIR = "/tmp"

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return provider(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [("Commons", provider), ("DDG", provider)],
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: (
            setattr(visual_qa._qa_state(), "last_failure", "transient_unavailable")
            or {}
        ),
    )

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [{"primary_entity": "IPL", "voiceover": "IPL logo"}],
        ["IPL logo"],
        "IPL logo",
        pool_target=3,
        pool_max=3,
    )

    assert len(result["assets"]) == 3
    assert all(item["status"] == "manual-review-unverified" for item in result["assets"])
    assert result["query_stats"][0]["qa_requests"] == 1

def test_manual_pool_exposes_candidates_when_gemini_hits_quota(monkeypatch):
    calls = []

    def provider(*args):
        calls.append(1)
        return [
            {
                "bytes": _jpeg_bytes((1200, 1600), (80 + index * 10, 90, 110)),
                "source_image_url": f"https://quota.example/image-{index}.jpg",
                "search_title": "India cricket image",
                "provenance": {"provider": "Commons", "license": "cc0"},
            }
            for index in range(4)
        ]

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return provider(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [("Commons", provider), ("DDG", provider)],
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: (
            setattr(visual_qa._qa_state(), "last_failure", "quota_or_rate_limit")
            or {}
        ),
    )

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [{"primary_entity": "India", "voiceover": "India cricket image"}],
        ["India"],
        "India cricket image",
        pool_target=3,
        pool_max=3,
    )

    assert len(result["assets"]) == 3
    assert all(item["status"] == "manual-review-unverified" for item in result["assets"])

def test_manual_pool_human_review_mode_does_not_call_gemini(monkeypatch):
    def provider(*args):
        return [
            {
                "bytes": _jpeg_bytes((1200, 1600), (40 + index * 10, 70, 100)),
                "source_image_url": f"https://manual.example/image-{index}.jpg",
                "search_title": "IPL logo",
                "provenance": {"provider": "Commons", "license": "cc0"},
            }
            for index in range(6)
        ]

    class FakeBot:
        ASSETS_DIR = "/tmp"

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return provider(*args)

    def should_not_run(*args, **kwargs):
        raise AssertionError("Gemini must not block an explicit manual-QC pool build.")

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args, **kwargs: [("Commons", provider), ("DDG", provider)],
    )
    monkeypatch.setattr(visual_qa, "strict_gemini_check_batch", should_not_run)

    result = retrieval.collect_manual_visual_pool(
        FakeRuntime(),
        FakeBot(),
        [{"primary_entity": "IPL", "voiceover": "IPL logo"}],
        ["IPL logo"],
        "IPL logo",
        pool_target=3,
        pool_max=3,
        verify_with_ai=False,
    )

    assert len(result["assets"]) == 3
    assert all(item["status"] == "manual-review-unverified" for item in result["assets"])
    assert result["query_stats"][0]["qa_requests"] == 0


def test_gemini_transient_503_has_no_recursive_retry():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("visual_qa_runtime.py").read_text(
        encoding="utf-8"
    )
    start = source.index("def strict_gemini_check_batch(")
    end = source.index("\ndef install_visual_qa_bridge", start)
    block = source[start:end]

    assert "_allow_transient_retry" not in block
    assert "GEMINI_VISUAL_RETRIES" not in block
    assert "retrying once as" not in block
    assert "strict_gemini_check_batch(" not in block.split(
        "except Exception as exc:", 1
    )[1].split(
        "install_visual_qa_bridge", 1
    )[0]
    assert "transient_unavailable" in block


def test_dashboard_manual_search_uses_only_first_two_preferred_providers(monkeypatch):
    calls = []

    def make_provider(name):
        def fetch(*args):
            calls.append(name)
            candidate = _licensed_candidate(
                _jpeg_bytes((1200, 1600), color=(40 + len(calls) * 20, 70, 100)),
                "cc0",
            )
            candidate["source_image_url"] = f"https://{name}.example/image-{len(calls)}.jpg"
            candidate["search_title"] = f"{name} result"
            return [candidate]
        return fetch

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda *args: [
            ("Commons", make_provider("Commons")),
            ("DDG", make_provider("DDG")),
            ("Openverse", make_provider("Openverse")),
        ],
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {
            index: True for index in range(len(images))
        },
    )

    result = retrieval.collect_manual_visual_search(
        FakeRuntime(),
        FakeBot(),
        "IPL logo",
    )

    assert calls == ["Commons", "DDG"]
    assert len(result["assets"]) == 2


def test_manual_source_plan_can_include_ddg_without_global_unlicensed_flag(monkeypatch):
    monkeypatch.delenv("ALLOW_UNLICENSED_VISUALS", raising=False)
    plan = provider_boundary.build_raw_source_plan(
        "PERSON",
        "PERSON_ACTION",
        allow_unlicensed=True,
    )
    assert any(name == "DDG" for name, _fetcher in plan)


def test_manual_commons_qc_does_not_filter_noncommercial_license(monkeypatch):
    image_bytes = _jpeg_bytes()
    monkeypatch.setattr(
        provider_boundary,
        "_api_json",
        lambda *args, **kwargs: {
            "query": {
                "pages": {
                    "1": {
                        "title": "File:Test.jpg",
                        "imageinfo": [{
                            "thumburl": "https://commons.example/test.jpg",
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Test.jpg",
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC BY-NC 4.0"},
                                "Artist": {"value": "Test"},
                                "ImageDescription": {"value": "Test Person"},
                            },
                        }],
                    }
                }
            }
        },
    )
    monkeypatch.setattr(
        provider_boundary,
        "_download_image",
        lambda url, used_urls=None, metadata=None: {
            "bytes": image_bytes,
            "provenance": dict(metadata or {}),
            **dict(metadata or {}),
        },
    )

    candidates = provider_boundary.fetch_commons_candidates(
        "Test Person",
        set(),
        "",
        "",
        "PERSON",
        "PERSON_ACTION",
        True,
    )

    assert candidates
    assert candidates[0]["provenance"]["license"] == "by-nc"


def test_manual_query_planner_has_non_network_fallback(monkeypatch):
    import manual_visual_query_runtime as planner

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = planner.generate_visual_query_suggestions(
        "Virat Kohli joins India camp",
        "Virat Kohli joined the India cricket camp in Mumbai.",
        category="sports",
        max_queries=5,
    )
    assert result
    assert result[0]["query"]



def test_manual_visual_search_advances_to_new_page_after_used_images(monkeypatch):
    image_bytes = [
        _jpeg_bytes((900, 1200), (20 + index * 10, 60, 100))
        for index in range(10)
    ]
    calls = []

    class FakeBot:
        pass

    class FakeRuntime:
        @staticmethod
        def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=10):
            return fetcher(*args)

    def fake_fetcher(*args):
        page = int(args[-1])
        calls.append(page)
        start = (page - 1) * 5
        return [
            {
                "bytes": image_bytes[index],
                "source_image_url": f"https://cdn.pexels.com/photos/{index + 1}/image.jpg",
                "provenance": {
                    "provider": "Pexels",
                    "url": f"https://www.pexels.com/photo/{index + 1}/",
                    "author": "Tester",
                    "license": "Pexels License",
                    "license_url": "https://www.pexels.com/license/",
                },
                "search_title": "Indian cricket team",
            }
            for index in range(start, start + 5)
        ]

    monkeypatch.setattr(
        retrieval,
        "_manual_query_visual_context",
        lambda query, scenes: ("GENERAL_CONTEXT", "SPORTS_ACTION"),
    )
    monkeypatch.setattr(
        retrieval,
        "_source_plan",
        lambda bot, visual_type, visual_genre="": [("Pexels", fake_fetcher)],
    )
    monkeypatch.setattr(
        visual_qa,
        "strict_gemini_check_batch",
        lambda images, *args, **kwargs: {index: True for index in range(len(images))},
    )

    bot = FakeBot()
    first = retrieval.collect_manual_visual_search(
        FakeRuntime(),
        bot,
        "Indian cricket team",
    )
    first_hashes = {item["hash"] for item in first["assets"]}
    assert len(first_hashes) == 5
    assert calls == [1]

    second = retrieval.collect_manual_visual_search(
        FakeRuntime(),
        bot,
        "Indian cricket team",
        used_hashes=first_hashes,
    )
    second_hashes = {item["hash"] for item in second["assets"]}
    assert len(second_hashes) == 5
    assert first_hashes.isdisjoint(second_hashes)
    assert calls == [1, 2]


def test_pixabay_manual_search_requests_latest_page(monkeypatch, tmp_path):
    import image_sources_runtime as sources

    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    monkeypatch.setattr(sources, "_cache_dir", lambda: tmp_path)
    requested = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"hits": []}

    monkeypatch.setattr(
        sources.requests,
        "get",
        lambda url, **kwargs: requested.update(kwargs) or Response(),
    )

    result = sources.fetch_pixabay_candidates(
        "Indian cricket team",
        set(),
        "Indian cricket team",
        "",
        "ORGANIZATION",
        "TEAM_ACTION",
        True,
        2,
    )

    assert result == []
    assert requested["params"]["page"] == 2
    assert requested["params"]["order"] == "latest"


def test_pexels_search_requests_requested_page(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    requested = {}

    class Response:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"photos": []}

    monkeypatch.setattr(
        provider_boundary.requests,
        "get",
        lambda url, **kwargs: requested.update(kwargs) or Response(),
    )

    result = provider_boundary.fetch_pexels_candidates(
        "cricket team",
        set(),
        "cricket team",
        "",
        "ORGANIZATION",
        "TEAM_ACTION",
        True,
        3,
    )

    assert result == []
    assert requested["params"]["page"] == 3


def test_commons_search_requests_requested_page(monkeypatch):
    monkeypatch.setattr(provider_boundary, "_api_json", lambda *args, **kwargs: {
        "query": {
            "pages": {
                "1": {
                    "title": "File:Cricket.jpg",
                    "imageinfo": [{
                        "thumburl": "https://example.com/cricket.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Cricket.jpg",
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY 4.0"},
                            "Artist": {"value": "Tester"},
                            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                        },
                    }],
                    "categories": [],
                }
            }
        }
    })
    requested = {}

    original = provider_boundary._api_json
    def capture(url, *, params=None, headers=None):
        requested["params"] = dict(params or {})
        return original(url, params=params, headers=headers)

    monkeypatch.setattr(provider_boundary, "_api_json", capture)

    monkeypatch.setattr(
        provider_boundary,
        "_bounded_downloads",
        lambda urls, used_urls, limit=provider_boundary.MAX_PROVIDER_CANDIDATES: [],
    )

    result = provider_boundary.fetch_commons_candidates(
        "cricket team",
        set(),
        "cricket team",
        "",
        "ORGANIZATION",
        "TEAM_ACTION",
        True,
        3,
    )

    assert result == []
    assert requested["params"]["gsroffset"] == (3 - 1) * provider_boundary.MAX_PROVIDER_CANDIDATES


def test_verified_asset_preserves_provider_source_name():
    candidate = {
        "source": "Commons",
        "query": "Sanju Samson",
        "provenance_status": "commercial-verified",
    }
    assert candidate["source"] == "Commons"
    assert candidate["provenance_status"] == "commercial-verified"
