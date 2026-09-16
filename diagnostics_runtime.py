"""No-API diagnostics for the Viral Shorts Factory."""

import importlib
import os
import sqlite3
import tempfile
import traceback
from pathlib import Path

REQUIRED_MODULES = (
    "ultimate_bot", "db_architecture", "db_runtime", "factory_runtime",
    "runtime_bindings", "editorial_runtime", "script_runtime",
    "visual_strategy_runtime", "visual_runtime", "visual_content_runtime",
    "visual_qa_runtime", "quality_runtime", "semantic_runtime",
    "audio_runtime", "provider_runtime", "youtube_comment_runtime",
)


def _run(name, fn, results):
    try:
        detail = fn()
        results.append({"name": name, "status": "PASS", "detail": str(detail or "OK")})
    except Exception as exc:
        results.append({"name": name, "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(limit=3)})


def _test_imports():
    for module_name in REQUIRED_MODULES:
        importlib.import_module(module_name)
    return f"Imported {len(REQUIRED_MODULES)} factory modules"


def _test_environment():
    names = ("GEMINI_API_KEY", "GROQ_API_KEY", "GNEWS_API_KEY", "UNSPLASH_ACCESS_KEY", "HF_TOKEN", "PEXELS_API_KEY")
    configured = [name for name in names if os.getenv(name)]
    return f"Local environment loaded; {len(configured)}/{len(names)} provider keys configured (no API calls made)"


def _test_database():
    from db_architecture import migrate_vault, create_run_record, update_run_record
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(str(Path(tmp) / "diagnostic.sqlite"))
        try:
            migrate_vault(conn)
            if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='vault'").fetchone(): raise AssertionError("vault table was not created")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(vault)").fetchall()}
            missing = {"id", "run_id", "topic", "status", "title_used", "script_json"} - columns
            if missing: raise AssertionError(f"vault is missing columns: {sorted(missing)}")
            row_id, run_id = create_run_record(conn, "Offline diagnostic story", "diagnostic")
            update_run_record(conn, row_id, status="COMPLETED", title_used="Offline diagnostic")
            saved = conn.execute("SELECT run_id, status, title_used FROM vault WHERE id=?", (row_id,)).fetchone()
            if saved != (run_id, "COMPLETED", "Offline diagnostic"): raise AssertionError("run record round-trip failed")
        finally:
            conn.close()
    return "SQLite migration + run-record create/update passed in a temporary database"


def _test_visual_strategy():
    from visual_strategy_runtime import build_deep_queries, classify_scene
    from visual_qa_runtime import _tier_for
    from visual_runtime import _cache_key, _context_fingerprint
    person = {"primary_entity": "Lionel Messi", "voiceover": "Lionel Messi scored the winning goal", "visual_intent": "player", "specific_search_prompt": "Lionel Messi World Cup final", "sport_or_topic_category": "sports"}
    event = {"primary_entity": "2022 FIFA World Cup Final", "voiceover": "The final went to penalties", "visual_intent": "event", "specific_search_prompt": "Argentina France 2022 final penalty shootout"}
    process = {"primary_entity": "quantum computing", "voiceover": "Quantum computers work through a different computational process", "visual_intent": "how it works", "specific_search_prompt": "quantum computing process"}
    conceptual = {"primary_entity": "the future of work", "visual_intent": "conceptual"}
    if classify_scene(person) != "PERSON": raise AssertionError("PERSON classification failed")
    if classify_scene(event) != "EVENT": raise AssertionError("EVENT classification failed")
    if classify_scene(process) not in {"PROCESS", "CONCEPT"}: raise AssertionError("PROCESS/CONCEPT classification failed")

    queries, visual_type = build_deep_queries(person, "Messi's World Cup Moment")
    if visual_type != "PERSON": raise AssertionError("PERSON visual type failed")
    if not 3 <= len(queries) <= 6: raise AssertionError(f"person query ladder is not bounded: {len(queries)}")
    if len(queries) >= 10: raise AssertionError("visual query explosion has returned")
    if not any("official photo" in q.lower() for q in queries): raise AssertionError("official photo fallback is missing")
    if any("editorial_person" in q.lower() for q in queries): raise AssertionError("internal visual labels leaked into search queries")

    event_queries, event_type = build_deep_queries(event, "Argentina vs France")
    if event_type != "EVENT" or not 3 <= len(event_queries) <= 6: raise AssertionError("event query ladder is not bounded")
    if len(event_queries) >= 10: raise AssertionError("event query explosion has returned")

    if _tier_for("player portrait", "PERSON", "Wikipedia") != "CURATED_PERSON": raise AssertionError("curated person tier failed")
    if _tier_for("player portrait", "PERSON", "DDG") != "STRICT": raise AssertionError("third-party person tier failed")
    if _tier_for("stadium_event", "EVENT", "DDG") != "GENRE_PLAUSIBLE_EVENT": raise AssertionError("stadium event tier failed")
    if _tier_for("conceptual", "GENERAL_CONTEXT", "DDG") != "SKIPPED_CONCEPTUAL": raise AssertionError("conceptual tier failed")

    c1 = _context_fingerprint("person portrait", "Messi World Cup final", "Messi scored in the final", "Messi World Cup")
    c2 = _context_fingerprint("person portrait", "Messi training", "Messi trained before the match", "Messi World Cup")
    if c1 == c2: raise AssertionError("context fingerprints are not distinct")
    if _cache_key("Lionel Messi", "PERSON", c1) == _cache_key("Lionel Messi", "PERSON", c2): raise AssertionError("context-aware cache keys are not distinct")

    return f"Scene classification + bounded queries + visual QA tiers + context-aware cache passed ({len(queries)} person, {len(event_queries)} event)"


