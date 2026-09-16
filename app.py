from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict

import streamlit as st

import ultimate_bot
from audio_runtime import patch_audio_pipeline
from db_architecture import migrate_vault
from diagnostics_runtime import run_offline_diagnostics
from factory_runtime import install_safe_exception_hook, patch_dashboard_runtime
from provider_runtime import patch_provider_adapters
from quality_runtime import patch_quality_control
from runtime_bindings import bind_dashboard_patches, harden_editorial_defaults
from semantic_runtime import patch_semantic_dedup
from story_ranker import patch_story_selection
from visual_qa_runtime import install_visual_qa_bridge
from visual_runtime import patch_visual_pipeline
import visual_runtime
from workflow_runtime import (
    CRICKET_CATEGORIES,
    FORMAT_OPTIONS,
    WorkflowController,
    discover_three_candidates,
)

st.set_page_config(page_title="Viral Shorts Factory", page_icon="🎬", layout="wide")


def load_streamlit_secrets_into_runtime():
    secret_names = (
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "GNEWS_API_KEY",
        "UNSPLASH_ACCESS_KEY",
        "HF_TOKEN",
        "PEXELS_API_KEY",
    )
    loaded = []
    for name in secret_names:
        value = os.getenv(name)
        if not value:
            try:
                value = st.secrets.get(name)
            except Exception:
                value = None
        if value:
            value = str(value).strip()
            os.environ[name] = value
            setattr(ultimate_bot, name, value)
            loaded.append(name)
    print(
        "[Dashboard] Provider secrets loaded: " + ", ".join(loaded)
        if loaded
        else "[Dashboard] WARNING: No provider secrets were found.",
        flush=True,
    )
    return set(loaded)


load_streamlit_secrets_into_runtime()

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
    ultimate_bot.token_overlap_ratio = lambda _a, _b: 0.0
    # Learning/analytics are manual now. Production does not trigger a hidden
    # YouTube Analytics sync before or during story generation.
    ultimate_bot.run_analytics_sweep = lambda _conn: print(
        "   [Learning] Automatic analytics sync disabled in newsroom workflow.", flush=True
    )
    ultimate_bot._dashboard_runtime_initialized = True
else:
    harden_editorial_defaults(ultimate_bot)
    install_visual_qa_bridge(visual_runtime)
    patch_provider_adapters(ultimate_bot)

bind_dashboard_patches(ultimate_bot)

try:
