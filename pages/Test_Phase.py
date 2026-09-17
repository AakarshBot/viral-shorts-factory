from __future__ import annotations

import streamlit as st

import ultimate_bot
import visual_runtime
from audio_runtime import patch_audio_pipeline
from factory_runtime import install_safe_exception_hook, patch_dashboard_runtime
from provider_runtime import patch_provider_adapters
from quality_runtime import patch_quality_control
from runtime_bindings import bind_dashboard_patches, harden_editorial_defaults
from semantic_runtime import patch_semantic_dedup
from story_ranker import patch_story_selection
from visual_qa_runtime import install_visual_qa_bridge
from visual_content_runtime import patch_content_first_visuals as patch_visual_pipeline
import test_phase_runtime
from test_phase_patches import install_test_phase_patches
from test_history_runtime import install_test_history_bridge, render_test_history
from visual_resilience_runtime import install_visual_resilience

st.set_page_config(page_title="Test Phase", page_icon="🧪", layout="wide")


def _init_runtime() -> None:
    if not getattr(ultimate_bot, "_dashboard_runtime_initialized", False):
        install_safe_exception_hook()
        patch_dashboard_runtime(ultimate_bot)
        patch_semantic_dedup()
        patch_story_selection(ultimate_bot)
        harden_editorial_defaults(ultimate_bot)
        patch_quality_control(ultimate_bot)
        install_visual_qa_bridge(visual_runtime)
        patch_visual_pipeline(ultimate_bot)
        patch_audio_pipeline(ultimate_bot)
        patch_provider_adapters(ultimate_bot)
        ultimate_bot._dashboard_runtime_initialized = True
    else:
        harden_editorial_defaults(ultimate_bot)
        install_visual_qa_bridge(visual_runtime)
        patch_provider_adapters(ultimate_bot)
    bind_dashboard_patches(ultimate_bot)
    install_visual_resilience()
    install_test_phase_patches()
    install_test_history_bridge(test_phase_runtime, ultimate_bot)


_init_runtime()
test_phase_runtime.render_test_phase(ultimate_bot)
render_test_history(ultimate_bot, st)
