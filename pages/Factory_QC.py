from __future__ import annotations

import os
import streamlit as st

import ultimate_bot
import visual_runtime
from factory_runtime import install_safe_exception_hook, patch_dashboard_runtime
from provider_runtime import patch_provider_adapters
from quality_runtime import patch_quality_control
from runtime_bindings import bind_dashboard_patches, harden_editorial_defaults
from semantic_runtime import patch_semantic_dedup
from story_ranker import patch_story_selection
from visual_qa_runtime import install_visual_qa_bridge
from visual_runtime import patch_visual_pipeline
from audio_runtime import patch_audio_pipeline
from newsroom_dashboard import render_dashboard
import newsroom_dashboard
from visual_replacement_runtime import install_visual_replacement_bridge
from workflow_progress_runtime import install_workflow_progress_bridge, render_progress_events
from visual_resilience_runtime import install_visual_resilience

st.set_page_config(page_title="Factory QC", page_icon="🛠️", layout="wide")


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
    install_visual_replacement_bridge(ultimate_bot, newsroom_dashboard)
    install_workflow_progress_bridge(__import__("workflow_runtime"))


_init_runtime()

render_dashboard(ultimate_bot)
controller = st.session_state.get("nr_ai_controller")
if controller:
    snap = controller.snapshot()
    render_progress_events(snap, st)
