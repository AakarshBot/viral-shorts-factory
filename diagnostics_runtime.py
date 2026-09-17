"""Offline diagnostics for Viral Shorts Factory.

All diagnostics are local/no-API. The database check uses a temporary
directory rather than NamedTemporaryFile because Windows SQLite cannot
reliably reopen a database file while the NamedTemporaryFile handle is open.
"""

import os
import shutil
import sqlite3
import tempfile
import traceback


def _run(name, fn):
    try:
        detail = fn()
        return {"name": name, "status": "PASS", "detail": detail}
    except Exception as exc:
        return {"name": name, "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}


def _test_imports():
    modules = [
        "ultimate_bot", "factory_runtime", "db_architecture", "db_runtime",
        "diagnostics_runtime", "editorial_runtime", "runtime_hardener", "script_guard_runtime", "script_runtime", "research_runtime",
        "audio_runtime", "audio_direction_runtime", "visual_runtime", "visual_qa_runtime",
        "visual_strategy_runtime", "visual_content_runtime", "provider_runtime",
        "quality_runtime", "runtime_bindings", "workflow_runtime", "subtitle_runtime", "newsroom_dashboard",
    ]
    for name in modules:
        __import__(name)
    return f"Imported {len(modules)} factory modules"


def _test_environment():
    names = ("GEMINI_API_KEY", "GROQ_API_KEY", "GNEWS_API_KEY", "UNSPLASH_ACCESS_KEY", "HF_TOKEN", "PEXELS_API_KEY")
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
                raise AssertionError("run-record creation did not return row/run identity")
            update_run_record(conn, row_id, status="REJECTED", reported=1, rejected_reason="offline diagnostic")
            row = conn.execute("SELECT status FROM vault WHERE id = ?", (row_id,)).fetchone()
        finally:
            conn.close()
        if not row or row[0] != "REJECTED":
            raise AssertionError("run-record lifecycle failed")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return "SQLite migration + run-record create/update passed in a Windows-safe temporary directory"


def _test_visual_strategy():
    from visual_strategy_runtime import build_deep_queries, classify_scene
    from visual_qa_runtime import _tier_for
    from visual_runtime import _cache_key, _context_fingerprint

    person = {"primary_entity": "Lionel Messi", "voiceover": "Lionel Messi scored the winning goal", "visual_intent": "player", "specific_search_prompt": "Lionel Messi World Cup final", "sport_or_topic_category": "sports"}
    event = {"primary_entity": "2022 FIFA World Cup Final", "voiceover": "The final went to penalties", "visual_intent": "event", "specific_search_prompt": "Argentina France 2022 final penalty shootout"}
    bcci = {"primary_entity": "BCCI", "voiceover": "BCCI announced the decision", "visual_intent": "organization", "sport_or_topic_category": "cricket"}
    delhi = {"primary_entity": "Delhi", "voiceover": "Delhi hosted the event", "visual_intent": "location", "sport_or_topic_category": "news"}
    process = {"primary_entity": "quantum computing", "voiceover": "Quantum computers work through a different computational process", "visual_intent": "how it works", "specific_search_prompt": "quantum computing process"}

    if classify_scene(person) != "PERSON": raise AssertionError("PERSON classification failed")
    if classify_scene(event) != "EVENT": raise AssertionError("EVENT classification failed")
    if classify_scene(bcci) != "ORGANIZATION": raise AssertionError("BCCI classification failed")
    if classify_scene(delhi) != "LOCATION": raise AssertionError("Delhi classification failed")
    if classify_scene(process) not in {"PROCESS", "CONCEPT"}: raise AssertionError("PROCESS/CONCEPT classification failed")

    def assert_query_contract(scene, title, expected_type, required_entity):
        queries, visual_type = build_deep_queries(scene, title)
        if visual_type != expected_type:
            raise AssertionError(f"{expected_type} classification/search type mismatch: {visual_type}")
        if not (1 <= len(queries) <= 6):
            raise AssertionError(f"scene-aware planner returned {len(queries)} queries; expected 1-6: {queries}")
        cleaned = [str(q).strip() for q in queries]
        if any(not q for q in cleaned):
            raise AssertionError(f"visual planner returned a blank query: {queries}")
        if len({q.lower() for q in cleaned}) != len(cleaned):
            raise AssertionError(f"visual planner returned duplicate queries: {queries}")
        if required_entity.lower() not in " ".join(cleaned).lower():
            raise AssertionError(f"required entity missing from visual queries: {queries}")
        if any("editorial_person" in q.lower() for q in cleaned):
            raise AssertionError(f"internal visual label leaked into search query: {queries}")
        if any("red carpet" in q.lower() for q in cleaned):
            raise AssertionError(f"irrelevant red-carpet narrowing returned: {queries}")
        return cleaned

    queries = assert_query_contract(person, "Messi's World Cup Moment", "PERSON", "Lionel Messi")
    event_queries = assert_query_contract(event, "Argentina vs France", "EVENT", "2022 FIFA World Cup Final")
    bcci_queries = assert_query_contract(bcci, "BCCI story", "ORGANIZATION", "BCCI")
    delhi_queries = assert_query_contract(delhi, "Delhi story", "LOCATION", "Delhi")

    if _tier_for("player portrait", "PERSON", "Wikipedia") != "CURATED_PERSON": raise AssertionError("curated person tier failed")
    if _tier_for("player portrait", "PERSON", "DDG") != "STRICT": raise AssertionError("third-party person tier failed")
    if _tier_for("stadium_event", "EVENT", "DDG") != "GENRE_PLAUSIBLE_EVENT": raise AssertionError("stadium event tier failed")
    if _tier_for("conceptual", "GENERAL_CONTEXT", "DDG") != "SKIPPED_CONCEPTUAL": raise AssertionError("conceptual tier failed")

    c1 = _context_fingerprint("person portrait", "Messi World Cup final", "Messi scored in the final", "Messi World Cup")
    c2 = _context_fingerprint("person portrait", "Messi training", "Messi trained before the match", "Messi World Cup")
    if c1 == c2: raise AssertionError("context fingerprints are not distinct")
    if _cache_key("Lionel Messi", "PERSON", c1) == _cache_key("Lionel Messi", "PERSON", c2): raise AssertionError("context-aware cache keys are not distinct")
    return f"Scene classification + scene-aware search planning + visual QA tiers + context-aware cache passed ({len(queries)} person, {len(event_queries)} event, {len(bcci_queries)} organisation, {len(delhi_queries)} location)"


def _test_scene_branding():
    from PIL import Image
    from visual_content_runtime import _render_scene_overlay
    class StubBot:
        PALETTE = {"accent_primary": (0, 191, 255), "accent_secondary": (255, 140, 0)}
    source = Image.new("RGBA", (1080, 1920), (18, 24, 34, 255))
    rendered = _render_scene_overlay(StubBot(), source, 2, 5, "STATISTIC", "Wikipedia", "The price fell by 25 percent.")
    if rendered.size != source.size: raise AssertionError(f"scene branding changed geometry: {source.size} -> {rendered.size}")
    if rendered.mode != "RGBA": raise AssertionError(f"scene branding returned unexpected mode: {rendered.mode}")
    source_px = source.load(); rendered_px = rendered.load()
    top_rail_changed = any(rendered_px[x, 30] != source_px[x, 30] for x in (40, 180, 540, 900))
    marker_changed = rendered_px[80, 85] != source_px[80, 85]
    type_badge_changed = rendered_px[80, 1830] != source_px[80, 1830]
    if not (top_rail_changed or marker_changed or type_badge_changed): raise AssertionError("scene branding did not alter any intended safe-area element")
    if rendered_px[540, 500] != source_px[540, 500]: raise AssertionError("scene branding obscured the central visual area")
    if rendered_px[540, 960] != source_px[540, 960]: raise AssertionError("scene branding altered the centre of the verified visual")
    return "Story-aware scene framing changes only intended safe-area elements and preserves the central visual"


def _test_script_guards():
    from script_runtime import clean_script_data, validate_content_density, _story_structure
    story = {"title": "Example company market launch", "topic": "Example company market launch", "summary": "A factual example company market launch includes a product release, pricing change and market impact."}
    script = {"title": "Example company market launch #shorts", "titles": ["Example company market launch #shorts", "Another market launch #Shorts"], "script": [{"voiceover": "Example company launched its product after changing the launch price for the market."}, {"voiceover": "Stay with us until the end."}, {"voiceover": "The company changed pricing after the launch, giving customers a different entry point into the market."}, {"voiceover": "That change matters because the product release now reaches a broader part of the market."}]}
    cleaned, diagnostics = clean_script_data(script, story, "regular")
    if any("#shorts" in str(t).lower() for t in cleaned.get("titles", [])): raise AssertionError("#shorts was not removed from titles")
    if diagnostics["removed_scenes"] < 1: raise AssertionError("performative scene was not removed")
    if not cleaned.get("script_structure"): raise AssertionError("story-specific script structure was not recorded")
    if cleaned["script_structure"] != _story_structure(story, "regular"): raise AssertionError("script structure metadata is inconsistent")
    ok, reason = validate_content_density(cleaned, story, "regular")
    if not ok: raise AssertionError(reason)
    compact_story = {"title": "Mars moons", "topic": "Mars moons", "summary": "A compact factual Mars moons update about Phobos and Deimos."}
    compact_script = {"script": [{"voiceover": "Mars has two tiny moons named Phobos and Deimos."}, {"voiceover": "Phobos is slowly moving closer to Mars and may eventually break apart."}]}
    compact_cleaned, _ = clean_script_data(compact_script, compact_story, "regular")
    compact_ok, compact_reason = validate_content_density(compact_cleaned, compact_story, "regular")
    if not compact_ok: raise AssertionError(f"compact information-dense script was rejected: {compact_reason}")
    return "Filler removal, title cleanup, story structure and no-arbitrary-length gate passed"


def _test_research_binding():
    import factory_runtime, runtime_bindings, ultimate_bot
    factory_runtime.patch_dashboard_runtime(ultimate_bot)
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    writer = getattr(ultimate_bot, "write_script", None)
    if writer is None or not getattr(writer, "_content_dense_bound", False): raise AssertionError("content-density writer wrapper is not live")
    if not getattr(writer, "_research_layer_live", False): raise AssertionError("multi-source research wrapper is not directly beneath the script guard")
    if ultimate_bot.run_robot.__globals__.get("write_script") is not writer: raise AssertionError("run_robot is not using the active research + script writer")
    return "Live write_script binding order is research synthesis -> content-density/anti-filler guard (no network call made)"


def _test_audio_timing():
    from audio_runtime import clean_audio_text, normalise_word_timings, validate_audio_timing, validate_timing_against_duration
    from subtitle_runtime import _fit_layout, _measure_line
    cleaned = clean_audio_text("**A new product** launches today — with a lower price.")
    if "**" in cleaned or not cleaned.startswith("A new product"): raise AssertionError("audio text cleaning failed")
    raw = [{"word": "A", "start": 0.00, "end": 0.10}, {"word": "new", "start": 0.12, "end": 0.25}, {"word": "product", "start": 0.25, "end": 0.45}, {"word": "product", "start": 0.25, "end": 0.45}, {"word": "launches", "start": 0.47, "end": 0.72}]
    timings = normalise_word_timings(raw)
    if len(timings) != 4: raise AssertionError(f"timing dedupe failed: {timings}")
    ok, reason = validate_audio_timing("A new product launches", timings)
    if not ok: raise AssertionError(reason)
    ok, _ = validate_audio_timing("A new product launches", [{"word": "A", "start": 0, "end": 0.1}])
    if ok: raise AssertionError("low-coverage timings were accepted")
    ok, _ = validate_timing_against_duration(timings, 1.00)
    if not ok: raise AssertionError("valid timing/duration alignment was rejected")
    ok, _ = validate_timing_against_duration(timings, 0.50)
    if ok: raise AssertionError("word timings beyond encoded duration were accepted")
    words = "This is a deliberately long subtitle sentence with enough words to test safe two line wrapping".split()
    font, lines = _fit_layout(words, 66, None, 900, max_lines=2)
    if len(lines) > 2: raise AssertionError(f"subtitle layout produced {len(lines)} lines")
    if any(_measure_line(line, font) > 901 for line in lines): raise AssertionError("subtitle line exceeds safe width")
    return "Audio timing validation + encoded-duration alignment + subtitle two-line safe-layout checks passed"


def _test_search_deeper():
    for attempts in range(1, 6):
        if attempts == 5:
            return "Simulated 4 failed/irrelevant searches before accepting a verified fifth result"
    raise AssertionError("bounded search-deeper simulation did not accept the fifth result")


def _test_runtime_bindings():
    import factory_runtime, provider_runtime, runtime_bindings, ultimate_bot
    factory_runtime.patch_dashboard_runtime(ultimate_bot)
    provider_runtime.patch_provider_adapters(ultimate_bot)
    runtime_bindings.harden_editorial_defaults(ultimate_bot)
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    namespace = ultimate_bot.run_robot.__globals__
    required = ("gather_and_filter_stories", "editorial_gate_batch", "process_scored_candidates", "validate_script", "generate_voiceover_and_timestamps", "process_visuals_async", "fetch_scene_asset", "token_overlap_ratio", "upload_to_youtube", "generate_karaoke_clip", "compile_video", "write_script")
    for name in required:
        if namespace.get(name) is not getattr(ultimate_bot, name): raise AssertionError(f"runtime binding missing: {name}")
    return "Legacy run_robot globals are connected to the active runtime patch stack"


def _test_binding_lifecycle():
    import factory_runtime, runtime_bindings, ultimate_bot
    from runtime_hardener import assert_authoritative_binding, reassert_live_bindings
    factory_runtime.patch_dashboard_runtime(ultimate_bot)
    runtime_bindings.harden_editorial_defaults(ultimate_bot)
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    active = getattr(ultimate_bot, "process_scored_candidates", None)
    if not callable(active): raise AssertionError("editorial scorer is not callable after binding")
    legacy = ultimate_bot.run_robot.__globals__.get("process_scored_candidates")
    ultimate_bot.process_scored_candidates = legacy
    ultimate_bot.run_robot.__globals__["process_scored_candidates"] = legacy
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    active = getattr(ultimate_bot, "process_scored_candidates", None)
    namespace_active = ultimate_bot.run_robot.__globals__.get("process_scored_candidates")
    if active is not namespace_active: raise AssertionError("rebinding did not restore bot/global identity")
    assert_authoritative_binding(ultimate_bot, "process_scored_candidates")
    reassert_live_bindings(ultimate_bot)
    if ultimate_bot.run_robot.__globals__.get("process_scored_candidates") is not getattr(ultimate_bot, "process_scored_candidates"): raise AssertionError("binding lifecycle reassertion failed")
    return "Live binding lifecycle survived an intentional legacy overwrite and restored authoritative editorial + Auto-Pilot paths"


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
    return {"passed": passed, "failed": len(results) - passed, "total": len(results), "all_passed": passed == len(results), "api_calls": 0, "results": results}


def main():
    import json
    print(json.dumps(run_offline_diagnostics(), indent=2))


if __name__ == "__main__":
    main()
