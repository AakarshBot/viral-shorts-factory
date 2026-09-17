from __future__ import annotations

import streamlit as st

import ultimate_bot
from audio_runtime import patch_audio_pipeline
from factory_runtime import install_safe_exception_hook, patch_dashboard_runtime
from provider_runtime import patch_provider_adapters
from quality_runtime import patch_quality_control
from runtime_bindings import bind_dashboard_patches, harden_editorial_defaults
from semantic_runtime import patch_semantic_dedup
from story_ranker import patch_story_selection
from visual_qa_runtime import install_visual_qa_bridge
from visual_runtime import patch_visual_pipeline
from test_phase_runtime import render_test_phase

st.set_page_config(page_title="Test Phase", page_icon="🧪", layout="wide")


def _init_runtime() -> None:
    if not getattr(ultimate_bot, "_dashboard_runtime_initialized", False):
        install_safe_exception_hook()
        patch_dashboard_runtime(ultimate_bot)
        patch_semantic_dedup()
        patch_story_selection(ultimate_bot)
        harden_editorial_defaults(ultimate_bot)
        patch_quality_control(ultimate_bot)
        install_visual_qa_bridge(ultimate_bot.visual_runtime if hasattr(ultimate_bot, "visual_runtime") else __import__("visual_runtime"))
        patch_visual_pipeline(ultimate_bot)
        patch_audio_pipeline(ultimate_bot)
        patch_provider_adapters(ultimate_bot)
        ultimate_bot._dashboard_runtime_initialized = True
    else:
        harden_editorial_defaults(ultimate_bot)
        install_visual_qa_bridge(__import__("visual_runtime"))
        patch_provider_adapters(ultimate_bot)
    bind_dashboard_patches(ultimate_bot)


_init_runtime()
render_test_phase(ultimate_bot)
