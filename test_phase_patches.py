"""Test-only diagnostics layered onto the step-isolated Test Phase.

Production functions are not changed here. The Test Phase gets:
- visible progress bars around long-running checks;
- an offline source-grounded script test with zero model/API calls;
- three planned visual search terms for every slide, while executing exactly one
  real visual fetch for one randomly selected slide (3 or 4 when available).
"""
from __future__ import annotations

import os
import random
import re
from typing import Any, Callable

import streamlit as st

import script_guard_runtime
import test_phase_runtime
from newsroom_dashboard import _collect_visual_items, _script_text


def _run_with_progress(label: str, fn: Callable[[], Any]) -> Any:
    """Show a simple diagnostic progress bar while executing a real test."""
    st.caption(f"⏳ {label}")
    bar = st.progress(0, text=f"{label}: starting")
    bar.progress(0.18, text=f"{label}: preparing")
    try:
        bar.progress(0.40, text=f"{label}: running")
        result = fn()
        bar.progress(0.82, text=f"{label}: checking result")
        bar.progress(1.0, text=f"{label}: complete")
        return result
    except Exception:
        bar.progress(1.0, text=f"{label}: failed")
        raise


def _wrap_for_progress(owner: Any, attr: str, label: str):
    """Temporarily wrap a callable so the Test Phase shows real run progress."""
    original = getattr(owner, attr)
    if not callable(original):
        return lambda: None
    state = {"restored": False}

    def wrapped(*args, **kwargs):
        try:
            return _run_with_progress(label, lambda: original(*args, **kwargs))
        finally:
            if not state["restored"]:
                try:
                    setattr(owner, attr, original)
                except Exception:
                    pass
                state["restored"] = True

    setattr(owner, attr, wrapped)
    return lambda: setattr(owner, attr, original)


def _clean_subject(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,.-:;|'\"")
    text = re.sub(r"^(?:primary entity|visual subject|subject|search term|keyword)\s*[:=-]\s*", "", text, flags=re.I)
    text = re.sub(r"\s+(?:official(?:\s+photo)?|press\s+photo|editorial\s+photo|news\s+photo|real\s+photo|photo|image)\s*$", "", text, flags=re.I)
    return text.strip(" ,.-:;|'\"")


def _scene_phrase(seg: dict[str, Any]) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", str(seg.get("voiceover", "")))
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "into", "after", "before", "about",
        "they", "their", "there", "here", "when", "what", "which", "where", "while", "have", "has",
        "had", "will", "would", "could", "should", "just", "been", "were", "was", "are", "our",
        "you", "your", "today", "is", "a", "an", "to", "of", "in", "on", "as", "it", "its",
    }
    return " ".join(w for w in words if w.lower() not in stop)[:180].strip()


def _three_visual_terms(seg: dict[str, Any], video_title: str) -> list[str]:
    """Produce exactly three clean diagnostic search terms without calling an API."""
    entity = _clean_subject(seg.get("primary_entity", ""))
    phrase = _scene_phrase(seg)
    title = _clean_subject(video_title)
    intent = _clean_subject(seg.get("visual_intent", ""))
    candidates = [
        entity,
        f"{entity} {phrase}" if entity and phrase else phrase,
        f"{entity} {intent}" if entity and intent else title,
        title,
    ]
    terms: list[str] = []
    for value in candidates:
        clean = re.sub(r"\s+", " ", str(value or "")).strip(" ,.-")
        if clean and clean.lower() not in {t.lower() for t in terms}:
            terms.append(clean)
    while terms and len(terms) < 3:
        terms.append(terms[-1])
    return terms[:3] if terms else ["selected story", "news", "documentary"]


def _offline_script_test(bot) -> None:
    """Build the same source-grounded emergency script path without any API call."""
    with st.expander("2. Script writing — offline / no-API test", expanded=True):
        st.info("This test deliberately makes **zero AI/model API calls**. It exercises the factory's source-grounded fallback and narration guard.")
        title = st.text_input("Story title", key="tp_offline_story_title", placeholder="Example: RBI changes liquidity rules")
        topic = st.text_input("Topic", key="tp_offline_story_topic", placeholder="Example: Reserve Bank of India policy update")
        summary = st.text_area("Source/article text", key="tp_offline_story_summary", height=180, placeholder="Paste the article/source text to test script extraction.")
        url = st.text_input("Article URL (optional)", key="tp_offline_story_url")
        if st.button("▶ Run offline script test", key="tp_run_script_offline", type="primary", use_container_width=True):
            if not st.session_state.tp_config:
                st.warning("Select the Test Phase format/language/category first.")
                return
            config = st.session_state.tp_config
            story = {
                "title": title.strip(),
                "topic": topic.strip(),
                "summary": summary.strip(),
                "text": summary.strip(),
                "url": url.strip(),
                "source": "Offline diagnostic input",
            }
            if not (story["title"] or story["topic"] or story["text"]):
                st.error("Provide a title, topic, or source/article text first.")
                return

            def run():
                return script_guard_runtime.source_only_fallback(
                    story,
                    bot.LANGUAGES[config["language"]],
                    config["category"],
                    config["format_mode"],
                )

            st.session_state.tp_script = _run_with_progress("Offline script test", run)
            st.session_state.tp_story = story
            st.success("Offline script test completed with zero API calls.")

        script = st.session_state.get("tp_script")
        if script:
            st.text_area("Narration preview", _script_text(script), height=320, disabled=True, key="tp_script_offline_preview")
            with st.expander("Offline script object", expanded=False):
                st.json(script)


def _visual_test_limited(bot) -> None:
    """Plan terms for every slide, then perform exactly one real image fetch."""
    with st.expander("4. Visual sourcing — one-image diagnostic", expanded=True):
        script = st.session_state.get("tp_script")
        if not script:
            st.warning("Generate a script first.")
            return
        scenes = script.get("script", []) if isinstance(script, dict) else []
        if not isinstance(scenes, list) or not scenes:
            st.warning("The script contains no scenes to inspect.")
            return

        video_title = str(script.get("title", "") or (script.get("titles") or [""])[0])
        st.markdown("**Planned search terms — every slide**")
        planned: list[list[str]] = []
        for index, scene in enumerate(scenes, 1):
            terms = _three_visual_terms(scene if isinstance(scene, dict) else {}, video_title)
            planned.append(terms)
            st.write(f"Slide {index}: `1.` {terms[0]}  ·  `2.` {terms[1]}  ·  `3.` {terms[2]}")

        possible = [i for i in (2, 3) if i < len(scenes)]
        target_index = random.choice(possible) if possible else len(scenes) - 1
        target_terms = planned[target_index]
        selected_term = random.choice(target_terms)
        st.info(f"Only **one image** will be sourced: randomly selected slide **{target_index + 1}**, using one of its three planned terms: **{selected_term}**.")

        if st.button("▶ Run one-image visual test", key="tp_run_visuals_one", type="primary", use_container_width=True):
            config = st.session_state.tp_config
            test_scene = dict(scenes[target_index]) if isinstance(scenes[target_index], dict) else {}
            test_scene["primary_entity"] = selected_term
            test_scene["visual_subject"] = selected_term
            test_scene["specific_search_prompt"] = selected_term
            test_scene["query"] = selected_term
            one_scene_script = dict(script)
            one_scene_script["script"] = [test_scene]

            def run():
                result = bot.process_visuals_async(
                    one_scene_script,
                    bot.LANGUAGES[config["language"]],
                    config["format_mode"],
                )
                return test_phase_runtime._run_async(result) or []

            st.session_state.tp_visuals = _run_with_progress("One-image visual test", run)
            st.success("Visual diagnostic fetched one image only.")

        items = _collect_visual_items(st.session_state.get("tp_visuals") or [])
        if items:
            for item in items[:1]:
                st.image(item["path"], caption=f"Diagnostic image · {item['label']}", use_container_width=True)
        elif not st.session_state.get("tp_visuals"):
            st.caption("No image has been sourced yet.")


