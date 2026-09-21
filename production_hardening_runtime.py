"""Production hardening for scene-count integrity, visual entity integrity and live workflow progress."""
from __future__ import annotations

import inspect
import os
import re
from typing import Any



def _resolve_story_visual_entity(story_data: dict[str, Any], candidate: str) -> str:
    """Resolve a candidate visual entity against the actual story headline.

    The emergency script fallback used to select the first capitalised token,
    which turned headlines such as "Sri Lanka name squads..." into "Sri".
    Prefer a known multi-word location/organisation present in the headline,
    then preserve a legitimate multi-word candidate.
    """
    title = re.sub(r"\s+", " ", str((story_data or {}).get("title") or (story_data or {}).get("topic") or "")).strip()
    value = re.sub(r"\s+", " ", str(candidate or "")).strip(" ,.-:;|\"'")
    if not title:
        return value

    try:
        from visual_strategy_runtime import LOCATION_NAMES, ORGANIZATION_ACRONYMS
        known_terms = sorted(
            set(str(x) for x in LOCATION_NAMES) | set(str(x) for x in ORGANIZATION_ACRONYMS),
            key=lambda x: (-len(x.split()), -len(x)),
        )
    except Exception:
        known_terms = []

    title_lower = title.lower()
    for term in known_terms:
        if re.search(r"(?<![A-Za-z])" + re.escape(term.lower()) + r"(?![A-Za-z])", title_lower):
            return term

    # Preserve a useful multi-word candidate rather than reducing it to its
    # first token. Single-token candidates remain valid when no stronger
    # headline-grounded entity is available.
    if len(value.split()) >= 2:
        return value

    # A two-to-four token capitalised span in the headline is safer than the
    # first token alone. Stop at obvious sentence/function words.
    words = re.findall(r"[A-Za-z][A-Za-z'/-]*", title)
    stop = {
        "name", "names", "announce", "announces", "announced", "squad", "squads", "for", "against",
        "into", "from", "with", "after", "before", "and", "the", "to", "by", "beat", "beats",
        "enter", "enters", "win", "wins", "won", "will", "have", "has", "had", "on", "in",
    }
    for index, word in enumerate(words):
        if word.lower() == value.lower() and word[:1].isupper():
            span = [word]
            for nxt in words[index + 1:index + 4]:
                if nxt.lower() in stop or not nxt[:1].isupper():
                    break
                span.append(nxt)
            if len(span) >= 2:
                return " ".join(span)
    return value


def _repair_visual_identity(script_data: dict[str, Any], story_data: dict[str, Any]) -> dict[str, Any]:
    """Repair obviously truncated scene entities before visual sourcing begins."""
    if not isinstance(script_data, dict) or not isinstance(script_data.get("script"), list):
        return script_data
    repaired = dict(script_data)
    scenes = []
    changed = 0
    for scene in repaired.get("script") or []:
        if not isinstance(scene, dict):
            scenes.append(scene)
            continue
        copy = dict(scene)
        current = str(copy.get("primary_entity") or "").strip()
        resolved = _resolve_story_visual_entity(story_data, current)
        if resolved and resolved != current:
            copy["primary_entity"] = resolved
            prompt = str(copy.get("specific_search_prompt") or "").strip()
            if not prompt or prompt.lower() == current.lower():
                copy["specific_search_prompt"] = resolved
            changed += 1
        scenes.append(copy)
    repaired["script"] = scenes
    if changed:
        print(f"   [Visual Entity Hardening] Resolved {changed} scene entity label(s) against the story headline.", flush=True)
    return repaired


def _install_authoritative_visual_query_planner() -> None:
    """Verify that the canonical visual planner is still installed."""
    try:
        import visual_strategy_runtime
        current = getattr(visual_strategy_runtime, "build_deep_queries", None)
        if not getattr(current, "_authoritative_locked_subject_planner", False):
            raise RuntimeError("authoritative visual query planner is not installed")
        print(
            "   [Visual Strategy Hardening] Strict single-query visual planner preserved.",
            flush=True,
        )
    except Exception as exc:
        print(
            f"   [Visual Strategy Hardening] Strict planner check failed: {type(exc).__name__}: {exc}",
            flush=True,
        )


def _enrich_emergency_story(bot, story_data: dict[str, Any]) -> dict[str, Any]:
    """Give the deterministic fallback enough real evidence to form distinct scenes."""
    enriched = dict(story_data or {})
    existing_text = str(
        enriched.get("text")
        or enriched.get("summary")
        or enriched.get("description")
        or ""
    ).strip()

    snippets: list[str] = []

    # Phase 2 already built the authoritative evidence pack for this story.
    # Reuse its verified claims/source previews instead of calling the removed
    # legacy collect_source_bundle() API and performing a second discovery pass.
    pack = enriched.get("research_evidence_pack")
    if isinstance(pack, dict):
        for claim in pack.get("claims") or []:
            if not isinstance(claim, dict) or str(claim.get("status") or "").lower() == "conflicted":
                continue
            text = re.sub(r"\s+", " ", str(claim.get("text") or "")).strip()
            if text:
                snippets.append(text)
        for source in pack.get("sources") or []:
            if not isinstance(source, dict):
                continue
            text = re.sub(r"\s+", " ", str(source.get("clean_text_preview") or "")).strip()
            if text:
                snippets.append(text)

    if snippets:
        evidence_text = " ".join(dict.fromkeys(snippets))
        if existing_text:
            enriched["text"] = f"{existing_text} {evidence_text}".strip()
        else:
            enriched["text"] = evidence_text

    return enriched


