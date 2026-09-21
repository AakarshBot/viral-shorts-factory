"""Offline, no-API diagnostics for the supported Viral Shorts Factory path."""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import traceback
from pathlib import Path


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
        "visual_qa_runtime", "visual_strategy_runtime", "visual_semantic_guard_runtime",
        "visual_query_entities_runtime", "visual_content_runtime", "visual_retrieval_runtime",
        "visual_provider_boundary_runtime", "provider_runtime", "quality_runtime",
        "runtime_bindings", "workflow_runtime", "subtitle_runtime", "youtube_comment_runtime",
    ]
    for name in modules:
        __import__(name)
    return f"Imported {len(modules)} supported factory modules"


def _test_environment():
    names = (
        "GEMINI_API_KEY", "GROQ_API_KEY",
        "UNSPLASH_ACCESS_KEY", "HF_TOKEN", "PEXELS_API_KEY",
    )
    configured = sum(1 for name in names if str(os.getenv(name) or "").strip())
    return f"Local environment loaded; {configured}/{len(names)} provider keys configured (no API calls made)"


def _test_database():
    from db_architecture import create_run_record, make_run_id, migrate_vault, update_run_record
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
    from visual_query_entities_runtime import build_candidate_scene
    from visual_semantic_guard_runtime import meaningful_tokens, resolve_subject
    from manual_visual_query_runtime import assign_manual_queries, parse_manual_visual_queries
    from visual_strategy_runtime import build_deep_queries, build_scene_visual_brief
    from visual_qa_runtime import GEMINI_VISUAL_BATCH_SIZE, strict_gemini_check_batch
    from visual_runtime import _cache_key, _context_fingerprint

    cases = [
        ({"primary_entity": "Amina Rahman", "voiceover": "Amina Rahman presented the documentary.", "visual_intent": "person portrait"}, "Amina Rahman", "PERSON"),
        ({"primary_entity": "Northstar Labs", "voiceover": "Northstar Labs announced a research program.", "visual_intent": "company"}, "Northstar Labs", "ORGANIZATION"),
        ({"primary_entity": "Nova Phone 8", "voiceover": "Nova Phone 8 was announced.", "visual_intent": "product device"}, "Nova Phone 8", "PRODUCT"),
        ({"primary_entity": "Central City", "voiceover": "Central City hosted the report.", "visual_intent": "location geography"}, "Central City", "LOCATION"),
        ({"primary_entity": "Global Climate Summit", "voiceover": "The summit opened with a new agreement.", "visual_intent": "event"}, "Global Climate Summit", "EVENT"),
        ({"primary_entity": "quantum computing", "voiceover": "Quantum computing uses quantum states.", "visual_intent": "scientific concept"}, "quantum computing", "CONCEPT"),
    ]
    for scene, expected_subject, expected_type in cases:
        brief = build_scene_visual_brief(scene, scene["primary_entity"], "")
        queries, visual_type = build_deep_queries(scene, scene["primary_entity"])
        if visual_type != expected_type:
            raise AssertionError(f"type mismatch: {queries}, {visual_type}")
        if brief["subject"] != expected_subject or not queries:
            raise AssertionError(f"identity-first visual contract failed: {brief}, {queries}")
        subject_keys = set(meaningful_tokens(expected_subject))
        first_keys = set(meaningful_tokens(queries[0]))
        if not subject_keys.issubset(first_keys):
            raise AssertionError(f"first visual query lost factual identity: {queries}")
        if len(queries) > 6:
            raise AssertionError(f"visual query budget exceeded: {queries}")

    malformed = {
        "primary_entity": "Not Northstar Research Summit",
        "voiceover": "Not just a one-off thing — Northstar Research Summit could continue hosting in Berlin next year &nbsp;.",
        "visual_intent": "news_event",
        "specific_search_prompt": "news_event",
    }
    resolution = resolve_subject(malformed, "Northstar Research Summit")
    if resolution["visual_type"] != "EVENT" or resolution["subject"] != "Northstar Research Summit Berlin":
        raise AssertionError(f"malformed visual subject not recovered: {resolution}")
    queries, visual_type = build_deep_queries(malformed, "Northstar Research Summit")
    joined = " ".join(queries).casefold()
    if visual_type != "EVENT" or not queries or not set(meaningful_tokens("Northstar Research Summit Berlin")).issubset(set(meaningful_tokens(queries[0]))):
        raise AssertionError(f"malformed visual query not corrected: {queries}")
    if any(bad in joined.split() for bad in ("not", "nbsp", "thing")) or "&nbsp;" in joined:
        raise AssertionError(f"query retained discourse/html noise: {queries}")

    stale = {"primary_entity": "Indian cricket team lifting T20 World Cup trophy", "visual_type": "LOCATION", "visual_intent": "team trophy celebration"}
    prepared = build_candidate_scene(stale, stale["primary_entity"])
    if prepared["visual_type"] != "ORGANIZATION":
        raise AssertionError(f"stale type override survived: {prepared}")

    multilingual = {"primary_entity": "محمد صلاح", "voiceover": "محمد صلاح appeared in the report.", "visual_intent": "person portrait"}
    brief = build_scene_visual_brief(multilingual, "Global story", "international")
    queries, visual_type = build_deep_queries(multilingual, "Global story")
    if visual_type != "PERSON" or brief["subject"] != "محمد صلاح" or not queries or not set(meaningful_tokens("محمد صلاح")).issubset(set(meaningful_tokens(queries[0]))):
        raise AssertionError("multilingual identity was not preserved")

    if GEMINI_VISUAL_BATCH_SIZE != 10:
        raise AssertionError(f"entity QA batch size changed unexpectedly: {GEMINI_VISUAL_BATCH_SIZE}")
    if not callable(strict_gemini_check_batch):
        raise AssertionError("entity-only batch QA bridge is unavailable")
    manual = parse_manual_visual_queries("Shubman Gill batting; India Afghanistan cricket match; New Delhi stadium")
    if len(manual) != 3:
        raise AssertionError(f"manual query parsing failed: {manual}")
    manual_scenes = [
        {"primary_entity": "India Afghanistan", "voiceover": "India and Afghanistan play the final match."},
        {"primary_entity": "Shubman Gill", "voiceover": "Shubman Gill leads India's batting."},
        {"primary_entity": "New Delhi", "voiceover": "The match is being played in New Delhi."},
    ]
    assignments = assign_manual_queries(manual_scenes, manual)
    if len(assignments) != 3 or any(not item.get("query") for item in assignments):
        raise AssertionError(f"manual visual assignment failed: {assignments}")
    entity_prompt = strict_gemini_check_batch.__doc__ or ""
    if "entity" not in entity_prompt.casefold():
        raise AssertionError("entity-only batch QA contract is not exposed")
    c1 = _context_fingerprint("person portrait", "Amina documentary", "Amina presented it", "story")
    c2 = _context_fingerprint("person portrait", "Amina interview", "Amina discussed it", "story")
    if c1 == c2 or _cache_key("Amina Rahman", "PERSON", c1) == _cache_key("Amina Rahman", "PERSON", c2):
        raise AssertionError("context-aware cache identity failed")
    return "Identity-first semantic visual strategy, multilingual identity, manual query routing and context-aware cache checks passed"


def _test_visual_queries():
    """Show the bounded automatic visual-query fallback ladder used by retrieval."""
    from visual_strategy_runtime import build_deep_queries

    scene = {
        "primary_entity": "Shubman Gill",
        "voiceover": "Shubman Gill leads India's batting in the final match.",
        "visual_intent": "person action batting",
        "visual_context": "batting match cricket stadium",
        "specific_search_prompt": "Shubman Gill batting match",
    }
    queries, visual_type = build_deep_queries(scene, "India Afghanistan final")
    if not queries:
        raise AssertionError("no visual queries were generated")
    if len(queries) > 6:
        raise AssertionError(f"visual query budget exceeded: {queries}")
    if queries[-1].casefold() != "shubman gill":
        raise AssertionError(f"identity fallback missing: {queries}")
    return "Visual query fallback ladder (up to 6):\n" + "\n".join(
        f"{index}. {query}" for index, query in enumerate(queries, 1)
    ) + f"\nVisual type: {visual_type}"


def _test_scene_branding():
    from PIL import Image

    from branding_runtime import (
        LOGO_BOX_SIZE,
        build_scene_branding_overlays,
        source_credit_for_type,
    )

    source = Image.new("RGBA", (1080, 1920), (18, 24, 34, 255))
    layers = build_scene_branding_overlays(None, 1080, 1920, "Source: Reuters")

    if len(layers) != 2:
        raise AssertionError("final branding must expose logo/frame and source layers")
    if any(layer.shape != (1920, 1080, 4) for layer in layers):
        raise AssertionError("final branding layers changed output geometry")
    if layers[0][36:36 + LOGO_BOX_SIZE, 1080 - 36 - LOGO_BOX_SIZE:1080 - 36, 3].max() <= 0:
        raise AssertionError("top-right logo glass badge is not rendered")
    if layers[1][-100:, -420:, 3].max() <= 0:
        raise AssertionError("bottom-right source badge is not rendered")
    if source_credit_for_type("news_source", "Source: Reuters") != "SOURCE · Reuters":
        raise AssertionError("source credit normalization failed")
    return "Final branding owns the fixed logo/frame and dynamic source-credit overlays"

