import asyncio
import os

from visual_licensing_runtime import (
    append_image_credits,
    is_allowed_license,
    normalize_license_code,
    provenance,
)
from visual_provider_boundary_runtime import build_raw_source_plan


def test_default_provider_plan_excludes_unlicensed_sources(monkeypatch):
    monkeypatch.delenv("ALLOW_UNLICENSED_VISUALS", raising=False)
    names = {name.casefold() for name, _fetcher in build_raw_source_plan("PERSON", "PERSON_PORTRAIT")}
    assert "ddg" not in names
    assert "duckduckgo" not in names
    assert "news_source" not in names


def test_unlicensed_provider_is_not_part_of_active_plan(monkeypatch):
    monkeypatch.setenv("ALLOW_UNLICENSED_VISUALS", "true")
    names = {name.casefold() for name, _fetcher in build_raw_source_plan("GENERAL_CONTEXT", "GENERAL_CONTEXT")}
    assert "ddg" not in names
    assert "duckduckgo" not in names


def test_open_license_allowlist_rejects_nc_and_nd():
    assert is_allowed_license("cc0")
    assert is_allowed_license("pdm")
    assert is_allowed_license("by")
    assert is_allowed_license("cc-by-sa")
    for license_code in ("by-nc", "by-nc-sa", "by-nd", "by-sa-nd", "fair-use", "all-rights-reserved"):
        assert not is_allowed_license(license_code), license_code
    assert normalize_license_code("CC BY-NC-SA 4.0") == "by-nc-sa"


def test_image_credits_include_only_attribution_licenses():
    records = [
        provenance(
            "Openverse",
            "https://example.test/by.jpg",
            "Alice",
            "by",
            "https://creativecommons.org/licenses/by/4.0/",
        ),
        provenance(
            "Openverse",
            "https://example.test/cc0.jpg",
            "Bob",
            "cc0",
            "https://creativecommons.org/publicdomain/zero/1.0/",
        ),
        provenance(
            "Openverse",
            "https://example.test/by-sa.jpg",
            "Carol",
            "by-sa",
            "https://creativecommons.org/licenses/by-sa/4.0/",
        ),
    ]
    description = append_image_credits("Base description", records)
    assert "Image credits" in description
    assert "Alice — by — https://example.test/by.jpg" in description
    assert "Carol — by-sa — https://example.test/by-sa.jpg" in description
    assert "Bob" not in description


def test_news_source_loader_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ALLOW_UNLICENSED_VISUALS", raising=False)
    import visual_content_runtime

    result = asyncio.run(
        visual_content_runtime._load_verified_news_source_candidate(
            object(), object(), [], {"selected_story": {"story_url": "https://example.test/story"}}
        )
    )
    assert result is None
