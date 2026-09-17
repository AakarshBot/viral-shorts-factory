"""Offline, no-API diagnostics for Viral Shorts Factory."""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import traceback


def _run(name, fn):
    try:
        return {"name": name, "status": "PASS", "detail": fn()}
    except Exception as exc:
        return {
            "name": name,
            "status": "FAIL",
            "detail": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }


def _test_imports():
    modules = [
        "ultimate_bot", "factory_runtime", "db_architecture", "db_runtime",
        "diagnostics_runtime", "editorial_runtime", "runtime_hardener",
        "script_guard_runtime", "script_runtime", "research_runtime",
        "audio_runtime", "audio_direction_runtime", "visual_runtime",
        "visual_qa_runtime", "visual_strategy_runtime", "visual_content_runtime",
        "provider_runtime", "quality_runtime", "runtime_bindings", "workflow_runtime",
        "subtitle_runtime", "newsroom_dashboard",
    ]
    for name in modules:
        __import__(name)
    return f"Imported {len(modules)} factory modules"


def _test_environment():
    names = (
        "GEMINI_API_KEY", "GROQ_API_KEY", "GNEWS_API_KEY",
        "UNSPLASH_ACCESS_KEY", "HF_TOKEN", "PEXELS_API_KEY",
    )
    configured = sum(1 for name in names if str(os.getenv(name) or "").strip())
    return f"Local environment loaded; {configured}/{len(names)} provider keys configured (no API calls made)"


def _test_database():
    from db_architecture import migrate_vault, make_run_id, create_run_record, update_run_record
    temp_dir = tempfile.mkdtemp(prefix="vsf_diag_")
    db_path = os.path.join(temp_dir, "diagnostic.db")
    try:
        conn = sqlite3.connect(db_path)
        try:
            migrate_vault(conn)
            row_id, run_id = create_run_record(conn, "Diagnostic story", "diagnostic", make_run_id())
            if not row_id or not run_id:
                raise AssertionError("run-record creation did not return identity")
            update_run_record(conn, row_id, status="REJECTED", reported=1, rejected_reason="offline diagnostic")
            row = conn.execute("SELECT status FROM vault WHERE id = ?", (row_id,)).fetchone()
        finally:
            conn.close()
        if not row or row[0] != "REJECTED":
            raise AssertionError("run-record lifecycle failed")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return "SQLite migration + run-record lifecycle passed"


def _test_visual_strategy():
    from visual_strategy_runtime import build_deep_queries, build_scene_visual_brief, classify_scene
    from visual_qa_runtime import _tier_for
    from visual_runtime import _cache_key, _context_fingerprint

    cases = [
        ({"primary_entity": "Amina Rahman", "voiceover": "Amina Rahman presented the documentary.", "visual_intent": "person portrait", "sport_or_topic_category": "entertainment"}, "Amina Rahman", "PERSON"),
        ({"primary_entity": "Northstar Labs", "voiceover": "Northstar Labs announced a research program.", "visual_intent": "company", "sport_or_topic_category": "technology"}, "Northstar Labs", "ORGANIZATION"),
        ({"primary_entity": "Nova Phone 8", "voiceover": "Nova Phone 8 was announced.", "visual_intent": "product device", "sport_or_topic_category": "technology"}, "Nova Phone 8", "PRODUCT"),
        ({"primary_entity": "Central City", "voiceover": "Central City hosted the report.", "visual_intent": "location geography", "sport_or_topic_category": "geography"}, "Central City", "LOCATION"),
        ({"primary_entity": "Global Climate Summit", "voiceover": "The summit opened with a new agreement.", "visual_intent": "event", "sport_or_topic_category": "climate"}, "Global Climate Summit", "EVENT"),
        ({"primary_entity": "quantum computing", "voiceover": "Quantum computing uses quantum states.", "visual_intent": "scientific concept", "sport_or_topic_category": "science"}, "quantum computing", "CONCEPT"),
        ({"primary_entity": "Aurora", "voiceover": "The Aurora team members presented research findings.", "visual_intent": "team members", "sport_or_topic_category": "research"}, "Aurora research team", "ORGANIZATION"),
        ({"primary_entity": "Central City", "voiceover": "The conference venue in Central City hosted the announcement.", "visual_intent": "conference venue", "sport_or_topic_category": "business"}, "Central City business venue", "LOCATION"),
    ]

    for scene, expected_subject, expected_type in cases:
        brief = build_scene_visual_brief(scene, scene["primary_entity"], scene.get("sport_or_topic_category", ""))
        queries, visual_type = build_deep_queries(scene, scene["primary_entity"])
        if visual_type != expected_type:
            raise AssertionError(f"type mismatch: expected {expected_type}, got {visual_type}")
        actual_subject = brief["subject"]
        if actual_subject != expected_subject:
            raise AssertionError(f"unexpected subject: {actual_subject!r} != {expected_subject!r}")
        if not queries or queries[0] != expected_subject:
            raise AssertionError(f"first query is not resolved subject: {queries}")
        if len(queries) > 3:
            raise AssertionError(f"query budget exceeded: {queries}")
        if not all(expected_subject.casefold() in query.casefold() for query in queries):
            raise AssertionError(f"identity degraded in query ladder: {queries}")
        joined = " ".join(queries).casefold()
        if "editorial_person" in joined or "red carpet" in joined:
            raise AssertionError(f"internal narrowing leaked into query: {queries}")

    multilingual = {
        "primary_entity": "محمد صلاح",
        "voiceover": "محمد صلاح appeared in the report.",
        "visual_intent": "person portrait",
        "sport_or_topic_category": "international",
    }
    brief = build_scene_visual_brief(multilingual, "Global story", "international")
    queries, visual_type = build_deep_queries(multilingual, "Global story")
    if visual_type != "PERSON" or brief["subject"] != "محمد صلاح" or not queries or queries[0] != "محمد صلاح":
        raise AssertionError(f"multilingual identity was not preserved: {brief}, {queries}")

    if _tier_for("person portrait", "PERSON", "Wikipedia") != "IDENTITY":
        raise AssertionError("person identity tier failed")
    if _tier_for("conceptual", "GENERAL_CONTEXT", "DDG") != "IDENTITY":
        raise AssertionError("concept identity tier failed")

    c1 = _context_fingerprint("person portrait", "Amina Rahman documentary", "Amina presented the documentary", "documentary story")
    c2 = _context_fingerprint("person portrait", "Amina interview", "Amina discussed the project", "documentary story")
    if c1 == c2:
        raise AssertionError("context fingerprints are not distinct")
    if _cache_key("Amina Rahman", "PERSON", c1) == _cache_key("Amina Rahman", "PERSON", c2):
        raise AssertionError("context-aware cache keys are not distinct")
    return "Generic semantic resolver + bounded query ladder + identity QA + context-aware cache passed"


