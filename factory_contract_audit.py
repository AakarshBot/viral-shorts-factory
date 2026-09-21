"""Offline factory-wide contract audit for the supported Shorts architecture.

This audit checks the authoritative runtime surface and the repository layout so
obsolete dashboards, modal UI and historical patch scaffolding cannot silently
return during future maintenance.
"""
from __future__ import annotations

import ast
from pathlib import Path

REQUIRED_MODULES = (
    "ultimate_bot",
    "workflow_runtime",
    "research_runtime",
    "script_runtime",
    "script_guard_runtime",
    "quality_runtime",
    "visual_retrieval_planner",
    "visual_strategy_runtime",
    "visual_runtime",
    "visual_qa_runtime",
    "visual_content_runtime",
    "visual_retrieval_runtime",
    "visual_provider_boundary_runtime",
    "audio_runtime",
    "subtitle_runtime",
    "branding_runtime",
    "db_architecture",
    "db_runtime",
    "runtime_bindings",
    "runtime_hardener",
    "production_hardening_runtime",
    "final_qc_runtime",
)

OBSOLETE_REPOSITORY_ARTIFACTS = (
    "app_legacy.py",
    "app.py.mybackup",
    "newsroom_dashboard.py",
    "apply_production_fixes.py",
    "apply_script_fallback_fix.py",
    "apply_selected_story_lock_fix.py",
    "person_source_runtime.py",
    "visual_query_lock_runtime.py",
    "visual_replacement_runtime.py",
    "visual_resilience_runtime.py",
    "workflow_progress_runtime.py",
    "upload_runtime.py",
    "test_phase_runtime.py",
    "test_phase_patches.py",
    "test_history_runtime.py",
    "pipeline_integrity_loader.py",
    "diagnostic_visual_guard.py",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def source_syntax_audit() -> list[str]:
    errors: list[str] = []
    root = _repo_root()
    for path in root.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__", ".git"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{path.relative_to(root)}: {type(exc).__name__}: {exc}")
            continue
        names: dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names[node.name] = names.get(node.name, 0) + 1
        for name, count in sorted(names.items()):
            if count > 1:
                errors.append(f"{path.relative_to(root)}: duplicate top-level definition '{name}' ({count} times)")
    return errors


def runtime_surface_audit() -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_MODULES:
        try:
            module = __import__(name)
        except Exception as exc:
            errors.append(f"{name}: import failed ({type(exc).__name__}: {exc})")
            continue
        if name == "workflow_runtime":
            for attr in ("FORMAT_OPTIONS", "CRICKET_CATEGORIES", "MAX_DISCOVERY_CANDIDATES", "WorkflowController"):
                if not hasattr(module, attr):
                    errors.append(f"{name}: missing {attr}")
        elif name == "visual_provider_boundary_runtime":
            for attr in ("build_raw_source_plan", "fetch_wikipedia_person_candidates", "fetch_commons_candidates"):
                if not callable(getattr(module, attr, None)):
                    errors.append(f"{name}: missing callable {attr}")
        elif name == "visual_strategy_runtime":
            for attr in ("build_deep_queries", "classify_scene", "_clean", "_normalise", "_normalise_query"):
                if not callable(getattr(module, attr, None)):
                    errors.append(f"{name}: missing callable {attr}")
    return errors


def language_surface_audit() -> list[str]:
    errors: list[str] = []
    import ultimate_bot
    from unicode_runtime import install, unicode_words

    install()
    root = _repo_root()
    for key, config in ultimate_bot.LANGUAGES.items():
        if not config.get("voices"):
            errors.append(f"language {key}: no TTS voices configured")
        font = str(config.get("font") or "")
        if not font:
            errors.append(f"language {key}: no font configured")
        elif not (root / font).is_file() and not Path(font).is_file() and key in {"hindi", "telugu"}:
            errors.append(f"language {key}: configured font asset is missing: {font}")

    samples = {
        "hindi": "भारत ने आज नई नीति की घोषणा की",
        "telugu": "భారతదేశం ఈరోజు కొత్త విధానాన్ని ప్రకటించింది",
    }
    for key, sample in samples.items():
        if len(unicode_words(sample)) < 4:
            errors.append(f"language {key}: Unicode tokenizer lost source words")
    return errors


def dashboard_architecture_audit() -> list[str]:
    root = _repo_root()
    errors: list[str] = []
    app = root / "app.py"
    if not app.is_file():
        return ["app.py: canonical dashboard entrypoint is missing"]
    source = app.read_text(encoding="utf-8")
    forbidden_tokens = ("st.experimental_dialog", "newsroom_dashboard", "app_legacy", "runpy.run_module")
    for token in forbidden_tokens:
        if token in source:
            errors.append(f"app.py: obsolete dashboard token remains: {token}")
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
        errors.append("app.py: discovery topic-selection UI is missing")
    for artifact in OBSOLETE_REPOSITORY_ARTIFACTS:
        if (root / artifact).exists():
            errors.append(f"obsolete repository artifact remains: {artifact}")
    pages = root / "pages"
    if pages.exists():
        errors.append("obsolete Streamlit pages/ directory remains")
    return errors


def run_audit() -> list[str]:
    errors: list[str] = []
    errors.extend(source_syntax_audit())
    errors.extend(runtime_surface_audit())
    errors.extend(language_surface_audit())
    errors.extend(dashboard_architecture_audit())
    return errors


if __name__ == "__main__":
    findings = run_audit()
    if findings:
        print("FACTORY CONTRACT AUDIT: FAIL")
        for finding in findings:
            print(" - " + finding)
        raise SystemExit(1)
    print("FACTORY CONTRACT AUDIT: PASS")