def _test_script_and_audio():
    from audio_runtime import clean_audio_text, normalise_word_timings, validate_audio_timing, validate_timing_against_duration
    from script_guard_runtime import looks_like_instructional_narration
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
    text = clean_audio_text("**A new product** launches today")
    if "**" in text or not text.startswith("A new product"):
        raise AssertionError("audio text cleaning failed")
    ok, reason = validate_audio_timing("A new product launches", timings)
    if not ok:
        raise AssertionError(reason)
    ok, _ = validate_timing_against_duration(timings, 0.50)
    if ok:
        raise AssertionError("timings beyond encoded duration were accepted")
    if not looks_like_instructional_narration("Return only a valid JSON object with voiceover fields."):
        raise AssertionError("instructional narration guard failed")
    if looks_like_instructional_narration("Amina Rahman presented the documentary."):
        raise AssertionError("real narration was flagged as instructional")
    return "Script guard + audio cleaning + timing/duration alignment passed"


def _test_runtime_bindings():
    import factory_runtime, provider_runtime, runtime_bindings, ultimate_bot
    factory_runtime.patch_dashboard_runtime(ultimate_bot)
    provider_runtime.patch_provider_adapters(ultimate_bot)
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    namespace = ultimate_bot.run_robot.__globals__
    required = (
        "gather_and_filter_stories", "editorial_gate_batch", "process_scored_candidates", "validate_script",
        "self_critique_pass", "write_script", "generate_voiceover_and_timestamps", "process_visuals_async",
        "upload_to_youtube", "generate_karaoke_clip", "compile_video",
    )
    missing = [name for name in required if name not in namespace]
    if missing:
        raise AssertionError(f"runtime namespace missing required binding(s): {missing}")
    return "Core runtime bindings are complete"


def _test_provider_boundary():
    from test_visual_provider_boundary import (
        test_active_retrieval_plan_does_not_bind_legacy_bot_provider_methods,
        test_person_source_plan_uses_raw_multi_candidate_adapters_not_bot_fetchers,
        test_raw_candidate_adapters_have_no_legacy_quality_gate_dependency,
    )
    test_person_source_plan_uses_raw_multi_candidate_adapters_not_bot_fetchers()
    test_raw_candidate_adapters_have_no_legacy_quality_gate_dependency()
    test_active_retrieval_plan_does_not_bind_legacy_bot_provider_methods()
    return "Raw-provider boundary regression passed"