def _ensure_script_ready(bot, result: dict[str, Any], story_data: dict[str, Any], language_cfg: dict[str, Any], genre_key: str, format_mode: str) -> dict[str, Any]:
    """Require semantic narrative completeness before rendering; no scene/word quotas."""
    from script_runtime import assess_narrative_completeness, clean_script_data, validate_content_density, _extractive_script_fallback

    result = _repair_visual_identity(result, story_data)
    cleaned, _diag = clean_script_data(result, story_data, format_mode)
    valid, reason = validate_content_density(cleaned, story_data, format_mode)
    if valid:
        return cleaned

    assessment = assess_narrative_completeness(cleaned)
    print(
        f"   [Script Hardening] Narrative completeness failed: {reason}. "
        f"Roles={assessment.get('roles', {})}. Trying source-grounded fallback.",
        flush=True,
    )

    repair_story = _enrich_emergency_story(bot, story_data)
    fallback = _extractive_script_fallback(repair_story, language_cfg, genre_key, format_mode)
    fallback = _repair_visual_identity(fallback, repair_story)
    fallback, _fallback_diag = clean_script_data(fallback, repair_story, format_mode)
    valid, fallback_reason = validate_content_density(fallback, repair_story, format_mode)
    if not valid:
        raise ValueError(f"Production script rejected after semantic fallback: {fallback_reason}")

    fallback["fallback_reason"] = "narrative_completeness"
    fallback["fallback_source_enrichment"] = bool(repair_story.get("research_sources"))
    fallback["public_publish_blocked"] = True
    return fallback



def _patch_script_pipeline(bot) -> None:
    """Verify the canonical script router instead of adding another wrapper."""
    current = getattr(bot, "write_script", None)
    if callable(current) and getattr(current, "_canonical_script_pipeline", False):
        return
    raise RuntimeError(
        "Canonical script router is not installed; refusing to add a legacy script wrapper."
    )

def install_production_wrappers(controller) -> None:
    """Install the canonical live workflow wrappers for one controller."""
    if controller._patched:
        return
    run_robot = getattr(controller.bot, "run_robot", None)
    if run_robot is None:
        raise RuntimeError("Legacy run_robot() is not available.")
    globals_dict = getattr(run_robot, "__globals__", {})

    original_write = globals_dict.get("write_script")
    if callable(original_write):
        def write_wrapper(*args, **kwargs):
            controller._reporter("script", 28, "Writing and checking the selected story…")
            result = original_write(*args, **kwargs)
            if isinstance(result, dict):
                with controller._lock:
                    controller.state.script_data = result
                count = len(result.get("script") or [])
                controller._reporter("script", 38, f"Script complete — {count} scenes passed the production contract.")
            return result
        globals_dict["write_script"] = write_wrapper

    original_audio = globals_dict.get("generate_voiceover_and_timestamps")
    if callable(original_audio):
        async def audio_wrapper(*args, **kwargs):
            controller._reporter("audio", 42, "Generating narration and word timings…")
            result = await original_audio(*args, **kwargs) if inspect.iscoroutinefunction(original_audio) else original_audio(*args, **kwargs)
            audio_paths = result[0] if isinstance(result, tuple) and result else []
            controller._reporter("audio", 53, f"Narration complete — {len(audio_paths) if isinstance(audio_paths, list) else 0} scene audio files ready.")
            return result
        globals_dict["generate_voiceover_and_timestamps"] = audio_wrapper

    original_visuals = globals_dict.get("process_visuals_async")
    if callable(original_visuals):
        async def visuals_wrapper(*args, **kwargs):
            script_data = args[0] if args else kwargs.get("script_data") or {}
            total = len(script_data.get("script") or []) if isinstance(script_data, dict) else 0
            controller._reporter("visuals", 56, f"Sourcing and verifying visuals for {total} scenes…")
            result = await original_visuals(*args, **kwargs) if inspect.iscoroutinefunction(original_visuals) else original_visuals(*args, **kwargs)
            controller._reporter("visuals", 75, f"Visual package complete — {len(result) if isinstance(result, list) else 0} scene packages ready.")
            return result
        globals_dict["process_visuals_async"] = visuals_wrapper

    original_compile = globals_dict.get("compile_video")
    if callable(original_compile):
        def compile_wrapper(*args, **kwargs):
            controller._reporter("render", 78, "Rendering motion, word-highlight captions and branding…")
            result = original_compile(*args, **kwargs)
            controller._reporter("render", 94, "Video rendered and loudness normalized. Preparing final QC…")
            if isinstance(result, str) and os.path.isfile(result):
                with controller._lock:
                    controller.state.video_path = result
            return result
        globals_dict["compile_video"] = compile_wrapper

    real_upload = getattr(controller.bot, "upload_to_youtube", None)
    if callable(real_upload):
        controller._real_uploader = real_upload
        def production_blocked_upload(*args, **kwargs):
            controller._reporter("qc", 98, "Video ready. Waiting for your final QC and upload decision.")
            print("   [Workflow] Automatic upload blocked. Manual QC is required.", flush=True)
            return "PENDING_MANUAL_UPLOAD"
        globals_dict["upload_to_youtube"] = production_blocked_upload

    controller._patched = True

def install_production_hardening(bot) -> None:
    _install_authoritative_visual_query_planner()
    _patch_script_pipeline(bot)
    # Progress wrappers are installed by WorkflowController when production starts.
