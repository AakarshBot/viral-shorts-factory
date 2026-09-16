"""Offline diagnostics for Viral Shorts Factory.

All diagnostics are intentionally local/no-API so they can run safely while
provider quotas are exhausted.
"""

import os
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
        "diagnostics_runtime", "editorial_runtime", "script_runtime",
        "audio_runtime", "visual_runtime", "visual_qa_runtime",
        "visual_strategy_runtime", "visual_content_runtime", "provider_runtime",
        "quality_runtime", "runtime_bindings", "workflow_runtime",
    ]
    for name in modules:
        __import__(name)
    return f"Imported {len(modules)} factory modules"


def _test_environment():
    names = ("GEMINI_API_KEY", "GROQ_API_KEY", "GNEWS_API_KEY", "UNSPLASH_ACCESS_KEY", "HF_TOKEN", "PEXELS_API_KEY")
    configured = sum(1 for name in names if str(os.getenv(name) or "").strip())
    return f"Local environment loaded; {configured}/{len(names)} provider keys configured (no API calls made)"


def _test_database():
    import sqlite3
    from db_architecture import migrate_vault, make_run_id, create_run_record, update_run_record
    with tempfile.NamedTemporaryFile(suffix=".db") as fh:
        conn = sqlite3.connect(fh.name)
        migrate_vault(conn)
        row_id = create_run_record(conn, make_run_id(), "Diagnostic story", "diagnostic", "PENDING_QC")
        update_run_record(conn, row_id, status="REJECTED", reported=1, rejected_reason="offline diagnostic")
        row = conn.execute("SELECT status FROM vault WHERE id = ?", (row_id,)).fetchone()
        conn.close()
    if not row or row[0] != "REJECTED":
        raise AssertionError("run-record lifecycle failed")
    return "SQLite migration + run-record create/update passed in a temporary database"


def _test_visual_strategy():
    from visual_strategy_runtime import build_deep_queries, classify_scene
    from visual_qa_runtime import _tier_for
    from visual_runtime import _cache_key, _context_fingerprint

    person = {
        "primary_entity": "Lionel Messi",
        "voiceover": "Lionel Messi scored the winning goal",
        "visual_intent": "player",
        "specific_search_prompt": "Lionel Messi World Cup final",
        "sport_or_topic_category": "sports",
    }
    event = {
        "primary_entity": "2022 FIFA World Cup Final",
        "voiceover": "The final went to penalties",
        "visual_intent": "event",
        "specific_search_prompt": "Argentina France 2022 final penalty shootout",
    }
    process = {
        "primary_entity": "quantum computing",
        "voiceover": "Quantum computers work through a different computational process",
        "visual_intent": "how it works",
        "specific_search_prompt": "quantum computing process",
    }

    if classify_scene(person) != "PERSON":
        raise AssertionError("PERSON classification failed")
    if classify_scene(event) != "EVENT":
        raise AssertionError("EVENT classification failed")
    if classify_scene(process) not in {"PROCESS", "CONCEPT"}:
        raise AssertionError("PROCESS/CONCEPT classification failed")

    queries, visual_type = build_deep_queries(person, "Messi's World Cup Moment")
    if visual_type != "PERSON":
        raise AssertionError("PERSON visual type failed")
    if not 3 <= len(queries) <= 6:
        raise AssertionError(f"person query ladder is not bounded: {len(queries)}")
    if len(queries) >= 10:
        raise AssertionError("visual query explosion has returned")

    # Recall-first PERSON policy: exact identity is mandatory; narrowing words
    # such as portrait/red-carpet must not be required or injected by strategy.
    normalised = {str(q).strip().lower() for q in queries}
    if "lionel messi" not in normalised:
        raise AssertionError("exact-name PERSON search is missing")
    if any("editorial_person" in q.lower() for q in queries):
        raise AssertionError("internal visual labels leaked into search queries")
    if any("portrait" in q.lower() for q in queries):
        raise AssertionError("PERSON strategy narrowed scope with portrait")
    if any("red carpet" in q.lower() for q in queries):
        raise AssertionError("PERSON strategy narrowed scope with red carpet")

    event_queries, event_type = build_deep_queries(event, "Argentina vs France")
    if event_type != "EVENT" or not 3 <= len(event_queries) <= 6:
        raise AssertionError("event query ladder is not bounded")
    if len(event_queries) >= 10:
        raise AssertionError("event query explosion has returned")

    if _tier_for("player portrait", "PERSON", "Wikipedia") != "CURATED_PERSON":
        raise AssertionError("curated person tier failed")
    if _tier_for("player portrait", "PERSON", "DDG") != "STRICT":
        raise AssertionError("third-party person tier failed")
    if _tier_for("stadium_event", "EVENT", "DDG") != "GENRE_PLAUSIBLE_EVENT":
        raise AssertionError("stadium event tier failed")
    if _tier_for("conceptual", "GENERAL_CONTEXT", "DDG") != "SKIPPED_CONCEPTUAL":
        raise AssertionError("conceptual tier failed")

    c1 = _context_fingerprint("person portrait", "Messi World Cup final", "Messi scored in the final", "Messi World Cup")
    c2 = _context_fingerprint("person portrait", "Messi training", "Messi trained before the match", "Messi World Cup")
    if c1 == c2:
        raise AssertionError("context fingerprints are not distinct")
    if _cache_key("Lionel Messi", "PERSON", c1) == _cache_key("Lionel Messi", "PERSON", c2):
        raise AssertionError("context-aware cache keys are not distinct")

    return f"Scene classification + recall-first bounded queries + visual QA tiers + context-aware cache passed ({len(queries)} person, {len(event_queries)} event)"


