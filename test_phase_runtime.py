"""Interactive, step-by-step diagnostic workbench for Viral Shorts Factory."""
from __future__ import annotations

import asyncio
import copy
import inspect
import os
import sqlite3
from typing import Any, Dict

import streamlit as st

from workflow_runtime import CRICKET_CATEGORIES, FORMAT_OPTIONS, WorkflowController, discover_three_candidates
from newsroom_dashboard import _category_options, _collect_visual_items, _script_text, _metadata_variants
from youtube_comment_runtime import _build_clean_metadata, build_pinned_comment
from final_qc_runtime import validate_final_upload_metadata, validate_final_video

TEST_STEPS = (
    ("topic", "1. Topic choosing"),
    ("script", "2. Script writing"),
    ("audio", "3. Audio"),
    ("visuals", "4. Visual sourcing"),
    ("render", "5. Subs / overlays"),
    ("metadata", "6. Title / description"),
    ("upload", "7. Upload"),
)


def _init_state() -> None:
    defaults = {
        "tp_config": {}, "tp_candidates": [], "tp_story": None, "tp_script": None,
        "tp_audio": None, "tp_visuals": [], "tp_rendered": "", "tp_metadata": {},
        "tp_upload_result": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _run_async(result: Any) -> Any:
    if not inspect.isawaitable(result):
        return result
    try:
        return asyncio.run(result)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(result)
        finally:
            loop.close()


def _selected_language(bot) -> tuple[str, Dict[str, Any]]:
    labels = [cfg["label"] for cfg in bot.LANGUAGES.values()]
    label = st.selectbox("Language", labels, key="tp_language")
    key = next((k for k, cfg in bot.LANGUAGES.items() if cfg["label"] == label), "english")
    return key, bot.LANGUAGES[key]


def _topic_test(bot) -> None:
    with st.expander("1. Topic choosing — real discovery test", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            format_label = st.selectbox("Format", list(FORMAT_OPTIONS.keys()), key="tp_format")
            lang_key, _ = _selected_language(bot)
        with c2:
            format_mode = FORMAT_OPTIONS[format_label]
            requested_topic = ""
            cricket_scope = ""
            if format_label == "Cricket":
                cricket_scope = st.selectbox("Cricket scope", list(CRICKET_CATEGORIES.keys()), key="tp_cricket_scope")
                requested_topic = st.text_input("Specific topic / keyword", key="tp_topic", placeholder="Example: BCCI impact player rule")
            else:
                options = _category_options(bot, format_mode)
                category_label = st.selectbox("Category", list(options.keys()), key="tp_category")
                category_key = options[category_label]

        if format_label == "Cricket":
            category_key = "sports_stories_of_day"
        config = {
            "format_mode": "cricket" if format_label == "Cricket" else format_mode,
            "display_format": format_label,
            "category": category_key,
            "language": lang_key,
            "cricket_pipeline": format_label == "Cricket",
            "cricket_category": cricket_scope,
            "requested_topic": requested_topic.strip(),
            "language_label": bot.LANGUAGES[lang_key]["label"],
        }
        st.session_state.tp_config = config

        if st.button("▶ Run real topic discovery", key="tp_run_topic", type="primary", use_container_width=True):
            conn = sqlite3.connect(bot.DB_PATH)
            try:
                st.session_state.tp_candidates = discover_three_candidates(bot, config, conn)
            finally:
                conn.close()

        candidates = st.session_state.tp_candidates
        if not candidates:
            st.caption("Nothing has been run yet. This button calls the same discovery function as the factory.")
            return

        labels = [f"#{item.get('discovery_rank', i + 1)} — {item.get('title', 'Untitled')}" for i, item in enumerate(candidates)]
        picked = st.selectbox("Test story", labels, key="tp_story_choice")
        item = candidates[labels.index(picked)]
        st.info(f"**Source:** {item.get('source_label', 'News source')}\n\n**Reason:** {item.get('discovery_reason', '')}")
        if item.get("story_url"):
            st.link_button("Open source article", item["story_url"])
        if st.button("✅ Use this topic for the next test", key="tp_use_story"):
            st.session_state.tp_story = dict(item)
            st.success("Topic locked into the test workspace.")


def _story_for_test() -> Dict[str, Any]:
    if st.session_state.get("tp_story"):
        return dict(st.session_state.tp_story)
    return {
        "title": st.session_state.get("tp_manual_story_title", "").strip(),
        "topic": st.session_state.get("tp_manual_story_topic", "").strip(),
        "summary": st.session_state.get("tp_manual_story_summary", "").strip(),
        "url": st.session_state.get("tp_manual_story_url", "").strip(),
        "source": "Manual diagnostic input",
    }


def _script_test(bot) -> None:
    with st.expander("2. Script writing — real script test", expanded=True):
        st.caption("Uses the live bot.write_script() path. No script fixture is substituted.")
        if not st.session_state.get("tp_story"):
            st.markdown("No topic is locked yet; use the manual fields below for an isolated script test.")
            st.text_input("Story title", key="tp_manual_story_title")
            st.text_input("Topic", key="tp_manual_story_topic")
            st.text_area("Story summary", key="tp_manual_story_summary", height=120)
            st.text_input("Article URL (optional)", key="tp_manual_story_url")
        if not st.session_state.tp_config:
            st.warning("Run Topic choosing first so the test has a real language/category configuration.")
            return
        story = _story_for_test()
        if st.button("▶ Run real script writer", key="tp_run_script", type="primary", use_container_width=True):
            conn = sqlite3.connect(bot.DB_PATH)
            try:
                config = st.session_state.tp_config
                st.session_state.tp_script = bot.write_script(story, bot.LANGUAGES[config["language"]], config["category"], conn, config["format_mode"])
            finally:
                conn.close()
        script = st.session_state.get("tp_script")
        if not script:
            st.caption("Nothing has been generated yet.")
            return
        st.text_area("Narration preview", _script_text(script), height=320, disabled=True, key="tp_script_preview")
        with st.expander("Raw script object", expanded=False):
            st.json(script)


def _audio_test(bot) -> None:
    with st.expander("3. Audio — real voice + timing test", expanded=True):
        script = st.session_state.get("tp_script")
        if not script:
            st.warning("Generate a script first.")
            return
        if st.button("▶ Run real audio generator", key="tp_run_audio", type="primary", use_container_width=True):
            config = st.session_state.tp_config
            result = bot.generate_voiceover_and_timestamps(script, bot.LANGUAGES[config["language"]])
            st.session_state.tp_audio = _run_async(result)
        audio = st.session_state.get("tp_audio")
        if audio is None:
            st.caption("Nothing has been generated yet.")
            return
        try:
            audio_paths, timings = audio
        except (TypeError, ValueError):
            audio_paths, timings = [], []
        st.success(f"Audio test complete: {len(audio_paths or [])} scene track(s).")
        for index, path in enumerate(audio_paths or [], 1):
            if path and os.path.isfile(path):
                st.audio(path, format="audio/mp3")
                st.caption(f"Scene {index}: {path}")
        if timings:
            st.write("Word-timing preview")
            st.dataframe(timings, use_container_width=True, hide_index=True)


def _visual_query_override(script: Any, query: str) -> Any:
    if not query.strip():
        return script
    patched = copy.deepcopy(script)
    scenes = patched.get("script") if isinstance(patched, dict) else None
    if not isinstance(scenes, list):
        scenes = [patched] if isinstance(patched, dict) else []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        scene["primary_entity"] = query.strip()
        scene["visual_subject"] = query.strip()
        scene["specific_search_prompt"] = query.strip()
        scene["query"] = query.strip()
    return patched


def _visual_test(bot) -> None:
    with st.expander("4. Visual sourcing — real search + verification test", expanded=True):
        script = st.session_state.get("tp_script")
        if not script:
            st.warning("Generate a script first.")
            return
        query = st.text_input("Optional image search query override", key="tp_visual_query", placeholder="Example: BCCI", help="Leave blank to test the script's own visual subjects.")
        if st.button("▶ Run real visual sourcing", key="tp_run_visuals", type="primary", use_container_width=True):
            config = st.session_state.tp_config
            test_script = _visual_query_override(script, query)
            result = bot.process_visuals_async(test_script, bot.LANGUAGES[config["language"]], config["format_mode"])
            st.session_state.tp_visuals = _run_async(result) or []
        items = _collect_visual_items(st.session_state.get("tp_visuals") or [])
        if not items:
            st.caption("Nothing has been sourced yet.")
            return
        st.success(f"Visual test complete: {len(items)} unique image file(s).")
        for index, item in enumerate(items, 1):
            st.image(item["path"], caption=f"{index}. {item['label']}", use_container_width=True)


def _render_test(bot) -> None:
    with st.expander("5. Subs / overlays — real compile/render test", expanded=True):
        script = st.session_state.get("tp_script")
        visuals = st.session_state.get("tp_visuals") or []
        audio = st.session_state.get("tp_audio")
        if not script or not visuals or audio is None:
            st.warning("This test needs a script, sourced visuals, and generated audio. Complete steps 2–4 first.")
            return
        overlay_text = st.text_input("Test headline / overlay text", key="tp_overlay_text", placeholder="Example: BCCI changes the rule")
        render_script = copy.deepcopy(script)
        if isinstance(render_script, dict) and overlay_text.strip():
            render_script["title"] = overlay_text.strip()
            render_script["headline"] = overlay_text.strip()
            render_script["overlay_text"] = overlay_text.strip()
        if st.button("▶ Run real subtitle/overlay render", key="tp_run_render", type="primary", use_container_width=True):
            audio_paths, word_timings = audio
            config = st.session_state.tp_config
            result = bot.compile_video(visuals, audio_paths, word_timings, bot.LANGUAGES[config["language"]], config["format_mode"])
            st.session_state.tp_rendered = str(result or "")
        path = st.session_state.get("tp_rendered", "")
        if path and os.path.isfile(path):
            st.video(path)
            st.success(f"Render test passed: {path}")
        else:
            st.caption("Nothing has been rendered yet.")


def _metadata_test(bot) -> None:
    with st.expander("6. Title / description — real metadata test", expanded=True):
        script = st.session_state.get("tp_script")
        if not script or not st.session_state.get("tp_config"):
            st.warning("Generate a script and select a topic/config first.")
            return
        config = st.session_state.tp_config
        genre_cfg = bot.CONTENT_CATEGORIES.get(config.get("category", "national_global_affairs"), bot.CONTENT_CATEGORIES["national_global_affairs"])
        if st.button("▶ Run real metadata builder", key="tp_run_metadata", type="primary", use_container_width=True):
            title, description, tags = _build_clean_metadata(script, genre_cfg, config.get("trend_keyword", ""))
            comment = build_pinned_comment(script, title, genre_cfg.get("label", ""))
            st.session_state.tp_metadata = {
                "title": title or "", "description": description or "", "tags": tags if isinstance(tags, list) else [],
                "pinned_comment": comment or "", "variants": _metadata_variants(bot, script, config["category"]),
            }
        metadata = st.session_state.get("tp_metadata") or {}
        if not metadata:
            st.caption("Nothing has been generated yet.")
            return
        st.write("**Factory-generated title**")
        st.write(metadata["title"])
        st.write("**Factory-generated description**")
        st.text_area("Description preview", metadata["description"], height=160, disabled=True, key="tp_meta_description")
        st.write("**Factory-generated pinned comment**")
        st.write(metadata["pinned_comment"])
        variants = metadata.get("variants") or {}
        for field, label in (("title", "Title options"), ("description", "Description options"), ("pinned_comment", "Pinned comment options")):
            options = variants.get(field) or []
            if options:
                st.radio(label, options, key=f"tp_meta_pick_{field}")


def _upload_test(bot) -> None:
    with st.expander("7. Upload — real validation / optional real upload test", expanded=True):
        rendered = st.session_state.get("tp_rendered", "")
        script = st.session_state.get("tp_script") or {}
        config = st.session_state.get("tp_config") or {}
        metadata = st.session_state.get("tp_metadata") or {}
        video_path = st.text_input("Video path", value=rendered, key="tp_upload_path", placeholder=r"C:\...\output\final_video_output.mp4")
        title = st.text_input("Upload title", value=str(metadata.get("title", "")), key="tp_upload_title")
        description = st.text_area("Upload description", value=str(metadata.get("description", "")), height=120, key="tp_upload_description")
        comment = st.text_area("Pinned comment", value=str(metadata.get("pinned_comment", "")), height=100, key="tp_upload_comment")
        visibility = st.selectbox("Visibility", ["Private", "Public"], key="tp_upload_visibility")
        validate_col, upload_col = st.columns(2)
        with validate_col:
            if st.button("🔍 Validate upload payload", key="tp_validate_upload", use_container_width=True):
                try:
                    validate_final_video(video_path)
                    clean_title, clean_description, _ = _build_clean_metadata(
                        {**script, "title": title, "seo_description": description},
                        bot.CONTENT_CATEGORIES.get(config.get("category", "national_global_affairs"), bot.CONTENT_CATEGORIES["national_global_affairs"]),
                        config.get("trend_keyword", ""),
                    )
                    final_title, final_description, final_comment = validate_final_upload_metadata(clean_title, clean_description, comment)
                    st.success("Upload validation passed.")
                    st.json({"video_path": video_path, "title": final_title, "description": final_description, "pinned_comment": final_comment, "visibility": visibility})
                except Exception as exc:
                    st.error(f"Upload validation failed: {type(exc).__name__}: {exc}")
        st.checkbox("I understand the next button performs a real YouTube upload.", key="tp_real_upload_ack")
        with upload_col:
            allowed = bool(st.session_state.get("tp_real_upload_ack"))
            if st.button("⬆️ Perform real upload test", key="tp_real_upload", disabled=not allowed, use_container_width=True):
                try:
                    genre_cfg = bot.CONTENT_CATEGORIES.get(config.get("category", "national_global_affairs"), bot.CONTENT_CATEGORIES["national_global_affairs"])
                    uploader = WorkflowController(bot)
                    uploader._real_uploader = getattr(bot, "upload_to_youtube", None)
                    result = uploader.upload_manual(video_path, script, title, description, comment, "public" if visibility == "Public" else "private", genre_cfg)
                    st.session_state.tp_upload_result = result
                    st.success(f"Real upload test succeeded. Video ID: {result}")
                except Exception as exc:
                    st.error(f"Real upload test failed: {type(exc).__name__}: {exc}")
        if st.session_state.get("tp_upload_result"):
            st.info(f"Last upload test result: {st.session_state.tp_upload_result}")


def render_test_phase(bot) -> None:
    _init_state()
    st.markdown("# 🧪 Test Phase")
    st.caption("Step-isolated factory diagnostics. Each Run button calls the same production function used by the real factory. No step runs until you click its button.")
    st.sidebar.markdown("### Test Phase")
    st.sidebar.caption("Use these tests to pinpoint the first failing production step.")
    if st.sidebar.button("↻ Reset test workspace", use_container_width=True):
        for key in ("tp_config", "tp_candidates", "tp_story", "tp_script", "tp_audio", "tp_visuals", "tp_rendered", "tp_metadata", "tp_upload_result"):
            st.session_state[key] = {} if key in {"tp_config", "tp_metadata"} else [] if key in {"tp_candidates", "tp_visuals"} else None if key in {"tp_story", "tp_script", "tp_audio"} else ""
        st.rerun()
    status = {}
    for key, label in TEST_STEPS:
        status[label] = {
            "topic": bool(st.session_state.get("tp_candidates")),
            "script": bool(st.session_state.get("tp_script")),
            "audio": bool(st.session_state.get("tp_audio")),
            "visuals": bool(_collect_visual_items(st.session_state.get("tp_visuals", []))),
            "render": bool(st.session_state.get("tp_rendered")) and os.path.isfile(st.session_state.get("tp_rendered", "")),
            "metadata": bool(st.session_state.get("tp_metadata")),
            "upload": bool(st.session_state.get("tp_upload_result")),
        }[key]
    passed = sum(status.values())
    st.progress(passed / len(TEST_STEPS), text=f"Diagnostic progress: {passed}/{len(TEST_STEPS)} steps have results")
    _topic_test(bot)
    _script_test(bot)
    _audio_test(bot)
    _visual_test(bot)
    _render_test(bot)
    _metadata_test(bot)
    _upload_test(bot)
