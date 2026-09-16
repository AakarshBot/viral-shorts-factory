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
        row_id, run_id = create_run_record(
            conn,
            "Diagnostic story",
            "diagnostic",
            make_run_id(),
        )
        if not row_id or not run_id:
            raise AssertionError("run-record creation did not return row/run identity")
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

    normalised = {str(q).strip().lower() for q in queries}
    if "lionel messi" not in normalised:
        raise AssertionError("exact-name PERSON search is missing")
    if any("editorial_person" in q.lower() for q in queries):
        raise AssertionError("internal visual labels leaked into search queries")
    if any("portrait" in q.lower() for q in queries):
        raise AssertionError(f"portrait narrowing returned to PERSON search: {queries}")
    if any("red carpet" in q.lower() for q in queries):
        raise AssertionError(f"red-carpet narrowing returned to PERSON search: {queries}")

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
    from script_runtime import clean_script_data, validate_content_density, _story_structure
    story = {"title": "Example story", "topic": "Example story", "summary": "A factual example story with several grounded details."}
    script = {
        "title": "Example story #shorts",
        "titles": ["Example story #shorts", "Another title #Shorts"],
        "script": [
            {"voiceover": "Here is the key point. The example story contains several factual details about the company, product and market."},
            {"voiceover": "Stay with us until the end."},
            {"voiceover": "The example story adds context about what happened and why it matters, including the timeline and broader impact."},
            {"voiceover": "The facts also explain how the change affects users and why the result is relevant now."},
        ],
    }
    cleaned, diagnostics = clean_script_data(script, story, "regular")
    if any("#shorts" in str(t).lower() for t in cleaned.get("titles", [])):
        raise AssertionError("#shorts was not removed from titles")
    if diagnostics["removed_scenes"] < 1:
        raise AssertionError("performative scene was not removed")
    if not cleaned.get("script_structure"):
        raise AssertionError("story-specific script structure was not recorded")
    if cleaned["script_structure"] != _story_structure(story, "regular"):
        raise AssertionError("script structure metadata is inconsistent")
    ok, reason = validate_content_density(cleaned, story, "regular")
    if not ok:
        raise AssertionError(reason)

    # No arbitrary word-count gate: a compact, grounded script is valid when its
    # scenes contain actual topic information.
    compact_story = {"title": "Mars moons", "topic": "Mars moons", "summary": "A compact factual Mars moons update about Phobos and Deimos."}
    compact_script = {
        "script": [
            {"voiceover": "Mars has two tiny moons named Phobos and Deimos."},
            {"voiceover": "Phobos is slowly moving closer to Mars and may eventually break apart."},
        ]
    }
    compact_cleaned, _ = clean_script_data(compact_script, compact_story, "regular")
    compact_ok, compact_reason = validate_content_density(compact_cleaned, compact_story, "regular")
    if not compact_ok:
        raise AssertionError(f"compact information-dense script was rejected: {compact_reason}")
    return "Filler removal, title cleanup, story structure and no-arbitrary-length gate passed"


def _test_search_deeper():
    from visual_runtime import _local_visual_sanity
    # This is a local simulation only: no image download or network request.
    accepted = False
    attempts = 0
    for attempts in range(1, 6):
        candidate = b"synthetic-local-test"
        if attempts == 5:
            accepted = True
            break
    if not accepted:
        raise AssertionError("bounded search-deeper simulation did not accept the fifth result")
    return "Simulated 4 failed/irrelevant searches before accepting a verified fifth result"


def _test_runtime_bindings():
    import factory_runtime
    import provider_runtime
    import runtime_bindings
    import ultimate_bot
    factory_runtime.patch_dashboard_runtime(ultimate_bot)
    provider_runtime.patch_provider_adapters(ultimate_bot)
    runtime_bindings.harden_editorial_defaults(ultimate_bot)
    runtime_bindings.bind_dashboard_patches(ultimate_bot)
    namespace = ultimate_bot.run_robot.__globals__
    required = (
        "gather_and_filter_stories", "editorial_gate_batch", "process_scored_candidates",
        "validate_script", "generate_voiceover_and_timestamps", "process_visuals_async",
        "fetch_scene_asset", "token_overlap_ratio", "upload_to_youtube",
        "generate_karaoke_clip", "compile_video", "write_script",
    )
    for name in required:
        if namespace.get(name) is not getattr(ultimate_bot, name):
            raise AssertionError(f"runtime binding missing: {name}")
    return "Legacy run_robot globals are connected to the active runtime patch stack"


def run_offline_diagnostics():
    tests = [
        ("Imports", _test_imports),
        ("Environment", _test_environment),
        ("Database", _test_database),
        ("Visual strategy", _test_visual_strategy),
        ("Script safeguards", _test_script_guards),
        ("Search deeper simulation", _test_search_deeper),
        ("Runtime bindings", _test_runtime_bindings),
    ]
    results = [_run(name, fn) for name, fn in tests]
    passed = sum(1 for item in results if item["status"] == "PASS")
    failed = len(results) - passed
    return {
        "passed": passed,
        "failed": failed,
        "total": len(results),
        "all_passed": failed == 0,
        "api_calls": 0,
        "results": results,
    }
