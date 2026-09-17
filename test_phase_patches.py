"""Small corrections layered onto the step-isolated Test Phase without touching production paths."""
from __future__ import annotations

import os

import streamlit as st

import test_phase_runtime


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
            audio_paths, word_timings = audio
            config = st.session_state.tp_config
            result = bot.compile_video(
                visuals,
                audio_paths,
                word_timings,
                bot.LANGUAGES[config["language"]],
                config["format_mode"],
            )
            st.session_state.tp_rendered = str(result or "")
        path = st.session_state.get("tp_rendered", "")
        if path and os.path.isfile(path):
            st.video(path)
            st.success(f"Render test passed: {path}")
        else:
            st.caption("Nothing has been rendered yet.")


def install_test_phase_patches() -> None:
    test_phase_runtime._render_test = _render_test
