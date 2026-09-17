"""Test-only diagnostics layered onto the step-isolated Test Phase.

The visual diagnostic deliberately uses the same production query planner as the
real factory. Test mode limits execution to one scene and skips semantic QA,
but it does not invent a second search-query algorithm.
"""
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any, Callable

import streamlit as st

import script_guard_runtime
import test_phase_runtime
import visual_runtime
from newsroom_dashboard import _collect_visual_items, _script_text


def _run_with_progress(label: str, fn: Callable[[], Any]) -> Any:
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


def _production_visual_queries(scene: dict[str, Any], video_title: str) -> tuple[list[str], str]:
    """Return the exact query ladder the production visual runtime will use."""
    queries, visual_type = visual_runtime._build_search_variants(scene, video_title)
    clean_queries: list[str] = []
    for query in queries:
        query = " ".join(str(query or "").split()).strip(" ,.-")
        if query and query.lower() not in {q.lower() for q in clean_queries}:
            clean_queries.append(query)
    return clean_queries, str(visual_type or "GENERAL_CONTEXT")


def _offline_script_test(bot) -> None:
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
    """Show the real production query ladder for every scene; fetch exactly one scene."""
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
        st.markdown("**Planned search terms — actual factory logic**")
        planned: list[list[str]] = []
        for index, scene in enumerate(scenes, 1):
            scene_dict = scene if isinstance(scene, dict) else {}
            try:
                queries, visual_type = _production_visual_queries(scene_dict, video_title)
            except Exception as exc:
                queries, visual_type = [], "ERROR"
                st.error(f"Slide {index}: production visual planner failed: {type(exc).__name__}: {exc}")
            planned.append(queries)
            if queries:
                rendered = "  ·  ".join(f"`{n}.` {q}" for n, q in enumerate(queries, 1))
                st.write(f"Slide {index} [{visual_type}]: {rendered}")
            else:
                st.write(f"Slide {index} [{visual_type}]: **No production query generated**")

        possible = [i for i in range(len(scenes)) if planned[i]]
        target_index = random.choice(possible) if possible else len(scenes) - 1
        target_queries = planned[target_index]
        selected_query = target_queries[0] if target_queries else ""
        if selected_query:
            st.info(
                f"Only **one scene** will be sourced: randomly selected slide **{target_index + 1}**. "
                f"The test will use the production query ladder unchanged, starting with: **{selected_query}**."
            )
        else:
            st.warning(f"Slide {target_index + 1} has no production search query to execute.")

        if st.button("▶ Run one-image visual test", key="tp_run_visuals_one", type="primary", use_container_width=True):
            config = st.session_state.tp_config
            test_scene = dict(scenes[target_index]) if isinstance(scenes[target_index], dict) else {}
            one_scene_script = dict(script)
            one_scene_script["script"] = [test_scene]

            original_gate = visual_runtime._strict_gate
            original_cache = visual_runtime.get_cached_asset

            def local_test_gate(bot_obj, img_bytes, seg, video_title="", source=""):
                if not img_bytes or not visual_runtime._local_visual_sanity(img_bytes):
                    return False, "LOCAL-REJECT", 0, True
                return True, "TEST-LOCAL", 100, False

            try:
                visual_runtime._strict_gate = local_test_gate
                visual_runtime.get_cached_asset = lambda *args, **kwargs: (None, None)

                def run():
                    result = bot.process_visuals_async(
                        one_scene_script,
                        bot.LANGUAGES[config["language"]],
                        config["format_mode"],
                    )
                    return test_phase_runtime._run_async(result) or []

                st.session_state.tp_visuals = _run_with_progress("One-image visual test", run)
                st.success("Visual diagnostic fetched one scene using the production visual-query pipeline. Gemini semantic QA was skipped for this test.")
            finally:
                visual_runtime._strict_gate = original_gate
                visual_runtime.get_cached_asset = original_cache

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
            st.warning("This test needs a script, sourced visuals, and reused previous-run audio. Complete steps 2–4 first.")
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


