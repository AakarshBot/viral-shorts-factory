"""Offline factory-wide contract audit for the legacy Shorts architecture.

This is intentionally dependency-light. It checks source syntax, required runtime
entry points, multilingual text contracts, format/language coverage, and the
binding surfaces that historically failed after a patch was applied.
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
    "audio_runtime",
    "subtitle_runtime",
    "branding_runtime",
    "channel_branding_runtime",
    "db_architecture",
    "db_runtime",
    "runtime_bindings",
    "runtime_hardener",
    "production_hardening_runtime",
    "final_qc_runtime",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def source_syntax_audit() -> list[str]:
    """Return syntax/duplicate-top-level-definition defects in Python sources."""
    errors: list[str] = []
    for path in _repo_root().rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__", ".git"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{path.relative_to(_repo_root())}: {type(exc).__name__}: {exc}")
            continue

        names: dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names[node.name] = names.get(node.name, 0) + 1
        duplicate_names = sorted(name for name, count in names.items() if count > 1)
        for name in duplicate_names:
            errors.append(
                f"{path.relative_to(_repo_root())}: duplicate top-level definition '{name}' ({names[name]} times)"
            )
    return errors


def runtime_surface_audit() -> list[str]:
    """Check every authoritative runtime module can be imported and exposes its public surface."""
    errors: list[str] = []
    for name in REQUIRED_MODULES:
        try:
            module = __import__(name)
        except Exception as exc:
            errors.append(f"{name}: import failed ({type(exc).__name__}: {exc})")
            continue
        if name == "workflow_runtime":
            for attr in ("FORMAT_OPTIONS", "CRICKET_CATEGORIES", "WorkflowController", "discover_three_candidates"):
                if not hasattr(module, attr):
                    errors.append(f"{name}: missing {attr}")
        elif name == "research_runtime":
            for attr in ("patch_research_pipeline", "_openrouter_script_fallback", "_ollama_script_fallback"):
                if not callable(getattr(module, attr, None)):
                    errors.append(f"{name}: missing callable {attr}")
        elif name == "visual_strategy_runtime":
            for attr in ("build_deep_queries", "classify_scene", "_clean", "_normalise", "_normalise_query"):
                if not callable(getattr(module, attr, None)):
                    errors.append(f"{name}: missing callable {attr}")
    return errors


def language_surface_audit() -> list[str]:
    """Check configured languages have narration, font and Unicode-safe helper support."""
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
        elif not (root / font).is_file() and not Path(font).is_file():
            # English can use a system font, while Hindi/Telugu are repo assets.
            if key in {"hindi", "telugu"}:
                errors.append(f"language {key}: configured font asset is missing: {font}")

    multilingual_samples = {
        "hindi": "भारत ने आज नई नीति की घोषणा की",
        "telugu": "భారతదేశం ఈరోజు కొత్త విధానాన్ని ప్రకటించింది",
    }
    for key, sample in multilingual_samples.items():
        words = unicode_words(sample)
        if len(words) < 4:
            errors.append(f"language {key}: Unicode tokenizer lost source words: {words!r}")
    return errors


def visual_entity_fallback_audit() -> list[str]:
    """Ensure the repeat-limit compatibility layer never emits a blank entity."""
    errors: list[str] = []
    import unicode_runtime
    unicode_runtime.install()
    import visual_policy_runtime as policy

    builder = getattr(policy, "_subject_limit_wrapper", None)
    if not callable(builder):
        return ["visual policy: subject-limit wrapper is unavailable"]

    captured = []

    def original(bot, seg, category, used_urls, used_hashes, video_title=""):
        captured.append(dict(seg))
        return True

    wrapped = builder(original)
    bot = object()
    used_urls: set[str] = set()
    used_hashes: set[str] = set()
    scene = {
        "primary_entity": "BCCI",
        "visual_intent": "organization",
        "specific_search_prompt": "BCCI press conference",
    }
    for _ in range(3):
        wrapped(bot, scene, "sports", used_urls, used_hashes, "BCCI announces selection")

    if len(captured) != 3:
        errors.append(f"visual policy: repeat wrapper delegated {len(captured)} times; expected 3")
    if any(not str(item.get("primary_entity") or "").strip() for item in captured):
        errors.append("visual policy: repeat wrapper produced a blank primary_entity")
    return errors


def run_audit() -> list[str]:
    errors: list[str] = []
    errors.extend(source_syntax_audit())
    errors.extend(runtime_surface_audit())
    errors.extend(language_surface_audit())
    errors.extend(visual_entity_fallback_audit())
    return errors


if __name__ == "__main__":
    findings = run_audit()
    if findings:
        print("FACTORY CONTRACT AUDIT: FAIL")
        for finding in findings:
            print(" - " + finding)
        raise SystemExit(1)
    print("FACTORY CONTRACT AUDIT: PASS")