def _test_manual_visual_queries():
    from manual_visual_query_runtime import assign_manual_queries, parse_manual_visual_queries
    queries = parse_manual_visual_queries(
        "India Afghanistan cricket match; Shubman Gill batting; New Delhi cricket stadium"
    )
    scenes = [
        {"primary_entity": "India Afghanistan", "voiceover": "India and Afghanistan play the final match."},
        {"primary_entity": "Shubman Gill", "voiceover": "Shubman Gill leads India's batting."},
        {"primary_entity": "New Delhi", "voiceover": "The match is being played in New Delhi."},
    ]
    assignments = assign_manual_queries(scenes, queries)
    if len(queries) != 3 or len(assignments) != 3 or any(not item.get("query") for item in assignments):
        raise AssertionError(f"manual visual query routing failed: {assignments}")
    return "Manual semicolon-separated visual queries are parsed and assigned to the relevant scenes"


def _test_factory_function_coverage():
    from factory_function_coverage import collect_factory_function_coverage
    report = collect_factory_function_coverage(Path(__file__).resolve().parent)
    if not report["complete"]:
        raise AssertionError(
            f"Factory function coverage is incomplete: unmapped={report['unmapped']}, "
            f"stale={report['stale_map']}"
        )
    if report["total"] < 1:
        raise AssertionError("No ultimate_bot functions were discovered.")
    return (
        f"All {report['total']} ultimate_bot functions are explicitly accounted for "
        "as Live Factory, Channel Statistics, Demo / Diagnostics, or deliberate Internal."
    )


def _test_dashboard_architecture():
    root = Path(__file__).resolve().parent
    required = ["app.py", "ultimate_bot.py", "workflow_runtime.py", "visual_retrieval_runtime.py", "final_qc_runtime.py"]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise AssertionError(f"required supported files missing: {missing}")
    forbidden_paths = [
        "app_legacy.py", "app.py.mybackup", "newsroom_dashboard.py",
        "apply_production_fixes.py", "apply_script_fallback_fix.py", "apply_selected_story_lock_fix.py",
        "visual_query_lock_runtime.py", "visual_replacement_runtime.py", "visual_resilience_runtime.py",
        "workflow_progress_runtime.py", "upload_runtime.py",
        "test_phase_runtime.py", "test_phase_patches.py", "test_history_runtime.py",
    ]
    still_present = [name for name in forbidden_paths if (root / name).exists()]
    if (root / "pages").exists():
        still_present.append("pages/")
    if still_present:
        raise AssertionError(f"legacy dashboard artifacts remain: {still_present}")
    source = (root / "app.py").read_text(encoding="utf-8")
    forbidden_ui = ("st.experimental_dialog", "newsroom_dashboard", "app_legacy")
    leaked = [token for token in forbidden_ui if token in source]
    if leaked:
        raise AssertionError(f"obsolete dashboard UI leaked into app.py: {leaked}")
    legacy_paging_ui = "See next " in source and "candidate_next_page" in source
    ranked_headline_ui = (
        "Ranked headlines" in source
        and "Use headline →" in source
        and "candidate_page" in source
    )
    event_topic_ui = (
        "Event radar" in source
        and "TOPIC #" in source
        and "Use topic →" in source
        and "candidate_page" in source
    )
    if not (legacy_paging_ui or ranked_headline_ui or event_topic_ui):
        raise AssertionError("topic-selection UI is missing from the canonical dashboard")
    if event_topic_ui:
        surface = "event topic cards"
    elif ranked_headline_ui:
        surface = "ranked headline cards"
    else:
        surface = "legacy paging"
    return f"Single-dashboard architecture and {surface} surface passed"


def run_offline_diagnostics():
    checks = [
        ("imports", _test_imports),
        ("environment", _test_environment),
        ("database", _test_database),
        ("visual_strategy", _test_visual_strategy),
        ("scene_branding", _test_scene_branding),
        ("script_audio", _test_script_and_audio),
        ("runtime_bindings", _test_runtime_bindings),
        ("provider_boundary", _test_provider_boundary),
        ("manual_visual_queries", _test_manual_visual_queries),
        ("dashboard_architecture", _test_dashboard_architecture),
        ("factory_function_coverage", _test_factory_function_coverage),
    ]
    results = [_run(name, fn) for name, fn in checks]
    passed = sum(item["status"] == "PASS" for item in results)
    failed = len(results) - passed
    return {
        "results": results,
        "passed": passed,
        "failed": failed,
        "total": len(results),
        "all_passed": failed == 0,
        "api_calls": 0,
    }
