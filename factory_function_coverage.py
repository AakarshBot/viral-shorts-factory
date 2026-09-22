"""Audit of the canonical dashboard's coverage of ultimate_bot callables.

This module is intentionally read-only: it inspects the source and reports where
each factory function is exposed. It does not replace or patch factory behavior.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

# Every top-level ultimate_bot function belongs to one of these user-facing
# surfaces or is deliberately an implementation helper. "Internal" is not a
# missing feature; it prevents low-level helpers from being turned into unsafe
# buttons while still making the coverage explicit.
SURFACE_MAP: dict[str, str] = {
    "global_exception_hook": "Internal",
    "_remote_mode_enabled": "Internal",
    "_load_remote_youtube_credentials": "Internal",
    "safe_cleanup": "Live Factory",
    "enforce_cache_ttl_hygiene": "Live Factory",
    "parse_groq_json_response": "Demo / Diagnostics",
    "init_db": "Live Factory",
    "safe_text": "Live Factory",
    "get_google_credentials": "Channel Statistics",
    "get_genre_bonuses": "Live Factory",
    "calculate_smart_score": "Live Factory",
    "get_smart_metrics": "Channel Statistics",
    "print_metric_recommendations": "Demo / Diagnostics",
    "auto_pilot_selection": "Live Factory",
    "fetch_trending_topics": "Live Factory",
    "manual_prompts": "Live Factory",
    "cricket_pipeline_prompts": "Live Factory",
    "run_analytics_sweep": "Channel Statistics",
    "gather_and_filter_stories": "Live Factory",
    "editorial_gate_batch": "Live Factory",
    "process_scored_candidates": "Live Factory",
    "get_insights_for_script": "Live Factory",
    "validate_script": "Live Factory",
    "self_critique_pass": "Live Factory",
    "write_script": "Live Factory",
    "generate_voiceover_and_timestamps": "Live Factory",
    "get_cached_asset": "Live Factory",
    "save_to_cache": "Live Factory",
    "fetch_hf_ai_image": "Live Factory",
    "get_image_hash": "Live Factory",
    "get_bold_font": "Live Factory",
    "draw_text_with_double_shadow": "Live Factory",
    "wrap_text_exact": "Live Factory",
    "fit_text_in_box": "Live Factory",
    "create_branded_slide": "Live Factory",
    "render_hook_card": "Live Factory",
    "generate_karaoke_clip": "Live Factory",
    "_scene_visual_segment_count": "Internal",
    "_caption_y_position": "Internal",
    "_normalize_audio_loudness": "Internal",
    "compile_video": "Live Factory",
    "upload_to_youtube": "Live Factory",
    "_report_youtube_upload_visibility": "Internal",
    "_ensure_youtube_public_visibility": "Internal",
    "font_preflight_check": "Live Factory",
    "run_robot": "Live Factory",
}

def _function_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]

def collect_factory_function_coverage(repo_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parent
    source = root / "ultimate_bot.py"
    names = _function_names(source)
    missing_map = [name for name in names if name not in SURFACE_MAP]
    stale_map = [name for name in SURFACE_MAP if name not in names]
    buckets: dict[str, list[str]] = {}
    for name in names:
        surface = SURFACE_MAP.get(name, "Unmapped")
        buckets.setdefault(surface, []).append(name)
    return {
        "total": len(names),
        "mapped": len(names) - len(missing_map),
        "unmapped": missing_map,
        "stale_map": stale_map,
        "by_surface": buckets,
        "complete": not missing_map and not stale_map,
    }

def coverage_summary(repo_root: str | Path | None = None) -> str:
    report = collect_factory_function_coverage(repo_root)
    return (
        f"{report['mapped']}/{report['total']} ultimate_bot functions are explicitly "
        f"accounted for across Live Factory, Channel Statistics, Demo / Diagnostics, "
        f"or deliberate Internal status."
    )

__all__ = ["SURFACE_MAP", "collect_factory_function_coverage", "coverage_summary"]