def _local_topic_candidates(bot) -> None:
    with st.expander("1. Topic choosing — offline / no-API test", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            format_options = ["Deep Dive", "Top 5", "Cricket"]
            format_label = st.selectbox("Format", format_options, key="tp_format")
            labels = [cfg["label"] for cfg in bot.LANGUAGES.values()]
            lang_label = st.selectbox("Language", labels, key="tp_language")
        with c2:
            category_options = [
                ("National & Global Affairs", "national_global_affairs"),
                ("Technology", "technology"),
                ("Sports", "sports"),
                ("Business & Finance", "business_finance"),
                ("Entertainment", "entertainment"),
            ]
            category_label = st.selectbox("Category", [label for label, _ in category_options], key="tp_category")
            requested_topic = st.text_input("Local test topic", key="tp_topic", placeholder="Example: India's next big AI breakthrough")

        language_key = next((key for key, cfg in bot.LANGUAGES.items() if cfg["label"] == lang_label), "english")
        format_mode = {"Deep Dive": "regular", "Top 5": "top5", "Cricket": "cricket"}[format_label]
        category_key = dict(category_options)[category_label]
        if format_label == "Cricket":
            category_key = "sports_stories_of_day"

        config = {
            "format_mode": format_mode,
            "display_format": format_label,
            "category": category_key,
            "language": language_key,
            "cricket_pipeline": format_label == "Cricket",
            "cricket_category": "Offline diagnostic",
            "requested_topic": requested_topic.strip(),
            "language_label": lang_label,
        }
        st.session_state.tp_config = config

        topic_seed = requested_topic.strip() or {
            "national_global_affairs": "India's latest major policy change",
            "technology": "The next major AI breakthrough",
            "sports": "A major sports moment everyone is discussing",
            "business_finance": "A major business or market development",
            "entertainment": "The biggest entertainment story today",
            "sports_stories_of_day": "The biggest cricket story today",
        }.get(category_key, "A major story worth knowing")

        candidates = [
            {"title": topic_seed, "source_label": "Offline diagnostic topic", "discovery_reason": "Deterministic local seed — no discovery API called.", "discovery_rank": 1},
            {"title": f"{topic_seed} — what changed", "source_label": "Offline diagnostic variant", "discovery_reason": "Local angle variant — no discovery API called.", "discovery_rank": 2},
            {"title": f"{topic_seed} — what it means", "source_label": "Offline diagnostic variant", "discovery_reason": "Local impact variant — no discovery API called.", "discovery_rank": 3},
        ]
        st.session_state.tp_candidates = candidates

        labels = [f"#{item['discovery_rank']} — {item['title']}" for item in candidates]
        selected = st.radio("Choose a local test story", labels, key="tp_selected_candidate")
        selected_index = labels.index(selected) if selected in labels else 0
        st.session_state.tp_story = candidates[selected_index]
        st.success("Topic diagnostic uses local candidates only — no discovery API call.")


def _audio_test_reuse(bot) -> None:
    with st.expander("3. Audio — reuse latest local audio", expanded=True):
        candidates = []
        roots = [Path(getattr(bot, "OUTPUT_DIR", "")), Path(getattr(bot, "BASE_DIR", "")) / "output"]
        for root in roots:
            if not root or not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".mp3", ".wav", ".aac", ".m4a"}:
                    candidates.append(path)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        audio_path = str(candidates[0]) if candidates else ""
        timing = ""
        if audio_path:
            stem = Path(audio_path).with_suffix("")
            for ext in (".json", ".txt"):
                probe = Path(str(stem) + "_timings" + ext)
                if probe.is_file():
                    timing = str(probe)
                    break
        if audio_path:
            st.session_state.tp_audio = ([audio_path], timing)
            st.success(f"Reused local audio: {audio_path}")
        else:
            st.session_state.tp_audio = None
            st.warning("No previous audio file was found locally.")


def _patch_test_runtime(bot) -> None:
    current = getattr(bot, "write_script", None)
    if current and not getattr(current, "_test_phase_patched", False):
        original_write = current

        def offline_write_script(story_data, language_cfg, genre_key, conn, format_mode):
            return script_guard_runtime.source_only_fallback(story_data, language_cfg, genre_key, format_mode)

        offline_write_script._test_phase_patched = True
        bot.write_script = offline_write_script
        bot.run_robot.__globals__["write_script"] = offline_write_script
        bot._test_phase_original_write_script = original_write

    current_visual = getattr(bot, "process_visuals_async", None)
    if current_visual and not getattr(current_visual, "_test_phase_patched", False):
        bot._test_phase_original_visuals = current_visual


def render_test_phase(bot) -> None:
    _patch_test_runtime(bot)
    _local_topic_candidates(bot)
    _offline_script_test(bot)
    _audio_test_reuse(bot)
    _visual_test_limited(bot)
    _render_test(bot)