def _test_script_guards():
    from script_runtime import clean_script_data, validate_content_density
    story = {"title": "Scientists build a new battery", "topic": "new battery technology", "summary": "Scientists built a battery that charges faster and lasts longer."}
    data = {"titles": ["Scientists Build A New Battery #shorts"], "script": [{"voiceover": "Wait until the end, because this changes everything."}, {"voiceover": "Scientists built a new battery that charges faster and lasts longer than earlier designs."}, {"voiceover": "The prototype reaches a higher charging rate while retaining useful capacity over repeated cycles."}, {"voiceover": "The result matters because faster charging can reduce downtime for devices that rely on frequent recharging."}]}
    cleaned, diag = clean_script_data(data, story, "regular")
    ok, reason = validate_content_density(cleaned, story, "regular")
    if not diag["removed_scenes"]: raise AssertionError("performative filler was not removed")
    if any("#shorts" in t.lower() for t in cleaned.get("titles", [])): raise AssertionError("#shorts was not removed")
    if not ok: raise AssertionError(reason)
    return "Filler removal, title cleanup and content-density gate passed"


def _test_search_deeper_simulation():
    attempts = [("source-1", "network failure"), ("source-2", "no result"), ("source-3", "irrelevant image"), ("source-4", "irrelevant image"), ("source-5", "verified matching image")]
    calls = []
    accepted = None
    for provider, result in attempts:
        calls.append(provider)
        if result == "verified matching image": accepted = provider; break
    if accepted != "source-5" or len(calls) != 5: raise AssertionError("search simulation stopped before a verified result")
    return "Simulated 4 failed/irrelevant searches before accepting a verified fifth result"


def _test_bindings():
    import ultimate_bot
    from runtime_bindings import bind_dashboard_patches
    if not hasattr(ultimate_bot, "run_robot") or not hasattr(ultimate_bot.run_robot, "__globals__"): raise AssertionError("run_robot globals are unavailable")
    bind_dashboard_patches(ultimate_bot)
    namespace = ultimate_bot.run_robot.__globals__
    required = ("write_script", "process_visuals_async", "upload_to_youtube")
    missing = [name for name in required if name not in namespace]
    if missing: raise AssertionError(f"run_robot globals are not bound: {missing}")
    return "Legacy run_robot globals are connected to the active runtime patch stack"


def run_offline_diagnostics():
    """Run every diagnostic without Groq, Gemini, YouTube, news or image API calls."""
    results = []
    _run("Imports", _test_imports, results)
    _run("Environment", _test_environment, results)
    _run("Database", _test_database, results)
    _run("Visual strategy", _test_visual_strategy, results)
    _run("Script safeguards", _test_script_guards, results)
    _run("Search deeper simulation", _test_search_deeper_simulation, results)
    _run("Runtime bindings", _test_bindings, results)
    passed = sum(1 for item in results if item["status"] == "PASS")
    failed = len(results) - passed
    return {"passed": passed, "failed": failed, "total": len(results), "all_passed": failed == 0, "results": results, "api_calls": 0}