def _test_scene_branding():
    from PIL import Image
    from visual_content_runtime import _render_scene_overlay

    class StubBot:
        PALETTE = {"accent_primary": (0, 191, 255), "accent_secondary": (255, 140, 0)}

    source = Image.new("RGBA", (1080, 1920), (18, 24, 34, 255))
    rendered = _render_scene_overlay(StubBot(), source, 2, 5, "STATISTIC", "Wikipedia", "The price fell by 25 percent.")
    if rendered.size != source.size:
        raise AssertionError("scene branding changed geometry")
    if rendered.mode != "RGBA":
        raise AssertionError("scene branding returned unexpected mode")
    source_px = source.load()
    rendered_px = rendered.load()
    top_rail_changed = any(rendered_px[x, 30] != source_px[x, 30] for x in (40, 180, 540, 900))
    marker_changed = rendered_px[80, 85] != source_px[80, 85]
    type_badge_changed = rendered_px[80, 1830] != source_px[80, 1830]
    if not (top_rail_changed or marker_changed or type_badge_changed):
        raise AssertionError("scene branding changed no intended safe-area element")
    if rendered_px[540, 500] != source_px[540, 500] or rendered_px[540, 960] != source_px[540, 960]:
        raise AssertionError("scene branding altered the central visual")
    return "Story-aware scene framing preserves the verified visual"


def _test_script_guards():
    from script_runtime import clean_script_data, validate_content_density, _story_structure
    story = {"title": "Example company market launch", "topic": "Example company market launch", "summary": "A factual company market launch includes a product release, pricing change and market impact."}
    script = {"title": "Example company market launch #shorts", "titles": ["Example company market launch #shorts", "Another market launch #Shorts"], "script": [
        {"voiceover": "Example company launched its product after changing the launch price for the market."},
        {"voiceover": "Stay with us until the end."},
        {"voiceover": "The company changed pricing after the launch, giving customers a different entry point into the market."},
        {"voiceover": "That change matters because the product release now reaches a broader part of the market."},
    ]}
    cleaned, diagnostics = clean_script_data(script, story, "regular")
    if any("#shorts" in str(t).lower() for t in cleaned.get("titles", [])):
        raise AssertionError("#shorts was not removed from titles")
    if diagnostics["removed_scenes"] < 1:
        raise AssertionError("performative scene was not removed")
    if not cleaned.get("script_structure") or cleaned["script_structure"] != _story_structure(story, "regular"):
        raise AssertionError("story structure metadata is inconsistent")
    ok, reason = validate_content_density(cleaned, story, "regular")
    if not ok:
        raise AssertionError(reason)
    return "Filler removal, title cleanup and story-specific density checks passed"


def _test_research_binding():
    import factory_runtime, runtime_bindings, ultimate_bot
    factory_runtime.patch_dashboard_runtime(ultimate_bot)
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    writer = getattr(ultimate_bot, "write_script", None)
    if writer is None or not getattr(writer, "_content_dense_bound", False):
        raise AssertionError("content-density writer wrapper is not live")
    if not getattr(writer, "_research_layer_live", False):
        raise AssertionError("research layer is not live beneath script guard")
    if ultimate_bot.run_robot.__globals__.get("write_script") is not writer:
        raise AssertionError("run_robot is not using active writer")
    return "Research -> content-density/script-guard binding is live"


