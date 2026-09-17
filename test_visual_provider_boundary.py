from __future__ import annotations

import visual_provider_boundary_runtime as boundary
import visual_retrieval_runtime as retrieval


def test_person_source_plan_uses_raw_adapters_not_bot_fetchers():
    plan = retrieval._source_plan(object(), "PERSON")
    names = [name for name, _fetcher in plan]
    assert names[:2] == ["Wikipedia", "Commons"]
    assert all(
        fetcher.__module__ == boundary.__name__ or fetcher.__module__ == "image_sources_runtime"
        for _name, fetcher in plan
    )


def test_raw_person_adapters_have_no_legacy_quality_gate_dependency():
    assert "passes_quality_gate" not in boundary.fetch_wikipedia_person.__code__.co_names
    assert "passes_quality_gate" not in boundary.fetch_commons.__code__.co_names


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
    assert len(plan) >= 7


if __name__ == "__main__":
    test_person_source_plan_uses_raw_adapters_not_bot_fetchers()
    test_raw_person_adapters_have_no_legacy_quality_gate_dependency()
    test_active_retrieval_plan_does_not_bind_legacy_bot_provider_methods()
    print("Visual provider boundary regression checks passed.")
