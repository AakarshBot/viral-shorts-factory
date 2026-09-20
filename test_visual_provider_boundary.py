from __future__ import annotations

import visual_provider_boundary_runtime as boundary
import visual_retrieval_runtime as retrieval


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


if __name__ == "__main__":
    test_person_source_plan_uses_raw_multi_candidate_adapters_not_bot_fetchers()
    test_raw_person_adapters_have_no_legacy_quality_gate_dependency()
    test_active_retrieval_plan_does_not_bind_legacy_bot_provider_methods()
    test_commons_candidate_adapter_is_bounded()
    print("Visual provider boundary regression checks passed.")
