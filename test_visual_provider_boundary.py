from __future__ import annotations

import visual_provider_boundary_runtime as boundary
import visual_retrieval_runtime as retrieval
import visual_search_intent_runtime as search_intent


def test_person_source_plan_uses_raw_multi_candidate_adapters_not_bot_fetchers():
    plan = retrieval._source_plan(object(), "PERSON")
    names = [name for name, _fetcher in plan]
    assert names[:2] == ["Wikipedia", "Commons"]
    assert all(
        fetcher.__module__ == boundary.__name__ or fetcher.__module__ == "image_sources_runtime"
        for _name, fetcher in plan
    )


def test_raw_candidate_adapters_have_no_legacy_quality_gate_dependency():
    assert "passes_quality_gate" not in boundary.fetch_wikipedia_person_candidates.__code__.co_names
    assert "passes_quality_gate" not in boundary.fetch_commons_candidates.__code__.co_names


def test_active_retrieval_plan_does_not_bind_legacy_bot_provider_methods():
    class ExplosiveBot:
        def fetch_wiki_person_image(self, *args, **kwargs):
            raise AssertionError("legacy Wikipedia fetcher must not be used by the active retrieval path")

        def fetch_wikimedia_commons(self, *args, **kwargs):
            raise AssertionError("legacy Commons fetcher must not be used by the active retrieval path")

        def fetch_duckduckgo(self, *args, **kwargs):
            raise AssertionError("legacy DDG fetcher must not be used by the active retrieval path")

        def fetch_pexels(self, *args, **kwargs):
            raise AssertionError("legacy Pexels fetcher must not be used by the active retrieval path")

        def fetch_unsplash(self, *args, **kwargs):
            raise AssertionError("legacy Unsplash fetcher must not be used by the active retrieval path")

    plan = retrieval._source_plan(ExplosiveBot(), "PERSON")
    names = [name.casefold() for name, _fetcher in plan]
    assert "ddg" not in names
    assert "news_source" not in names
    assert names[:2] == ["wikipedia", "commons"]
    assert "openverse" in names
    optional = {
        "pixabay": "PIXABAY_API_KEY",
        "pexels": "PEXELS_API_KEY",
        "unsplash": "UNSPLASH_ACCESS_KEY",
    }
    for provider, env_name in optional.items():
        configured = bool(str(__import__("os").getenv(env_name, "")).strip())
        assert (provider in names) is configured


def test_commons_candidate_adapter_is_bounded():
    assert 1 <= boundary.MAX_PROVIDER_CANDIDATES <= 6
    assert callable(boundary.fetch_commons_candidates)
    assert callable(boundary.fetch_wikipedia_person_candidates)