def _render_test(bot) -> None:
    with st.expander("5. Subs / overlays — real compile/render test", expanded=True):
        script = st.session_state.get("tp_script")
        visuals = st.session_state.get("tp_visuals") or []
        audio = st.session_state.get("tp_audio")
        if not script or not visuals or audio is None:
            st.warning("This test needs a script, sourced visuals, and generated audio. Complete steps 2–4 first.")
            return
        st.caption(
            "This calls the same compile_video() function used by the factory. "
            "The render path owns subtitles, overlays, branding and final frame composition."
        )
        if st.button("▶ Run real subtitle/overlay render", key="tp_run_render", type="primary", use_container_width=True):
            def run():
                audio_paths, word_timings = audio
                config = st.session_state.tp_config
                return bot.compile_video(
                    visuals,
                    audio_paths,
                    word_timings,
                    bot.LANGUAGES[config["language"]],
                    config["format_mode"],
                )
            st.session_state.tp_rendered = str(_run_with_progress("Subtitle/overlay render test", run) or "")
        path = st.session_state.get("tp_rendered", "")
        if path and os.path.isfile(path):
            st.video(path)
            st.success(f"Render test passed: {path}")
        else:
            st.caption("Nothing has been rendered yet.")


def _topic_test_progress(bot) -> None:
    """Keep the original topic UI but wrap its actual discovery call with progress."""
    original_discovery = test_phase_runtime.discover_three_candidates
    restore = _wrap_for_progress(test_phase_runtime, "discover_three_candidates", "Topic discovery test")
    try:
        return test_phase_runtime._original_topic_test(bot)
    finally:
        restore()


def _audio_test_progress(bot) -> None:
    original = getattr(bot, "generate_voiceover_and_timestamps", None)
    if callable(original):
        restore = _wrap_for_progress(bot, "generate_voiceover_and_timestamps", "Audio generation test")
    else:
        restore = lambda: None
    try:
        return test_phase_runtime._original_audio_test(bot)
    finally:
        restore()


def _metadata_test_progress(bot) -> None:
    restores = []
    for owner, attr, label in (
        (test_phase_runtime, "_build_clean_metadata", "Metadata builder test"),
        (test_phase_runtime, "build_pinned_comment", "Pinned-comment builder test"),
    ):
        if callable(getattr(owner, attr, None)):
            restores.append(_wrap_for_progress(owner, attr, label))
    try:
        return test_phase_runtime._original_metadata_test(bot)
    finally:
        for restore in restores:
            restore()


def _upload_test_progress(bot) -> None:
    restores = []
    for owner, attr, label in (
        (test_phase_runtime, "validate_final_video", "Upload video validation"),
        (test_phase_runtime, "validate_final_upload_metadata", "Upload metadata validation"),
    ):
        if callable(getattr(owner, attr, None)):
            restores.append(_wrap_for_progress(owner, attr, label))
    controller_cls = getattr(test_phase_runtime, "WorkflowController", None)
    if controller_cls is not None and callable(getattr(controller_cls, "upload_manual", None)):
        restores.append(_wrap_for_progress(controller_cls, "upload_manual", "Real upload test"))
    try:
        return test_phase_runtime._original_upload_test(bot)
    finally:
        for restore in restores:
            restore()


def install_test_phase_patches() -> None:
    """Install Test Phase-only UI corrections once."""
    if getattr(test_phase_runtime, "_enhanced_test_phase_patches_installed", False):
        return

    # Preserve originals so the wrappers below can keep the production Test Phase UI.
    test_phase_runtime._original_topic_test = test_phase_runtime._topic_test
    test_phase_runtime._original_audio_test = test_phase_runtime._audio_test
    test_phase_runtime._original_metadata_test = test_phase_runtime._metadata_test
    test_phase_runtime._original_upload_test = test_phase_runtime._upload_test

    test_phase_runtime._topic_test = _topic_test_progress
    test_phase_runtime._audio_test = _audio_test_progress
    test_phase_runtime._metadata_test = _metadata_test_progress
    test_phase_runtime._upload_test = _upload_test_progress

    # Test Phase-only replacements.
    test_phase_runtime._script_test = _offline_script_test
    test_phase_runtime._visual_test = _visual_test_limited
    test_phase_runtime._render_test = _render_test
    test_phase_runtime._enhanced_test_phase_patches_installed = True