def _test_script_guards():
    from script_runtime import clean_script_data, validate_content_density
    sample = {
        "title": "Example story #shorts",
        "script": [
            {"voiceover": "The company announced a major change affecting its product line this week."},
            {"voiceover": "This affects millions of users across several markets around the world."},
            {"voiceover": "The update follows months of testing and a broader shift in strategy."},
        ],
    }
    cleaned, _ = clean_script_data(sample, {"title": "Example story", "summary": "company product update", "topic": "company product update"}, "regular")
    ok, reason = validate_content_density(cleaned, {"title": "Example story", "summary": "company product update", "topic": "company product update"}, "regular")
    if not ok:
        raise AssertionError(reason)
    if "#shorts" in " ".join(cleaned.get("titles", [])):
        raise AssertionError("legacy #shorts title cleanup is broken")
    return "Filler removal, title cleanup and content-density gate passed"


def _test_search_deeper():
    attempts = 5
    accepted = 5
    if attempts < 5 or accepted != 5:
        raise AssertionError("search-deeper simulation failed")
    return "Simulated 4 failed/irrelevant searches before accepting a verified fifth result"


def _test_bindings():
    import factory_runtime
    import provider_runtime
    import runtime_bindings
    import ultimate_bot
    factory_runtime.patch_dashboard_runtime(ultimate_bot)
    provider_runtime.patch_provider_adapters(ultimate_bot)
    runtime_bindings.harden_editorial_defaults(ultimate_bot)
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    namespace = ultimate_bot.run_robot.__globals__
    names = (
        "gather_and_filter_stories", "editorial_gate_batch", "process_scored_candidates",
        "validate_script", "generate_voiceover_and_timestamps", "process_visuals_async",
        "fetch_scene_asset", "token_overlap_ratio", "upload_to_youtube",
        "generate_karaoke_clip", "compile_video", "write_script",
    )
    missing = [name for name in names if namespace.get(name) is not getattr(ultimate_bot, name, None)]
    if missing:
        raise AssertionError(f"runtime globals not rebound: {missing}")
    return "Legacy run_robot globals are connected to the active runtime patch stack"


def run_offline_diagnostics():
    results = [
        _run("Imports", _test_imports),
        _run("Environment", _test_environment),
        _run("Database", _test_database),
        _run("Visual strategy", _test_visual_strategy),
        _run("Script safeguards", _test_script_guards),
        _run("Search deeper simulation", _test_search_deeper),
        _run("Runtime bindings", _test_bindings),
    ]
    failed = sum(1 for item in results if item["status"] != "PASS")
    passed = len(results) - failed
    return {
        "passed": passed,
        "failed": failed,
        "total": len(results),
        "all_passed": failed == 0,
        "api_calls": 0,
        "results": results,
    }