def test_retrieval_accepts_multi_candidate_provider_payloads(monkeypatch):
    class FakeRuntime:
        VISUAL_MAX_VERIFICATION_ATTEMPTS = 4

        def _build_search_variants(self, seg, video_title=""):
            return ["Northstar Research Summit"], "EVENT"

        def _verification_tier(self, seg, visual_type, source):
            return "SKIPPED(conceptual)"

        def _call_fetcher_with_timeout(self, fetcher, args, source, query):
            return fetcher(*args)

        def get_cached_asset(self, *args, **kwargs):
            return None, None

        def save_to_cache(self, *args, **kwargs):
            return None

        def _strict_gate(self, *args, **kwargs):
            return True, "STRICT(event)", 100, False

    first = __import__("io").BytesIO()
    from PIL import Image
    Image.new("RGB", (600, 600), "white").save(first, format="JPEG")
    second = __import__("io").BytesIO()
    Image.new("RGB", (600, 600), "black").save(second, format="JPEG")

    class ProviderRuntime(FakeRuntime):
        pass

    candidates = [
        {"bytes": first.getvalue(), "provenance": {
            "provider": "Commons",
            "url": "https://commons.wikimedia.org/wiki/File:Northstar.jpg",
            "author": "Test Author",
            "license": "by",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
        }},
        {"bytes": second.getvalue(), "provenance": {
            "provider": "Commons",
            "url": "https://commons.wikimedia.org/wiki/File:Northstar-2.jpg",
            "author": "Test Author 2",
            "license": "cc0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        }},
    ]
    monkeypatch.setattr(retrieval, "_source_plan", lambda _bot, _visual_type: [("Commons", lambda *args: candidates)])
    monkeypatch.setattr(retrieval, "_hash_image", lambda _bot, data: __import__("hashlib").sha256(data).hexdigest())

    seg = {"primary_entity": "Northstar Research Summit", "specific_search_prompt": "Northstar Research Summit", "voiceover": "Northstar Research Summit opened today"}
    image, used_ai, source = retrieval.run_visual_retrieval(ProviderRuntime(), object(), seg, "business", set(), set(), "Northstar Research Summit")
    assert image.size == (600, 600)
    assert used_ai is False
    assert source == "Commons"
    assert seg["visual_verified"] is True
    assert seg["asset_provenance"]["license"] == "by"


def test_person_portrait_intent_is_identity_first():
    scene = {
        "primary_entity": "Vaibhav Sooryavanshi",
        "visual_type": "PERSON",
        "visual_intent": "portrait",
        "specific_search_prompt": "Vaibhav Sooryavanshi",
        "voiceover": "Vaibhav Sooryavanshi is an Indian cricketer.",
    }
    intent = search_intent.resolve_visual_search_intent(scene)
    assert intent.visual_genre == "PERSON_PORTRAIT"
    assert intent.queries == ("Vaibhav Sooryavanshi", "Vaibhav Sooryavanshi portrait")
    assert intent.query == "Vaibhav Sooryavanshi"


def test_person_provider_queries_use_canonical_identity_and_structured_commons():
    identity = "Vaibhav Sooryavanshi"
    assert retrieval._provider_search_query(
        "Wikipedia", "Vaibhav Sooryavanshi portrait", identity, "PERSON", "PERSON_PORTRAIT", "Q123", 1
    ) == identity
    assert retrieval._provider_search_query(
        "Commons", "Vaibhav Sooryavanshi", identity, "PERSON", "PERSON_PORTRAIT", "Q123", 1
    ) == "haswbstatement:P180=Q123"
    assert retrieval._provider_search_query(
        "Commons", "Vaibhav Sooryavanshi portrait", identity, "PERSON", "PERSON_PORTRAIT", "Q123", 2
    ) == identity
    assert retrieval._provider_search_query(
        "Openverse", "Vaibhav Sooryavanshi", identity, "PERSON", "PERSON_PORTRAIT", "Q123", 1,
        identity_label="Vaibhav Suryavanshi"
    ) == "Vaibhav Suryavanshi"
    assert retrieval._provider_search_query(
        "Openverse", "Vaibhav Sooryavanshi portrait", identity, "PERSON", "PERSON_PORTRAIT", "Q123", 2,
        identity_label="Vaibhav Suryavanshi"
    ) == "Vaibhav Suryavanshi portrait"


def test_person_identity_resolver_uses_wikidata_and_caches(monkeypatch):
    cache_key = "test person identity resolver"
    boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)
    calls = []

    def fake_api(url, *, params=None, headers=None):
        calls.append((url, params))
        return {"search": [{"id": "Q123456", "label": "Test Person"}]}

    monkeypatch.setattr(boundary, "_api_json", fake_api)
    first = boundary.resolve_person_identity("Test Person Identity Resolver")
    second = boundary.resolve_person_identity("Test Person Identity Resolver")
    assert first == second == {"qid": "Q123456", "label": "Test Person"}
    assert len(calls) == 2
    assert calls[0][1]["action"] == "wbsearchentities"
    assert calls[1][1]["action"] == "wbgetentities"
    boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)


