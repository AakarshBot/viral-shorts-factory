from __future__ import annotations

from evidence_runtime import (
    source_tier,
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
    else:
        item["tier"] = source_tier(item)
    return item


def test_source_hierarchy_keeps_reddit_at_discovery_only():
    assert source_tier({
        "url": "https://www.reddit.com/r/news/comments/1",
        "source_kind": "event_source",
    }) == "C"
    assert source_tier({
        "url": "https://nasa.gov/news/example",
        "collection_source": "official",
    }) == "A"
    assert source_tier({"url": "https://reuters.com/world/example"}) == "B"


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

def test_discover_sources_overlaps_independent_research_calls(monkeypatch):
    import threading

    barrier = threading.Barrier(2, timeout=1.0)
    calls = []

    def fake_ddg(query):
        calls.append(("ddg", query))
        barrier.wait()
        return [_source("https://reuters.com/example", "Reuters", "A Reuters page with useful evidence.")]

    def fake_openalex(story):
        calls.append(("openalex", story.get("title")))
        barrier.wait()
        return [_source("https://nature.com/example", "Nature", "A Nature research page with useful evidence.")]

    monkeypatch.setattr("evidence_runtime._ddg_sources", fake_ddg)
    monkeypatch.setattr("evidence_runtime._openalex_sources", fake_openalex)

    result = __import__("evidence_runtime").discover_sources(
        {"title": "New cancer research study", "description": "Scientists published a new clinical study with results."},
        max_sources=5,
    )

    assert {item["publisher"] for item in result} == {"Reuters", "Nature"}
    assert {kind for kind, _ in calls} == {"ddg", "openalex"}

    
def test_discover_sources_skips_redundant_discovery_when_event_evidence_fills_budget(monkeypatch):
    calls = []

    def fail_ddg(_query):
        calls.append("ddg")
        raise AssertionError("DDG should not run when event evidence already fills the source budget.")

    monkeypatch.setattr("evidence_runtime._ddg_sources", fail_ddg)
    result = __import__("evidence_runtime").discover_sources(
        {
            "title": "India wins a major final",
            "event_evidence": [
                _source(
                    f"https://source{i}.example/story",
                    f"Publisher {i}",
                    "A useful factual event report with enough text for source selection.",
                )
                for i in range(1, 6)
            ],
        },
        max_sources=5,
    )

    assert len(result) == 5
    assert calls == []
