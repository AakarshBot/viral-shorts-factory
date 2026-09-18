from __future__ import annotations

from evidence_runtime import (
    _source_tier,
    build_evidence_pack,
    claims_conflict,
    format_evidence_pack_for_script,
)


def _source(url, publisher, text, tier=None):
    item = {
        "url": url,
        "publisher": publisher,
        "title": f"{publisher} report",
        "clean_text": text,
        "extraction_status": "ok",
        "extraction_method": "fixture",
        "domain": url.split("/")[2].removeprefix("www."),
        "source_id": publisher.lower(),
        "published_at": "2026-09-18T00:00:00+00:00",
    }
    if tier is not None:
        item["tier"] = tier
    return item


def test_source_hierarchy_keeps_reddit_at_discovery_only():
    assert _source_tier({
        "url": "https://www.reddit.com/r/news/comments/1",
        "source_kind": "event_source",
    }) == "C"
    assert _source_tier({
        "url": "https://nasa.gov/news/example",
        "collection_source": "official",
    }) == "A"
    assert _source_tier({"url": "https://reuters.com/world/example"}) == "B"


def test_claim_conflict_detects_incompatible_numbers():
    left = "The company reported sales of 10 million dollars in the quarter."
    right = "The company reported sales of 20 million dollars in the quarter."
    assert claims_conflict(left, right)


def test_evidence_pack_corroborrates_real_pages_not_discovery_snippets(monkeypatch):
    sources = [
        _source(
            "https://reuters.com/world/example",
            "Reuters",
            "NASA announced that the Artemis mission launched successfully on Friday. "
            "The mission carries four astronauts and will test new systems for the lunar programme.",
        ),
        _source(
            "https://apnews.com/article/example",
            "Associated Press",
            "NASA confirmed that the Artemis mission launched successfully on Friday. "
            "Four astronauts are travelling on the mission and the flight will test new lunar systems.",
        ),
        _source(
            "https://www.reddit.com/r/space/comments/1",
            "Reddit",
            "NASA definitely launched the mission and everyone says it is historic.",
            tier="C",
        ),
    ]

    def fake_extract(source):
        return dict(source)

    monkeypatch.setattr("evidence_runtime.extract_article_source", fake_extract)

    pack = build_evidence_pack(
        {"event_id": "evt-1", "title": "NASA launches Artemis"},
        sources=sources,
    )

    assert pack["counts"]["usable_sources"] == 2
    assert pack["counts"]["independent_domains"] == 2
    assert pack["counts"]["corroborated_claims"] >= 1
    assert all(item["best_tier"] in {"A", "B"} for item in pack["claims"])

    formatted = format_evidence_pack_for_script(pack)
    assert "DISCOVERY-ONLY SOURCES" in formatted
    assert "Reuters" in formatted
    assert "Associated Press" in formatted


def test_insufficient_page_extraction_is_not_silent(monkeypatch):
    source = _source(
        "https://example.com/story",
        "Example News",
        "This source never produced a usable article body.",
    )

    def fake_extract(item):
        result = dict(item)
        result["extraction_status"] = "failed"
        result["error"] = "blocked"
        return result

    monkeypatch.setattr("evidence_runtime.extract_article_source", fake_extract)
    pack = build_evidence_pack({"title": "Blocked story"}, sources=[source])

    assert pack["status"] == "insufficient_evidence"
    assert pack["counts"]["usable_sources"] == 0
