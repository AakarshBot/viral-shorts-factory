"""Production hardening for scene-count integrity, visual entity integrity and live workflow progress."""
from __future__ import annotations

import inspect
import os
import re
from typing import Any


_MIN_SCENES = {"regular": 5, "trending": 5, "tech_reviews": 5, "top5": 7, "cricket": 5}
_MAX_SCENES = {"regular": 8, "trending": 8, "tech_reviews": 8, "top5": 7, "cricket": 8}


def _scene_count(format_mode: str) -> tuple[int, int]:
    mode = str(format_mode or "regular").lower()
    return _MIN_SCENES.get(mode, 5), _MAX_SCENES.get(mode, 8)


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
        if not getattr(current, "_authoritative_bounded_query_planner", False):
            raise RuntimeError("authoritative visual query planner is not installed")
        print(
            "   [Visual Strategy Hardening] Canonical bounded visual query planner preserved.",
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

    sources = enriched.get("research_sources")
    snippets: list[str] = []
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, dict):
                snippet = re.sub(r"\s+", " ", str(source.get("snippet") or "")).strip()
                if len(snippet.split()) >= 5:
                    snippets.append(snippet)

    if not snippets:
        try:
            from research_runtime import collect_source_bundle
            collected = collect_source_bundle(bot, enriched, max_sources=5)
            for source in collected:
                if isinstance(source, dict):
                    snippet = re.sub(r"\s+", " ", str(source.get("snippet") or "")).strip()
                    if len(snippet.split()) >= 5:
                        snippets.append(snippet)
            if collected:
                enriched["research_sources"] = collected
        except Exception as exc:
            print(
                f"   [Script Hardening] Emergency research enrichment unavailable: {type(exc).__name__}: {exc}",
                flush=True,
            )

    if snippets:
        evidence_text = " ".join(dict.fromkeys(snippets))
        if existing_text:
            enriched["text"] = f"{existing_text} {evidence_text}".strip()
        else:
            enriched["text"] = evidence_text

    return enriched


def _repair_scene_count(bot, result: dict[str, Any], story_data: dict[str, Any], language_cfg: dict[str, Any], genre_key: str, format_mode: str) -> dict[str, Any]:
    """Never let a cleaned script reach rendering below the production minimum."""
    minimum, maximum = _scene_count(format_mode)
    result = _repair_visual_identity(result, story_data)
    scenes = result.get("script") if isinstance(result, dict) else None
    if isinstance(scenes, list) and minimum <= len(scenes) <= maximum:
        return result

    print(
        f"   [Script Hardening] Scene contract failed: got {len(scenes) if isinstance(scenes, list) else 0}; "
        f"required {minimum}-{maximum}. Rebuilding from source-grounded fallback.",
        flush=True,
    )
    try:
        from script_runtime import _extractive_script_fallback, clean_script_data, validate_content_density

        repair_story = _enrich_emergency_story(bot, story_data)
        fallback = _extractive_script_fallback(repair_story, language_cfg, genre_key, format_mode)
        fallback = _repair_visual_identity(fallback, repair_story)
        fallback, _diag = clean_script_data(fallback, repair_story, format_mode)
        ok, reason = validate_content_density(fallback, story_data, format_mode)
        if not ok:
            raise ValueError(reason)
        fallback_scenes = list(fallback.get("script") or [])
        if len(fallback_scenes) >= minimum:
            if len(fallback_scenes) > maximum:
                fallback_scenes = fallback_scenes[:maximum]
            fallback["script"] = fallback_scenes
            fallback["fallback_reason"] = "scene_count_contract"
            fallback["fallback_source_enrichment"] = bool(repair_story.get("research_sources"))
            return fallback
    except Exception as exc:
        print(f"   [Script Hardening] Source-grounded repair failed: {type(exc).__name__}: {exc}", flush=True)

    raise ValueError(
        f"Production script rejected: {len(scenes) if isinstance(scenes, list) else 0} scenes after cleanup; "
        f"required {minimum}-{maximum}. Refusing to render a one-scene Short."
    )


def _patch_script_pipeline(bot) -> None:
    current = getattr(bot, "write_script", None)
    if not callable(current) or getattr(current, "_scene_contract_bound", False):
        return
    run_robot = getattr(bot, "run_robot", None)
    globals_dict = getattr(run_robot, "__globals__", {}) if run_robot is not None else {}

    def guarded_write(story_data, language_cfg, genre_key, conn, format_mode):
        result = current(story_data, language_cfg, genre_key, conn, format_mode)
        if not isinstance(result, dict):
            raise ValueError("Script generation returned no usable dictionary.")
        repaired = _repair_scene_count(bot, result, story_data, language_cfg, genre_key, format_mode)
        print(f"   [Script Hardening] Final scene count: {len(repaired.get('script') or [])}", flush=True)
        return repaired

    guarded_write._scene_contract_bound = True
    guarded_write._content_dense_bound = bool(getattr(current, "_content_dense_bound", False))
    guarded_write._research_layer_live = bool(getattr(current, "_research_layer_live", False))
    guarded_write._scene_contract_inner_writer = current
    bot.write_script = guarded_write
    if globals_dict:
        globals_dict["write_script"] = guarded_write


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
            controller._reporter("render", 78, "Rendering scenes, subtitles and branding…")
            result = original_compile(*args, **kwargs)
            controller._reporter("render", 94, "Final video rendered. Preparing final QC…")
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