def test_person_identity_resolver_falls_back_to_wikipedia_for_spelling_variants(monkeypatch):
    cache_keys = {"smriti mandana", "smriti mandhana"}
    for cache_key in cache_keys:
        boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)

    calls = []

    def fake_api(url, *, params=None, headers=None):
        calls.append((url, dict(params or {})))
        action = (params or {}).get("action")
        if action == "wbsearchentities":
            return {"search": []}
        if url == "https://en.wikipedia.org/w/api.php":
            return {
                "query": {
                    "pages": {
                        "16224802": {
                            "pageid": 16224802,
                            "index": 1,
                            "title": "Smriti Mandhana",
                            "pageprops": {"wikibase_item": "Q16224802"},
                        }
                    }
                }
            }
        return {
            "entities": {
                "Q16224802": {
                    "claims": {
                        "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]
                    },
                    "labels": {"en": {"value": "Smriti Mandhana"}},
                }
            }
        }

    monkeypatch.setattr(boundary, "_api_json", fake_api)
    first = boundary.resolve_person_identity("Smriti Mandana")
    second = boundary.resolve_person_identity("Smriti Mandana")

    assert first == second == {"qid": "Q16224802", "label": "Smriti Mandhana"}
    actions = [params.get("action") for _url, params in calls]
    assert actions == ["wbsearchentities", "query", "wbgetentities"]
    assert boundary._PERSON_IDENTITY_CACHE["smriti mandana"] == first
    assert boundary._PERSON_IDENTITY_CACHE["smriti mandhana"] == first

    for cache_key in cache_keys:
        boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)


def test_wikipedia_person_search_does_not_poison_identity_cache(monkeypatch):
    cache_key = "cache poisoning test person"
    boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)

    def fake_api(url, *, params=None, headers=None):
        action = (params or {}).get("action")
        if action == "query":
            return {
                "query": {
                    "pages": {
                        "123": {
                            "pageid": 123,
                            "title": "Not Yet Verified Person",
                            "index": 1,
                            "pageprops": {"wikibase_item": "Q999"},
                        }
                    }
                }
            }
        return None

    monkeypatch.setattr(boundary, "_api_json", fake_api)
    boundary.fetch_wikipedia_person_candidates("Cache Poisoning Test Person")
    assert "cache poisoning test person" not in boundary._PERSON_IDENTITY_CACHE
    boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)



def test_person_identity_resolver_prefers_verified_human(monkeypatch):
    cache_key = "human identity resolver test"
    boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)

    def fake_api(url, *, params=None, headers=None):
        action = (params or {}).get("action")
        if action == "wbsearchentities":
            return {
                "search": [
                    {"id": "Q111", "label": "Organisation With Person-Like Name"},
                    {"id": "Q222", "label": "Actual Person"},
                ]
            }
        return {
            "entities": {
                "Q111": {"claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q43229"}}}}]}, "labels": {"en": {"value": "Wrong Candidate"}}},
                "Q222": {
                    "claims": {
                        "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]
                    },
                    "labels": {"en": {"value": "Actual Person"}}
                },
            }
        }

    monkeypatch.setattr(boundary, "_api_json", fake_api)
    assert boundary.resolve_person_identity("Human Identity Resolver Test") == {"qid": "Q222", "label": "Actual Person"}
    boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)


def test_person_identity_resolver_rejects_non_human_when_wikidata_is_complete(monkeypatch):
    cache_key = "non human identity resolver test"
    boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)

    def fake_api(url, *, params=None, headers=None):
        action = (params or {}).get("action")
        if action == "wbsearchentities":
            return {"search": [{"id": "Q333", "label": "Not A Person"}]}
        return {
            "entities": {
                "Q333": {
                    "claims": {
                        "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q43229"}}}}]
                    }
                }
            }
        }

    monkeypatch.setattr(boundary, "_api_json", fake_api)
    assert boundary.resolve_person_identity("Non Human Identity Resolver Test") == {}
    boundary._PERSON_IDENTITY_CACHE.pop(cache_key, None)


def test_godl_india_is_an_explicit_commercial_license():
    assert boundary.is_allowed_license("GODL-India")
    assert boundary.is_allowed_license("Government Open Data License - India")


if __name__ == "__main__":
    test_person_source_plan_uses_raw_multi_candidate_adapters_not_bot_fetchers()
    test_raw_candidate_adapters_have_no_legacy_quality_gate_dependency()
    test_active_retrieval_plan_does_not_bind_legacy_bot_provider_methods()
    test_commons_candidate_adapter_is_bounded()
    test_person_portrait_intent_is_identity_first()
    test_person_provider_queries_use_canonical_identity_and_structured_commons()
    test_godl_india_is_an_explicit_commercial_license()
    print("Visual provider boundary regression checks passed.")