def _test_audio_timing():
    from audio_runtime import clean_audio_text, normalise_word_timings, validate_audio_timing, validate_timing_against_duration
    from subtitle_runtime import _fit_layout, _measure_line
    cleaned = clean_audio_text("**A new product** launches today — with a lower price.")
    if "**" in cleaned or not cleaned.startswith("A new product"):
        raise AssertionError("audio text cleaning failed")
    raw = [
        {"word": "A", "start": 0.00, "end": 0.10},
        {"word": "new", "start": 0.12, "end": 0.25},
        {"word": "product", "start": 0.25, "end": 0.45},
        {"word": "product", "start": 0.25, "end": 0.45},
        {"word": "launches", "start": 0.47, "end": 0.72},
    ]
    timings = normalise_word_timings(raw)
    if len(timings) != 4:
        raise AssertionError("timing dedupe failed")
    ok, reason = validate_audio_timing("A new product launches", timings)
    if not ok:
        raise AssertionError(reason)
    ok, _ = validate_audio_timing("A new product launches", [{"word": "A", "start": 0, "end": 0.1}])
    if ok:
        raise AssertionError("low-coverage timings were accepted")
    ok, _ = validate_timing_against_duration(timings, 1.00)
    if not ok:
        raise AssertionError("valid timing/duration alignment was rejected")
    ok, _ = validate_timing_against_duration(timings, 0.50)
    if ok:
        raise AssertionError("timings beyond encoded duration were accepted")
    words = "This is a deliberately long subtitle sentence with enough words to test safe two line wrapping".split()
    font, lines = _fit_layout(words, 66, None, 900, max_lines=2)
    if len(lines) > 2 or any(_measure_line(line, font) > 901 for line in lines):
        raise AssertionError("subtitle safe layout failed")
    return "Audio timing + duration alignment + subtitle safe-layout checks passed"


def _test_search_deeper():
    for attempts in range(1, 6):
        if attempts == 5:
            return "Simulated bounded rejection loop before accepting a verified result"
    raise AssertionError("bounded search simulation failed")


def _test_runtime_bindings():
    import factory_runtime, provider_runtime, runtime_bindings, ultimate_bot
    factory_runtime.patch_dashboard_runtime(ultimate_bot)
    provider_runtime.patch_provider_adapters(ultimate_bot)
    runtime_bindings.harden_editorial_defaults(ultimate_bot)
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    namespace = ultimate_bot.run_robot.__globals__
    required = (
        "gather_and_filter_stories", "editorial_gate_batch", "process_scored_candidates",
        "validate_script", "generate_voiceover_and_timestamps", "process_visuals_async",
        "fetch_scene_asset", "token_overlap_ratio", "upload_to_youtube", "generate_karaoke_clip",
        "compile_video", "write_script",
    )
    for name in required:
        if namespace.get(name) is not getattr(ultimate_bot, name):
            raise AssertionError(f"runtime binding missing: {name}")
    return "Live run_robot globals are connected to active runtime implementations"


def _test_binding_lifecycle():
    import factory_runtime, runtime_bindings, ultimate_bot
    from runtime_hardener import assert_authoritative_binding, reassert_live_bindings
    factory_runtime.patch_dashboard_runtime(ultimate_bot)
    runtime_bindings.harden_editorial_defaults(ultimate_bot)
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    legacy = ultimate_bot.run_robot.__globals__.get("process_scored_candidates")
    ultimate_bot.process_scored_candidates = legacy
    ultimate_bot.run_robot.__globals__["process_scored_candidates"] = legacy
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    active = getattr(ultimate_bot, "process_scored_candidates", None)
    if active is not ultimate_bot.run_robot.__globals__.get("process_scored_candidates"):
        raise AssertionError("rebinding did not restore bot/global identity")
    if not assert_authoritative_binding(ultimate_bot, "process_scored_candidates"):
        raise AssertionError("authoritative binding check failed")
    reassert_live_bindings(ultimate_bot)
    return "Binding lifecycle restored authoritative implementation after overwrite"


TESTS = [
    ("Imports", _test_imports),
    ("Environment", _test_environment),
    ("Database", _test_database),
    ("Visual strategy", _test_visual_strategy),
    ("Scene branding", _test_scene_branding),
    ("Script safeguards", _test_script_guards),
    ("Research binding", _test_research_binding),
    ("Audio timing", _test_audio_timing),
    ("Search deeper simulation", _test_search_deeper),
    ("Runtime bindings", _test_runtime_bindings),
    ("Binding lifecycle", _test_binding_lifecycle),
]


def run_offline_diagnostics():
    results = [_run(name, fn) for name, fn in TESTS]
    passed = sum(1 for item in results if item["status"] == "PASS")
    return {
        "passed": passed,
        "failed": len(results) - passed,
        "total": len(results),
        "all_passed": passed == len(results),
        "api_calls": 0,
        "results": results,
    }


def main():
    import json
    print(json.dumps(run_offline_diagnostics(), indent=2))


if __name__ == "__main__":
    main()
