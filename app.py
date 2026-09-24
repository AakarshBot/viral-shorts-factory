"""Single supported Streamlit dashboard for the Viral Shorts Factory.

Dashboard-only orchestration lives here. Factory generation modules remain
untouched: this file configures them, presents their progress and gates only
the user-facing visual approval/upload decisions.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict

import streamlit as st

import ultimate_bot
from db_architecture import migrate_vault
from diagnostics_runtime import run_offline_diagnostics
from factory_runtime import patch_dashboard_runtime
from provider_runtime import patch_provider_adapters
from quality_runtime import patch_quality_control
from runtime_bindings import bind_dashboard_patches
from story_ranker import patch_story_selection
from visual_qa_runtime import install_visual_qa_bridge
import visual_runtime
from workflow_runtime import CRICKET_CATEGORIES, FORMAT_OPTIONS
from dashboard_theme import apply_dashboard_theme

from dashboard_runtime import (
    LIVE_STAGE_SPEC,
    DashboardWorkflowController,
    build_discovery_evidence,
    collect_channel_statistics,
    collect_live_channel_statistics,
    discover_ranked_topics,
    discover_ai_topics,
    factory_function_coverage,
    run_demo_section,
    upload_ready_for_manual_decision,
    live_monitor_should_poll,
    _manual_crop_box_to_shorts,
)


MAX_DASHBOARD_DISCOVERY_HEADLINES = 60

_UI_ARTIFACT_RE = re.compile(
    r"(?i)(?<![a-z0-9])_arrow(?:_(?:right|left|up|down))?(?![a-z0-9])"
)


def _ui_text(value: Any, fallback: str = "") -> str:
    """Normalize generated text before it reaches Streamlit's HTML surface."""
    if value is None:
        return fallback
    text = str(value)
    text = _UI_ARTIFACT_RE.sub("", text)
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ui_html(value: Any, fallback: str = "") -> str:
    """Escape normalized dashboard text so generated content cannot break the layout."""
    return html.escape(_ui_text(value, fallback), quote=False)


st.set_page_config(page_title="Shorts Studio", page_icon="🎬", layout="wide")

st.markdown("""<style>
:root{
  --bg:#f4efe8;--surface:#fffdf9;--surface-soft:#faf6ef;--line:#e7ddd1;--line-strong:#d2c5b7;
  --text:#20242a;--muted:#716a61;--muted-2:#9a9084;--accent:#2f5d62;--accent-deep:#23484c;
  --accent-soft:#e8f0ef;--good:#2f7b61;--good-soft:#eaf5ef;--warn:#b56b2f;--warn-soft:#fbf0e4;
  --danger:#b64d42;--shadow:0 10px 30px rgba(69,49,31,.07);--shadow-lg:0 18px 48px rgba(69,49,31,.11);
}
html,body,[data-testid="stAppViewContainer"]{background:var(--bg);color:var(--text)}
[data-testid="stHeader"]{background:transparent;border-bottom:none}
.block-container{max-width:1380px;padding-top:1.45rem;padding-bottom:3rem}
section[data-testid="stSidebar"]{background:#11151d;border-right:1px solid #252b35;color:#eef2f7}
section[data-testid="stSidebar"]>div{padding-top:1.15rem}
section[data-testid="stSidebar"] .stMarkdown p,section[data-testid="stSidebar"] label,section[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{color:#b9c1cf}
html,body,.stApp{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif!important;letter-spacing:-.01em}
[data-testid="stIconMaterial"],
[data-testid="stExpanderToggleIcon"],
span[class*="material"]{
  font-family:"Material Symbols Rounded","Material Symbols Outlined","Material Icons"!important;
  font-feature-settings:"liga"!important;
  -webkit-font-feature-settings:"liga"!important;
  letter-spacing:normal!important;
  text-transform:none!important;
}
h1,h2,h3,h4{color:var(--text);letter-spacing:-.04em}
p{color:var(--text)}
.small-muted{color:var(--muted);font-size:.78rem}
.section-kicker{color:var(--accent);text-transform:uppercase;letter-spacing:.14em;font-size:.66rem;font-weight:850;margin-bottom:.28rem}
.section-title{color:var(--text);font-size:1.78rem;font-weight:850;line-height:1.1;margin:0}
.section-subtitle{color:var(--muted);font-size:.9rem;line-height:1.5;margin:.4rem 0 1.15rem}
.brand-card{background:var(--surface);border:1px solid var(--line);border-radius:22px;padding:18px 23px;min-height:88px;display:flex;flex-direction:column;justify-content:center;box-shadow:var(--shadow)}
.brand-pill{display:inline-block;width:max-content;border-radius:999px;padding:5px 9px;background:var(--accent-soft);border:1px solid #dcd7ff;color:var(--accent-deep);font-size:.64rem;font-weight:850;letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px}
.brand-title{font-size:1.9rem;font-weight:900;line-height:1.02;color:var(--text)}
.brand-sub{color:var(--muted);font-size:.86rem;margin-top:7px}
.factory-status{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:12px 14px;text-align:right;box-shadow:var(--shadow)}
.factory-status-label{font-size:.62rem;color:var(--muted-2);font-weight:850;letter-spacing:.12em}
.factory-status-value{font-size:.96rem;font-weight:900;color:var(--text);margin-top:3px}
.panel,.candidate,.story-card,.output-card,.release-card{background:var(--surface);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}
.panel{padding:16px 18px}
.story-card{padding:18px 18px;height:100%}
.output-card{padding:15px 16px}
.release-card{padding:16px 18px}
.story-rank{color:var(--accent);font-size:.66rem;font-weight:900;letter-spacing:.13em}
.story-title{font-size:1.03rem;font-weight:820;line-height:1.38;margin:7px 0}
.story-meta{color:var(--muted);font-size:.76rem;line-height:1.45}
.story-reason{color:#505a6a;font-size:.84rem;line-height:1.48;margin:10px 0 13px}
.score-chip{display:inline-flex;align-items:center;border-radius:999px;padding:4px 8px;background:#f2f4f7;border:1px solid var(--line);color:#414a59;font-size:.68rem;font-weight:800}
.stage-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:12px 0 14px}
.stage-card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:10px 11px}
.stage-card.active{border-color:#beb4ff;background:var(--accent-soft)}
.stage-card.done{border-color:#bfe5d2;background:var(--good-soft)}
.stage-card.stopped{border-color:#f1cf9f;background:var(--warn-soft)}
.stage-name{font-size:.76rem;font-weight:800;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stage-state{color:var(--muted);font-size:.66rem;margin-top:4px}
.qc-guide{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:12px 0 18px}
.qc-guide-step{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:12px 13px;box-shadow:var(--shadow)}
.qc-guide-step b{display:block;font-size:.77rem;margin-bottom:3px;color:var(--text)}
.qc-guide-step span{color:var(--muted);font-size:.71rem;line-height:1.42}
.qc-status{display:inline-flex;align-items:center;border-radius:999px;padding:5px 9px;font-size:.65rem;font-weight:900;letter-spacing:.08em}
.qc-status.ready{color:var(--good);background:var(--good-soft);border:1px solid #c6e8d6}
.qc-status.attention{color:var(--warn);background:var(--warn-soft);border:1px solid #f0d8b0}
.qc-meta{color:var(--muted);font-size:.74rem;line-height:1.45}
.qc-card-title{font-size:.97rem;font-weight:850;margin-bottom:7px;color:var(--text)}
.timeline{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:4px 16px;box-shadow:var(--shadow)}
.timeline-row{display:flex;gap:12px;padding:10px 0;border-bottom:1px solid #edf0f4}
.timeline-row:last-child{border-bottom:0}
.timeline-dot{width:22px;flex:0 0 22px;text-align:center}
.timeline-main{min-width:0;flex:1}.timeline-head{font-size:.77rem;font-weight:800;color:var(--text)}
.timeline-time{color:var(--muted-2);font-size:.66rem;margin-left:7px}
.timeline-message{color:var(--muted);font-size:.77rem;line-height:1.4;margin-top:2px}
.sidebar-title{font-size:1.04rem;font-weight:850;letter-spacing:-.02em;color:#f6f8fb}
.sidebar-kicker{color:#a99cff;text-transform:uppercase;letter-spacing:.12em;font-size:.62rem;font-weight:850}
.sidebar-status{background:#171c25;border:1px solid #29303b;border-radius:13px;padding:12px 13px;margin-top:10px}
.sidebar-status-title{font-size:.75rem;font-weight:800;color:#f3f5f8}
.sidebar-status-copy{color:#9ca6b5;font-size:.69rem;margin-top:3px;line-height:1.4}
.empty-state{background:linear-gradient(135deg,#ffffff 0%,#f8f7ff 100%);border:1px solid #ddd9ff;border-radius:21px;padding:28px;box-shadow:var(--shadow-lg)}
.empty-title{font-size:1.58rem;font-weight:900;letter-spacing:-.04em;color:var(--text)}
.empty-copy{color:var(--muted);font-size:.88rem;line-height:1.55;max-width:720px}
.meta-row{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}
.meta-chip{border:1px solid var(--line);background:#f7f8fa;border-radius:999px;padding:5px 9px;color:#596273;font-size:.69rem}
[data-testid="stMetric"]{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:11px 13px;box-shadow:var(--shadow)}
[data-testid="stMetricLabel"]{color:var(--muted)!important;font-size:.7rem!important}
[data-testid="stMetricValue"]{color:var(--text)!important;font-size:1.3rem!important}
.stButton>button,.stLinkButton>a{border-radius:10px;min-height:40px;font-weight:800;border:1px solid var(--line-strong);background:var(--surface);color:var(--text)}
.stButton>button[kind="primary"]{background:linear-gradient(180deg,#6854ef,#533dd9);color:#fff;border-color:#4e39cd;box-shadow:0 8px 18px rgba(91,70,232,.18)}
.stButton>button:hover,.stLinkButton>a:hover{border-color:#b8bec9;background:#f8f9fb}
.stButton>button[kind="primary"]:hover{background:linear-gradient(180deg,#5f4be5,#4b37c9);color:#fff;border-color:#4633bd}
.stTextInput>div>div,.stTextArea>div>div,.stSelectbox>div>div{background:var(--surface)!important;border-color:var(--line)!important;color:var(--text)!important}
.stTextInput input,.stTextArea textarea{color:var(--text)!important}
div[data-testid="stExpander"]{border:1px solid var(--line)!important;border-radius:12px!important;background:var(--surface)!important}
div[data-testid="stExpander"] summary p{font-size:.8rem;font-weight:800;color:var(--text)}
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--surface)}
[data-testid="stProgress"] div[role="progressbar"]{background:#e7eaf0}
[data-testid="stProgress"] div[role="progressbar"] > div{background:var(--accent)}
[data-baseweb="popover"]{
  z-index:1000000!important;
  background:var(--surface)!important;
  border:1px solid var(--line)!important;
  border-radius:12px!important;
  box-shadow:0 18px 50px rgba(15,23,42,.18)!important;
  opacity:1!important;
  overflow:hidden!important;
}
[data-baseweb="popover"] [data-baseweb="menu"],
[data-baseweb="popover"] [role="listbox"]{
  background:var(--surface)!important;
  color:var(--text)!important;
  opacity:1!important;
  border:0!important;
  box-shadow:none!important;
  padding:5px!important;
}
[data-baseweb="popover"] [role="option"]{
  color:var(--text)!important;
  background:#fff!important;
  border-radius:8px!important;
  min-height:38px!important;
}
[data-baseweb="popover"] [role="option"] span,
[data-baseweb="popover"] [role="option"] div{
  color:var(--text)!important;
  background:transparent!important;
  opacity:1!important;
  text-shadow:none!important;
}
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [role="option"][aria-selected="true"],
[data-baseweb="popover"] [aria-selected="true"]{
  background:var(--accent-soft)!important;
  color:var(--text)!important;
}
[data-baseweb="popover"] input{
  color:var(--text)!important;
  background:#fff!important;
  border-color:var(--line)!important;
}
.stSelectbox [data-baseweb="select"]>div{
  background:var(--surface)!important;
  border:1px solid var(--line)!important;
  border-radius:11px!important;
  min-height:42px!important;
}
.stSelectbox [data-baseweb="select"]>div:hover{
  border-color:#b8bec9!important;
}
.stSelectbox [data-baseweb="select"] *,
.stMultiSelect [data-baseweb="select"] *,
[data-baseweb="input"] input{
  color:var(--text)!important;
}
.stSelectbox [data-baseweb="select"],
.stMultiSelect [data-baseweb="select"],
[data-baseweb="input"]{
  background:#fff!important;
}
.crop-shell{background:var(--surface-soft);border:1px solid var(--line);border-radius:16px;padding:12px}
.crop-caption{color:var(--muted);font-size:.73rem;line-height:1.45;margin-bottom:9px}
.dashboard-footer{text-align:center;color:var(--muted-2);font-size:.7rem;padding:10px 0}

/* Keep generated content contained and readable at every dashboard breakpoint. */
.story-title,.story-reason,.output-card,.release-gate,.release-gate-detail,.timeline-message,
.brand-sub,.sidebar-status-copy,.live-bar-copy,.qc-guide-step span,.empty-copy{
  min-width:0;
  overflow-wrap:anywhere;
  word-break:break-word;
}
.story-title,.output-card{line-height:1.5}
.story-reason{max-height:7.5rem;overflow:auto;padding-right:4px}
.topic-card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:17px 17px 15px;box-shadow:var(--shadow);height:100%}
.topic-kicker{display:flex;justify-content:space-between;gap:8px;align-items:center;color:var(--accent);font-size:.64rem;font-weight:900;letter-spacing:.12em;text-transform:uppercase}
.topic-title{font-size:1.08rem;font-weight:850;line-height:1.34;color:var(--text);overflow-wrap:anywhere}
.topic-subtitle{font-size:.76rem;line-height:1.45;color:var(--muted)}
.topic-chips{display:flex;flex-wrap:wrap;gap:6px;margin:2px 0 3px}
.topic-chip{display:inline-flex;align-items:center;border:1px solid var(--line);background:#f7f1e9;border-radius:999px;padding:4px 7px;color:#625b52;font-size:.64rem;font-weight:800}
.topic-chip.strong{background:#eaf2f1;border-color:#c8dedd;color:#31565a}
.release-gate-detail,.timeline-message{line-height:1.5}
[data-testid="stMarkdownContainer"],[data-testid="stCaptionContainer"],
[data-testid="stTextArea"],[data-testid="stTextInput"]{min-width:0}
.stTextInput input,.stTextArea textarea{
  line-height:1.45!important;
  overflow-wrap:anywhere!important;
  word-break:break-word!important;
}
.stTextArea textarea{min-height:112px!important}
.stButton>button,.stLinkButton>a{
  white-space:normal!important;
  line-height:1.2!important;
}
section[data-testid="stSidebar"]{
  background:#eee6db;
  border-right:1px solid #ddd0c1;
}
section[data-testid="stSidebar"] .stMarkdown p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{
  color:#655e55;
}
.sidebar-title{color:#252a2d}
.sidebar-kicker{color:var(--accent)}
.sidebar-status{
  background:rgba(255,253,249,.86);
  border:1px solid #dcd0c3;
  box-shadow:none;
}
.sidebar-status-title{color:#252a2d}
.sidebar-status-copy{color:#756d63}
section[data-testid="stSidebar"] .st-key-workspace_mode button{
  min-height:42px!important;
  border-radius:12px!important;
  border:1px solid #d4c7b9!important;
  background:rgba(255,253,249,.82)!important;
  color:#2d3235!important;
  font-weight:850!important;
  box-shadow:none!important;
}
section[data-testid="stSidebar"] .st-key-workspace_mode button:hover{
  border-color:#a9bbb8!important;
  background:#f8fbfa!important;
}
section[data-testid="stSidebar"] .st-key-workspace_mode button[aria-selected="true"],
section[data-testid="stSidebar"] .st-key-workspace_mode button[aria-checked="true"]{
  transform:scale(1.02);
  border-color:#8eafab!important;
  background:#e9f1ef!important;
  color:#244d50!important;
  box-shadow:0 6px 16px rgba(47,93,98,.10)!important;
}
[data-testid="stSidebar"] [data-testid="stExpander"]{
  background:rgba(255,253,249,.82)!important;
  border-color:#d8ccbf!important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary{
  color:#252a2d!important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] pre{
  background:#20282b!important;
  color:#e9f0ed!important;
  border:1px solid #394346!important;
  border-radius:10px!important;
  font-size:.72rem!important;
  line-height:1.45!important;
  max-height:430px!important;
  overflow:auto!important;
}
[data-testid="stSidebar"] .stButton>button{
  background:rgba(255,253,249,.84)!important;
  border-color:#d2c5b7!important;
}
.stButton>button[kind="primary"]{
  background:linear-gradient(180deg,#3d6f74,#2f5d62)!important;
  border-color:#2b5559!important;
  box-shadow:0 8px 18px rgba(47,93,98,.16)!important;
}
.stButton>button[kind="primary"]:hover{
  background:linear-gradient(180deg,#35666b,#294f53)!important;
  border-color:#294f53!important;
}
.score-chip{background:#eef3f2;border-color:#cfdfdd;color:#31565a}
.meta-chip{background:#f7f1e9;border-color:#e2d6ca;color:#665e55}
.brand-card{background:linear-gradient(135deg,#fffdf9 0%,#f5eee5 100%);border-color:#e4d8cc}
.factory-status{background:#fffdf9;border-color:#e2d6ca}
.stage-card.active{border-color:#95b5b2;background:#eaf2f1}
.stage-card.done{border-color:#bcd9ca;background:#eef7f1}
.stage-card.stopped{border-color:#ebcbb1;background:#fcf1e6}
.empty-state{background:linear-gradient(135deg,#fffdf9 0%,#f4eeea 100%);border-color:#dfd3c8}

/* Live navigation: matte glass pills with an RGB-style edge and a clear active step. */
.st-key-live_format_menu button,
.st-key-live_topic_menu button,
.st-key-live_sports_menu button,
.st-key-live_cricket_scope_menu button,
.st-key-test_menu button{
  min-height:54px!important;
  padding:11px 22px!important;
  border-radius:18px!important;
  border:1.5px solid transparent!important;
  background:
    linear-gradient(rgba(255,253,249,.94),rgba(245,239,232,.94)) padding-box,
    conic-gradient(from 120deg,#ff5a5f,#ffd45a,#58d68d,#56a8ff,#9b6cff,#ff5a5f) border-box!important;
  color:#252a2d!important;
  box-shadow:0 10px 24px rgba(69,49,31,.08), inset 0 1px 0 rgba(255,255,255,.85)!important;
  backdrop-filter:blur(12px)!important;
  transition:transform .16s ease,box-shadow .16s ease!important;
}
.st-key-live_format_menu button:hover,
.st-key-live_topic_menu button:hover,
.st-key-live_sports_menu button:hover,
.st-key-live_cricket_scope_menu button:hover,
.st-key-test_menu button:hover{
  transform:translateY(-1px);
  box-shadow:0 12px 28px rgba(69,49,31,.11), inset 0 1px 0 rgba(255,255,255,.9)!important;
}
.st-key-live_format_menu button[aria-checked="true"],
.st-key-live_format_menu button[aria-selected="true"],
.st-key-live_topic_menu button[aria-checked="true"],
.st-key-live_topic_menu button[aria-selected="true"],
.st-key-live_sports_menu button[aria-checked="true"],
.st-key-live_sports_menu button[aria-selected="true"],
.st-key-live_cricket_scope_menu button[aria-checked="true"],
.st-key-live_cricket_scope_menu button[aria-selected="true"],
.st-key-test_menu button[aria-checked="true"],
.st-key-test_menu button[aria-selected="true"]{
  transform:scale(1.035);
  font-weight:900!important;
  box-shadow:0 14px 30px rgba(47,93,98,.16), inset 0 1px 0 rgba(255,255,255,.9)!important;
}
.live-choice-label{color:var(--muted);font-size:.72rem;font-weight:800;letter-spacing:.11em;text-transform:uppercase;margin:12px 0 8px}
.live-settings{
  margin-top:18px;
  padding:14px 16px;
  background:rgba(255,253,249,.72);
  border:1px solid var(--line);
  border-radius:18px;
  box-shadow:var(--shadow);
  backdrop-filter:blur(10px);
}
.stage-accordion-status{font-size:.68rem;font-weight:850;letter-spacing:.08em;color:var(--muted)}
[data-testid="stExpander"] summary{min-height:48px}
[data-testid="stExpander"] summary p{line-height:1.2!important}
@media(max-width:1100px){.stage-strip{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:900px){.qc-guide{grid-template-columns:1fr}.release-gates{grid-template-columns:1fr}.brand-title{font-size:1.6rem}}
@media(max-width:700px){.stage-strip{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>""", unsafe_allow_html=True)

apply_dashboard_theme()

REQUIRED_SECRET_NAMES = (
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "UNSPLASH_ACCESS_KEY",
    "HF_TOKEN",
    "PEXELS_API_KEY",
    "OPENROUTER_API_KEY",
    "SERPAPI_API_KEY",
    "PIXABAY_API_KEY",
    "OPENALEX_API_KEY",
    "OLLAMA_BASE_URL",
    # Remote-only controls/credentials. These are loaded only when present
    # in Streamlit Secrets; the local .env/file-based paths remain untouched.
    "YOUTUBE_TOKEN_JSON",
    "VSF_REMOTE_MODE",
)


def load_streamlit_secrets_into_runtime() -> set[str]:
    loaded: set[str] = set()
    for name in REQUIRED_SECRET_NAMES:
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
            loaded.add(name)

    ultimate_bot._dashboard_loaded_secret_names = set(loaded)
    return set(loaded)



def _remote_startup_guard() -> None:
    """Fail fast on Streamlit when required remote-only credentials are absent."""
    remote_mode = str(os.getenv("VSF_REMOTE_MODE", "")).strip().lower() in {
        "1", "true", "yes", "remote", "cloud", "streamlit", "streamlit_cloud"
    }
    if not remote_mode:
        return
    if not str(os.getenv("YOUTUBE_TOKEN_JSON", "")).strip():
        st.error(
            "Remote mode is enabled, but YOUTUBE_TOKEN_JSON is missing. "
            "Add the complete refreshable token.json content to Streamlit Secrets before starting production."
        )
        st.stop()
    if not any(
        str(os.getenv(name, "")).strip()
        for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY")
    ):
        st.error(
            "Remote mode is enabled, but no script-generation provider key is configured. "
            "Add GEMINI_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY to Streamlit Secrets."
        )
        st.stop()

def check_required_local_assets() -> list[str]:
    base = ultimate_bot.BASE_DIR
    problems: list[str] = []
    brand_dir = getattr(ultimate_bot, "BRAND_ASSETS_DIR", None)
    if brand_dir:
        logo_candidates = [
            os.path.join(brand_dir, "logo.png"),
            os.path.join(brand_dir, "channels4_profile.jpg"),
        ]
        if not any(os.path.exists(path) for path in logo_candidates):
            problems.append("Channel logo asset is missing from brand_assets/.")
    for font_name in ("NotoSansDevanagari-Bold.ttf", "NotoSansTelugu-Bold.ttf"):
        if not os.path.exists(os.path.join(base, font_name)):
            problems.append(f"Language font is missing: {font_name}.")
    for env_key in ("GEMINI_API_KEY", "GROQ_API_KEY"):
        if not os.getenv(env_key):
            problems.append(f"Live provider key is not configured: {env_key}.")
    return problems


def _canonical_runtime_bindings_need_refresh() -> bool:
    """Check whether dashboard runtime globals have drifted from the canonical set."""
    run_robot = (
        getattr(ultimate_bot, "_vsf_canonical_run_robot", None)
        or getattr(ultimate_bot, "run_robot", None)
    )
    canonical = getattr(ultimate_bot, "_vsf_canonical_runtime_bindings", None)
    namespace = getattr(run_robot, "__globals__", None)

    if not callable(run_robot) or not isinstance(canonical, dict) or not canonical:
        return True
    if not isinstance(namespace, dict):
        return True

    for name, canonical_callable in canonical.items():
        if callable(canonical_callable) and namespace.get(name) is not canonical_callable:
            return True
    return False


def initialise_runtime() -> None:
    if not getattr(ultimate_bot, "_dashboard_runtime_initialized", False):
        patch_dashboard_runtime(ultimate_bot)
        patch_story_selection(ultimate_bot)
        patch_quality_control(ultimate_bot)
        install_visual_qa_bridge(visual_runtime)
        from audio_runtime import patch_audio_pipeline
        patch_audio_pipeline(ultimate_bot)
        patch_provider_adapters(ultimate_bot)
        ultimate_bot._dashboard_runtime_initialized = True

    # A full Streamlit rerun must not rebind run_robot globals while the
    # background production worker is active. Doing so replaces the live
    # dashboard progress/visual-review wrappers with the raw factory callables.
    active_controller = st.session_state.get("workflow_controller")
    if active_controller is not None:
        try:
            if active_controller.snapshot().get("thread_alive"):
                return
        except Exception:
            pass

    # The canonical production bindings are installed once, then only restored
    # when a prior dashboard production wrapper has genuinely changed them.
    if _canonical_runtime_bindings_need_refresh():
        bind_dashboard_patches(ultimate_bot)


def _channel_options() -> list[str]:
    configured = os.getenv("CHANNEL_OPTIONS", "").strip()
    if configured:
        values = [item.strip() for item in configured.split(",") if item.strip()]
        if values:
            return values
    try:
        secret_values = str(st.secrets.get("CHANNEL_OPTIONS", "") or "").strip()
    except Exception:
        secret_values = ""
    if secret_values:
        values = [item.strip() for item in secret_values.split(",") if item.strip()]
        if values:
            return values
    return [os.getenv("CHANNEL_NAME", "Primary channel")]


def _init_state() -> None:
    if "workflow_controller" not in st.session_state:
        st.session_state.workflow_controller = DashboardWorkflowController(ultimate_bot)
        try:
            if st.session_state.workflow_controller.restore_ready_upload():
                # The worker may have disappeared with the previous Python
                # process, but the database/artifact pair proves the run reached
                # the manual upload gate. Restore the same production context
                # used by the upload action, not just the visible artifact.
                st.session_state.web_config = dict(
                    getattr(ultimate_bot, "_active_web_config", {}) or {}
                )
                st.session_state.production_started = True
                st.session_state.metadata_loaded_run_id = ""
                st.session_state.metadata_approved = False
                st.session_state.approved_metadata = {}
                st.session_state.metadata_editing = False
        except Exception as exc:
            print(f"[Dashboard] Ready-for-upload recovery unavailable: {type(exc).__name__}: {exc}", flush=True)

    defaults = {
        "candidates": [],
        "retained_topics": [],
        "web_config": {},
        "production_started": False,
        "upload_result": "",
        "upload_mode": "",
        "upload_notice": "",
        "upload_notice_kind": "",
        "confirm_public_upload": False,
        "candidate_page": 0,
        "selected_channel": _channel_options()[0],
        "last_demo_results": {},
        "offline_diagnostics": {},
        "pending_candidate": None,
        "visual_search_queries": "",
        "visual_query_story_key": "",
        "visual_query_suggestions": [],
        "visual_query_field_count": 0,
        "editorial_mode": "Deep Dive",
        "metadata_approved": False,
        "approved_metadata": {},
        "metadata_editing": False,
        "metadata_loaded_run_id": "",
        "metadata_pending_values": None,
        "workspace_mode": "Live",
        "live_format_selection": "",
        "live_topic_selection": "",
        "live_sports_selection": "",
        "live_cricket_scope": "",
        "language_label": next(iter(ultimate_bot.LANGUAGES.values()))["label"] if ultimate_bot.LANGUAGES else "English",
        "visual_pipeline_label": "Option 1 · Current image sourcing",
        "live_path_ready": False,
        "test_menu_selection": "Offline Diagnostics",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _topic_identity(candidate: Dict[str, Any]) -> str:
    story_key = str(candidate.get("story_key") or "").strip().casefold()
    if story_key:
        return story_key
    title = str(candidate.get("title") or "").strip().casefold()
    url = str(candidate.get("story_url") or candidate.get("url") or candidate.get("link") or "").strip().casefold()
    return re.sub(r"[^a-z0-9]+", " ", f"{title} {url}").strip()


def _remember_unpublished_topic(candidate: Dict[str, Any]) -> None:
    if not isinstance(candidate, dict) or not str(candidate.get("title") or "").strip():
        return
    key = _topic_identity(candidate)
    if not key:
        return
    retained = [
        dict(item)
        for item in (st.session_state.get("retained_topics") or [])
        if isinstance(item, dict) and _topic_identity(item) != key
    ]
    copy = dict(candidate)
    copy["retained_from_previous_run"] = True
    retained.insert(0, copy)
    st.session_state.retained_topics = retained[:40]


def reset_run() -> None:
    from dashboard_topic_discovery_runtime import clear_dashboard_discovery_cache

    clear_dashboard_discovery_cache()
    controller: DashboardWorkflowController = st.session_state.workflow_controller
    before_reset = controller.snapshot()
    selected_story = before_reset.get("selected_story") or {}
    if selected_story and not str(before_reset.get("uploaded_video_id") or "").strip():
        _remember_unpublished_topic(selected_story)
    controller.reset()
    for key, value in {
        "candidates": [],
        "web_config": {},
        "production_started": False,
        "upload_result": "",
        "upload_mode": "",
        "upload_notice": "",
        "upload_notice_kind": "",
        "confirm_public_upload": False,
        "candidate_page": 0,
        # final_title/final_description/final_comment belong to Streamlit widgets.
        # They are repopulated safely before widget instantiation on the next rerun.
        "pending_candidate": None,
        "visual_search_queries": "",
        "visual_query_story_key": "",
        "visual_query_suggestions": [],
        "visual_query_field_count": 0,
        "metadata_approved": False,
        "approved_metadata": {},
        "metadata_editing": False,
        "metadata_loaded_run_id": "",
        "metadata_pending_values": None,
    }.items():
        st.session_state[key] = value

DEEP_DIVE_TOPICS = (
    "national_global_affairs",
    "technology",
    "business_finance",
    "health_lifestyle",
    "entertainment",
    "viral_phenomenon",
    "regional_state_news",
)
TOP_FIVE_TOPICS = (
    "national_global_affairs",
    "technology",
    "business_finance",
    "entertainment",
    "viral_phenomenon",
)


def category_options(format_mode: str, editorial_mode: str = "Deep Dive") -> Dict[str, str]:
    mode = re.sub(r"[^a-z0-9]+", " ", str(editorial_mode or "").strip().lower()).strip()
    keys = TOP_FIVE_TOPICS if mode in {"top five", "top 5"} else DEEP_DIVE_TOPICS
    output: Dict[str, str] = {}
    for key in keys:
        cfg = ultimate_bot.CONTENT_CATEGORIES.get(key)
        if not cfg:
            continue
        if format_mode == "top5" and not cfg.get("usable_top5", True) and key != "sports":
            continue
        if format_mode == "regular" and not cfg.get("usable_regular", True):
            continue
        label = cfg.get("label", key)
        if key == "sports":
            label = "Sports & Niche Sports"
        output[label] = key
    return output



def _selected_visual_pipeline() -> str:
    """Return the user-selected visual production architecture."""
    label = str(
        st.session_state.get("visual_pipeline_label")
        or "Option 1 · Current image sourcing"
    ).strip()
    return "option2_storyboard" if label.startswith("Option 2") else "option1_scrape"


def build_config() -> Dict[str, Any]:
    language_options = {cfg["label"]: key for key, cfg in ultimate_bot.LANGUAGES.items()}
    language_label = st.session_state.get("language_label") or (
        next(iter(language_options)) if language_options else "English"
    )
    language_key = language_options.get(language_label, "english")
    live_format = str(st.session_state.get("live_format_selection") or "").strip()
    topic_label = str(st.session_state.get("live_topic_selection") or "").strip()
    sports_mode = str(st.session_state.get("live_sports_selection") or "").strip()
    cricket_scope = str(st.session_state.get("live_cricket_scope") or "").strip()

    channel = st.session_state.get("selected_channel", _channel_options()[0])

    if live_format == "Sports":
        if sports_mode == "Cricket":
            return {
                "format_mode": "cricket",
                "display_format": "Cricket",
                "editorial_mode": "Cricket",
                "category": "sports_stories_of_day",
                "language": language_key,
                "language_label": language_label,
                "channel": channel,
                "cricket_pipeline": True,
                "visual_pipeline": _selected_visual_pipeline(),
                "cricket_category": cricket_scope or "India / Asia",
                "requested_topic": "",
            }
        if sports_mode == "Niche Sports":
            return {
                "format_mode": "regular",
                "display_format": "Niche Sports",
                "editorial_mode": "Niche Sports",
                "category": "sports",
                "language": language_key,
                "language_label": language_label,
                "channel": channel,
                "cricket_pipeline": False,
                "visual_pipeline": _selected_visual_pipeline(),
                "cricket_category": "",
                "requested_topic": "",
            }
        if sports_mode == "AI":
            return {
                "format_mode": "regular",
                "display_format": "AI",
                "editorial_mode": "AI",
                # AI is a smart-selection mode inside Sports, not a separate
                # production genre. Keep the real category key so downstream
                # metadata/script routing cannot fall back to national affairs.
                "category": "sports",
                "discovery_mode": "ai_sports",
                "language": language_key,
                "language_label": language_label,
                "channel": channel,
                "cricket_pipeline": False,
                "visual_pipeline": _selected_visual_pipeline(),
                "cricket_category": "",
                "requested_topic": "",
            }

    format_mode = "top5" if live_format == "Top 5" else "regular"
    category_key = ""
    if topic_label:
        options = category_options(format_mode, live_format or "Deep Dive")
        category_key = options.get(topic_label, "")
    if not category_key:
        category_key = next(iter(category_options(format_mode, live_format or "Deep Dive").values()), "national_global_affairs")

    return {
        "format_mode": format_mode,
        "display_format": "Top Five" if live_format == "Top 5" else (live_format or "Deep Dive"),
        "editorial_mode": "Top Five" if live_format == "Top 5" else (live_format or "Deep Dive"),
        "category": category_key,
        "language": language_key,
        "language_label": language_label,
        "channel": channel,
        "cricket_pipeline": False,
        "visual_pipeline": _selected_visual_pipeline(),
        "cricket_category": "",
        "requested_topic": "",
    }


def _render_section_header(kicker: str, title: str, subtitle: str = "") -> None:
    st.markdown(
        f"<div class='section-kicker'>{_ui_html(kicker)}</div>"
        f"<div class='section-title'>{_ui_html(title)}</div>"
        + (f"<div class='section-subtitle'>{_ui_html(subtitle)}</div>" if subtitle else ""),
        unsafe_allow_html=True,
    )

def render_header(action_mode: str) -> None:
    titles = {
        "Live Factory": ("Live Factory", "Create, review and release a Short."),
        "Test": ("Test", "Diagnostics, previews and engineering checks."),
        "Channel Statistics": ("Channel Statistics", "Recorded performance and connected-channel totals."),
        "Run Offline Diagnostics": ("Offline Diagnostics", "Safe code and runtime checks with zero provider calls."),
        "Demo Factory": ("Demo Factory", "Controlled tests for factory components."),
        "Final Branding Preview": ("Final Branding Preview", "Inspect the canonical branding compositor."),
    }
    title, subtitle = titles.get(action_mode, ("Dashboard", "Viral Shorts Factory"))

    logo_path = ""
    brand_dir = getattr(ultimate_bot, "BRAND_ASSETS_DIR", None)
    if brand_dir:
        for candidate in ("logo.png", "channels4_profile.jpg"):
            candidate_path = os.path.join(brand_dir, candidate)
            if os.path.isfile(candidate_path):
                logo_path = candidate_path
                break

    left, middle, right = st.columns([0.42, 5.65, 1.15], gap="small")
    with left:
        if logo_path:
            st.image(logo_path, width=48)
        else:
            st.markdown(
                "<div class='studio-logo-fallback'>🎬</div>",
                unsafe_allow_html=True,
            )
    with middle:
        snapshot = (
            st.session_state.workflow_controller.snapshot()
            if "workflow_controller" in st.session_state
            else {}
        )
        status = (
            "RUNNING"
            if snapshot.get("thread_alive")
            else ("DONE" if snapshot.get("completed") else "READY")
        )
        status_class = "live" if status == "RUNNING" else ("done" if status == "DONE" else "")
        st.markdown(
            f"<div class='studio-masthead'>"
            f"<div class='studio-masthead-kicker'>{_ui_html(title)}</div>"
            f"<div class='studio-masthead-title'>Viral Shorts Factory</div>"
            f"<div class='studio-masthead-sub'>{_ui_html(subtitle)}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f"<div class='studio-status {status_class}'>"
            f"<span class='studio-status-dot'></span>"
            f"<div><div class='studio-status-label'>FACTORY</div>"
            f"<div class='studio-status-value'>{_ui_html(status)}</div></div>"
            f"</div>",
            unsafe_allow_html=True,
        )

def _clear_live_downstream() -> None:
    """Clear only choices that depend on the currently selected Live menu."""
    for key in (
        "live_topic_selection",
        "live_sports_selection",
        "live_cricket_scope",
        "live_topic_menu",
        "live_sports_menu",
        "live_cricket_scope_menu",
    ):
        st.session_state[key] = None if key.endswith("_menu") else ""

def _clear_live_run_selection() -> None:
    """Clear headline/production state when a Live path changes."""
    st.session_state.candidates = []
    st.session_state.pending_candidate = None
    st.session_state.production_started = False
    st.session_state.upload_result = ""
    st.session_state.upload_mode = ""
    st.session_state.upload_notice = ""
    st.session_state.upload_notice_kind = ""
    st.session_state.metadata_approved = False
    st.session_state.approved_metadata = {}
    st.session_state.metadata_editing = False
    st.session_state.metadata_loaded_run_id = ""
    st.session_state.candidate_page = 0
    st.session_state.visual_query_story_key = ""
    st.session_state.visual_query_suggestions = []
    st.session_state.visual_query_field_count = 0


def render_workspace_navigation() -> str:
    options = ["Live", "Test"]
    current = st.session_state.get("workspace_mode", "Live")
    if current not in options:
        current = "Live"

    st.sidebar.markdown("<div class='sidebar-kicker'>Workspace</div>", unsafe_allow_html=True)
    selected = st.sidebar.pills(
        "Workspace",
        options,
        selection_mode="single",
        key="workspace_mode",
        label_visibility="collapsed",
        width="stretch",
    )
    return selected or current


def _reset_live_navigation() -> None:
    """Return the Live selector to its first decision without touching production state."""
    for key in (
        "live_format_selection",
        "live_topic_selection",
        "live_sports_selection",
        "live_cricket_scope",
        "live_format_menu",
        "live_topic_menu",
        "live_sports_menu",
        "live_cricket_scope_menu",
    ):
        st.session_state[key] = None if key.endswith("_menu") else ""
    st.session_state["live_path_ready"] = False
    _clear_live_run_selection()


def render_live_navigation() -> Dict[str, Any]:
    """Render Live choices with staged disclosure: only the next decision stays expanded."""
    st.session_state["live_path_ready"] = False
    _render_section_header(
        "Live",
        "Build a Short",
        "Choose one decision at a time. Completed choices collapse into the path.",
    )

    live_format = str(st.session_state.get("live_format_selection") or "").strip()
    topic_label = str(st.session_state.get("live_topic_selection") or "").strip()
    sports_mode = str(st.session_state.get("live_sports_selection") or "").strip()
    cricket_scope = str(st.session_state.get("live_cricket_scope") or "").strip()

    if not live_format:
        st.markdown(
            "<div class='choice-kicker'>01 · Format</div>",
            unsafe_allow_html=True,
        )
        format_choice = st.pills(
            "Live format",
            ["Deep Dive", "Top 5", "Sports"],
            selection_mode="single",
            default=None,
            key="live_format_menu",
            required=False,
            label_visibility="collapsed",
            width="stretch",
        )
        if format_choice:
            st.session_state.live_format_selection = format_choice
            _clear_live_downstream()
            _clear_live_run_selection()
            st.rerun()
        st.caption("Start with the format.")
        return build_config()

    path_parts = [live_format]

    if live_format in {"Deep Dive", "Top 5"}:
        path_parts.append(topic_label)
        format_mode = "top5" if live_format == "Top 5" else "regular"
        options = category_options(format_mode, live_format)
        if not topic_label:
            st.markdown(
                "<div class='path-summary'><span class='path-check'>✓</span><span class='path-label'>" + _ui_html(live_format) + "</span><span class='path-summary-copy'>Format selected</span></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<div class='choice-kicker'>02 · Topic</div>",
                unsafe_allow_html=True,
            )
            topic_choice = st.pills(
                "Topic",
                list(options.keys()),
                selection_mode="single",
                default=None,
                key="live_topic_menu",
                label_visibility="collapsed",
                width="stretch",
                wrap=True,
            )
            if topic_choice:
                st.session_state.live_topic_selection = topic_choice
                _clear_live_run_selection()
                st.rerun()

    elif live_format == "Sports":
        if not sports_mode:
            st.markdown(
                "<div class='choice-kicker'>01 · Format</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div class='path-summary'><div><span class='path-check'>✓</span><span class='path-label'>{_ui_html(live_format)}</span></div></div>",
                unsafe_allow_html=True,
            )
            path_controls = st.columns([6.5, 1], gap="small")
            with path_controls[0]:
                st.caption("Sports selected · choose the sports lane below.")
            with path_controls[1]:
                if st.button("Change", key="change_live_format_sports", width="stretch"):
                    _reset_live_navigation()
                    st.rerun()
            st.markdown(
                "<div class='choice-kicker'>02 · Sports lane</div>",
                unsafe_allow_html=True,
            )
            sports_choice = st.pills(
                "Sports mode",
                ["Cricket", "Niche Sports", "AI"],
                selection_mode="single",
                default=None,
                key="live_sports_menu",
                label_visibility="collapsed",
                width="stretch",
            )
            if sports_choice:
                st.session_state.live_sports_selection = sports_choice
                st.session_state.live_cricket_scope = ""
                st.session_state.live_cricket_scope_menu = None
                _clear_live_run_selection()
                st.rerun()
        else:
            path_parts.append(sports_mode)
            if sports_mode == "Cricket":
                if not cricket_scope:
                    st.markdown(
                        "<div class='choice-kicker'>03 · Cricket scope</div>",
                        unsafe_allow_html=True,
                    )
                    cricket_choice = st.pills(
                        "Cricket scope",
                        ["India / Asia", "Global"],
                        selection_mode="single",
                        default=None,
                        key="live_cricket_scope_menu",
                        label_visibility="collapsed",
                        width="stretch",
                    )
                    if cricket_choice:
                        st.session_state.live_cricket_scope = cricket_choice
                        _clear_live_run_selection()
                        st.rerun()
                else:
                    path_parts.append(cricket_scope)

    final_path_ready = (
        bool(topic_label) if live_format in {"Deep Dive", "Top 5"} else
        bool(sports_mode) and (sports_mode != "Cricket" or bool(cricket_scope))
    )
    if final_path_ready:
        st.session_state["live_path_ready"] = True
        st.markdown(
            f"<div class='path-ready'>"
            f"<span class='path-ready-dot'>✓</span>"
            f"<span class='path-ready-copy'><b>Path ready</b><span class='path-ready-value'>{' · '.join(_ui_html(part) for part in path_parts if part)}</span></span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button("Change path", key="change_live_path_ready", width="content"):
            _reset_live_navigation()
            st.rerun()

        with st.popover("⚙ Settings", width="stretch"):
            st.caption("Optional production settings")
            columns = st.columns(3, gap="medium")
            language_options = {cfg["label"]: key for key, cfg in ultimate_bot.LANGUAGES.items()}
            language_labels = list(language_options.keys())
            current_language = st.session_state.get("language_label") or (language_labels[0] if language_labels else "English")
            if current_language not in language_labels and language_labels:
                current_language = language_labels[0]
            with columns[0]:
                st.selectbox(
                    "Language",
                    language_labels or ["English"],
                    index=language_labels.index(current_language) if language_labels else 0,
                    key="language_label",
                )
            channels = _channel_options()
            current_channel = st.session_state.get("selected_channel") or channels[0]
            if current_channel not in channels:
                current_channel = channels[0]
            with columns[1]:
                st.selectbox(
                    "Channel",
                    channels,
                    index=channels.index(current_channel),
                    key="selected_channel",
                )
            visual_pipeline_labels = [
                "Option 1 · Current image sourcing",
                "Option 2 · AI editorial storyboard",
            ]
            current_visual_pipeline = st.session_state.get("visual_pipeline_label") or visual_pipeline_labels[0]
            if current_visual_pipeline not in visual_pipeline_labels:
                current_visual_pipeline = visual_pipeline_labels[0]
            with columns[2]:
                st.selectbox(
                    "Visual pipeline",
                    visual_pipeline_labels,
                    index=visual_pipeline_labels.index(current_visual_pipeline),
                    key="visual_pipeline_label",
                )

        config = build_config()
        st.markdown(
            f"<div class='selection-rail'>"
            f"<span class='meta-chip'>Format · {_ui_html(config.get('display_format'))}</span>"
            f"<span class='meta-chip'>Language · {_ui_html(config.get('language_label'))}</span>"
            f"<span class='meta-chip'>Channel · {_ui_html(config.get('channel'))}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        return config

    return build_config()

def render_stage_progress(snapshot: Dict[str, Any]) -> None:
    stages = [
        (item["label"], item["key"])
        for item in LIVE_STAGE_SPEC
    ]
    current = str(snapshot.get("stage") or "idle").strip()
    percent = max(0, min(100, int(snapshot.get("percent", 0) or 0)))
    current_key = {"script": "script_review", "visuals": "visual_approval"}.get(current, current)
    bounds = {
        item["key"]: (item["min_percent"], item["max_percent"])
        for item in LIVE_STAGE_SPEC
    }
    if current == "error":
        current_key = next(
            (key for key, (lo, hi) in bounds.items() if lo <= percent <= hi),
            "qc",
        )

    active_index = next(
        (i for i, (_label, key) in enumerate(stages) if key == current_key),
        -1,
    )
    if snapshot.get("completed"):
        current_key = "qc"
        active_index = len(stages) - 1

    label_map = dict((key, label) for label, key in stages)
    current_label = label_map.get(current_key, "Ready")
    message = _ui_text(snapshot.get("message"), "Ready.")
    if current == "error":
        message = _ui_text(
            snapshot.get("error") or message,
            "The run stopped before completion.",
        )

    nodes = []
    for index, (label, key) in enumerate(stages):
        if snapshot.get("completed") or (active_index >= 0 and index < active_index):
            node_state = "done"
            icon = "✓"
            state_label = "Done"
        elif key == current_key:
            node_state = "active-error" if current == "error" else "active"
            icon = "!"
            state_label = "Stopped" if current == "error" else "Current"
        else:
            node_state = "next"
            icon = str(index + 1)
            state_label = "Next"

        connector = ""
        if index < len(stages) - 1:
            connector_state = "done" if (
                snapshot.get("completed")
                or (active_index >= 0 and index < active_index)
            ) else ""
            connector = f"<div class='workflow-connector {connector_state}'></div>"

        nodes.append(
            f"<div class='workflow-node {node_state}' title='{_ui_html(label)}'>"
            f"<div class='workflow-dot'>{icon}</div>"
            f"<div class='workflow-node-copy'>"
            f"<div class='workflow-node-name'>{_ui_html(label)}</div>"
            f"<div class='workflow-node-state'>{state_label}</div>"
            f"</div>"
            f"</div>{connector}"
        )

    status_word = "RUNNING" if snapshot.get("thread_alive") else (
        "ERROR" if current == "error" else ("READY" if active_index < 0 else "WAITING")
    )
    step_number = max(1, active_index + 1)
    st.markdown(
        f"<div class='workflow-shell'>"
        f"<div class='workflow-topline'>"
        f"<div><span class='workflow-kicker'>WORKFLOW</span>"
        f"<span class='workflow-current'>{_ui_html(current_label)}</span></div>"
        f"<div class='workflow-percent'>{percent}%</div>"
        f"</div>"
        f"<div class='workflow-rail'>{''.join(nodes)}</div>"
        f"<div class='workflow-bottomline'>"
        f"<span class='workflow-status-pill {('error' if current == 'error' else 'live' if snapshot.get('thread_alive') else '')}'>"
        f"{_ui_html(status_word)}"
        f"</span>"
        f"<span class='workflow-message'>{_ui_html(message)}</span>"
        f"<span class='workflow-step'>Step {step_number} / {len(stages)}</span>"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    controller = snapshot.get("_controller") or st.session_state.workflow_controller
    if current_key == "research":
        render_research_summary(snapshot)
    elif current_key == "script_review":
        render_script_visual_query_review(controller, snapshot)
    elif current_key == "audio":
        render_audio_preview(snapshot)
    elif current_key == "visual_approval":
        if snapshot.get("visual_review_required"):
            render_visual_review(controller, snapshot)
        else:
            items = _visual_items(snapshot)
            if items:
                ready = sum(1 for item in items if item.get("qc_passed"))
                st.caption(f"{ready}/{len(items)} visuals ready")
    elif current_key == "render":
        render_generated_outputs(snapshot)
        render_console(snapshot)
    elif current_key == "qc":
        render_upload_panel(controller, snapshot)
        if snapshot.get("stage") == "error":
            st.error(snapshot.get("error") or "The run stopped with an error.")
        elif snapshot.get("completed"):
            render_logs(snapshot)

def _script_text(script_data: Dict[str, Any]) -> str:
    scenes = script_data.get("script", [])
    if not isinstance(scenes, list):
        return ""
    blocks = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        voiceover = _ui_text(scene.get("voiceover", ""))
        if voiceover:
            blocks.append(f"Scene {index}\n{voiceover}")
    return "\n\n".join(blocks)


def _ensure_visual_query_plan(pending_candidate: Dict[str, Any], config: Dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Generate the editable ranked query list once per selected story."""
    title = str(pending_candidate.get("title") or "").strip()
    story_text = str(
        pending_candidate.get("text")
        or pending_candidate.get("summary")
        or pending_candidate.get("description")
        or ""
    ).strip()
    story_key = hashlib.sha1(
        f"{title}|{pending_candidate.get('story_url','')}|{story_text[:1200]}".encode("utf-8")
    ).hexdigest()[:16]

    if st.session_state.get("visual_query_story_key") != story_key:
        try:
            from manual_visual_query_runtime import generate_visual_query_suggestions
            suggestions = generate_visual_query_suggestions(
                title,
                story_text,
                category=str(config.get("category") or pending_candidate.get("recommended_category") or ""),
                max_queries=5,
            )
        except Exception:
            suggestions = []
        suggestions = [dict(item) for item in (suggestions or []) if str(item.get("query") or "").strip()]
        if not suggestions:
            suggestions = [{
                "query": title,
                "source_hint": "Configured image source",
                "reason": "Fallback search term from the selected story headline.",
            }]
        st.session_state.visual_query_story_key = story_key
        st.session_state.visual_query_suggestions = suggestions
        st.session_state.visual_query_field_count = len(suggestions)
        for index, suggestion in enumerate(suggestions):
            st.session_state[f"visual_query_field_{story_key}_{index}"] = str(
                suggestion.get("query") or ""
            ).strip()
    else:
        suggestions = list(st.session_state.get("visual_query_suggestions") or [])
        count = int(st.session_state.get("visual_query_field_count") or len(suggestions) or 1)
        for index in range(count):
            key = f"visual_query_field_{story_key}_{index}"
            if key not in st.session_state:
                value = suggestions[index].get("query", "") if index < len(suggestions) else ""
                st.session_state[key] = value

    return story_key, list(st.session_state.get("visual_query_suggestions") or [])


def render_script_visual_query_review(
    controller: DashboardWorkflowController,
    snapshot: Dict[str, Any],
) -> None:
    script_data = snapshot.get("script_data") or {}
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not isinstance(scenes, list) or not scenes:
        st.warning("The script is not available for review yet.")
        st.info(
            "The run is paused at script review. Once the script payload is available, "
            "the review appears here; PowerShell remains active while it waits."
        )
        return

    run_id = str(snapshot.get("run_id") or "current-run").strip() or "current-run"
    visual_pipeline = str(snapshot.get("visual_pipeline") or "option1_scrape").strip()
    if visual_pipeline == "option2_storyboard":
        _render_section_header(
            "Step 04 · Script review",
            "Review the narration",
            "Check the story and title before the original storyboard is built. You can edit the narration here when needed.",
        )
    else:
        _render_section_header(
            "Step 04 · Script review",
            "Review the narration",
            "Check the story, evidence status and title choice before narration starts. Editing is optional; unchanged scripts continue without another AI review pass.",
        )

    evidence_pack = script_data.get("research_evidence_pack") or {}
    evidence_counts = evidence_pack.get("counts") if isinstance(evidence_pack, dict) else {}
    evidence_counts = evidence_counts if isinstance(evidence_counts, dict) else {}
    sources = int(
        script_data.get("research_source_count")
        or evidence_counts.get("usable_sources")
        or 0
    )
    domains = int(
        script_data.get("research_distinct_domains")
        or evidence_counts.get("independent_domains")
        or 0
    )
    corroborated = int(evidence_counts.get("corroborated_claims") or 0)
    conflicts = int(evidence_counts.get("conflicted_claims") or 0)

    metric_cols = st.columns(4, gap="small")
    metric_cols[0].metric("Sources", sources)
    metric_cols[1].metric("Independent", domains)
    metric_cols[2].metric("Corroborated", corroborated)
    metric_cols[3].metric("Conflicts", conflicts)

    originality = script_data.get("originality_overlap")
    critique = script_data.get("originality_critique")
    unsupported = (
        critique.get("unsupported_claims")
        if isinstance(critique, dict)
        else []
    ) or []
    originality_passed = (
        originality.get("passed")
        if isinstance(originality, dict)
        else None
    )
    status_parts = []
    if isinstance(originality_passed, bool):
        status_parts.append(
            "Originality passed" if originality_passed else "Originality needs attention"
        )
    if unsupported:
        status_parts.append(f"{len(unsupported)} unsupported claim(s) flagged")
    if status_parts:
        st.caption(" · ".join(status_parts))
    if conflicts:
        st.warning(
            "Some research claims conflict. The script should attribute or qualify those points rather than presenting them as settled facts."
        )
    if unsupported:
        with st.expander("Factual review notes", expanded=False):
            for item in unsupported[:6]:
                st.markdown(f"- {_ui_text(item)}")

    research_sources = script_data.get("research_sources") or []
    if research_sources:
        with st.expander("Open research sources", expanded=False):
            for source in research_sources[:5]:
                if not isinstance(source, dict):
                    continue
                source_title = _ui_text(
                    source.get("title") or source.get("source_name") or source.get("publisher"),
                    "Research source",
                )
                source_url = str(source.get("url") or "").strip()
                publisher = _ui_text(
                    source.get("publisher") or source.get("source_name") or "",
                    "",
                )
                if source_url.startswith(("http://", "https://")):
                    st.link_button(
                        source_title,
                        source_url,
                        width="stretch",
                    )
                    if publisher:
                        st.caption(f"Source · {publisher}")
                else:
                    st.caption(source_title)

    titles = [
        str(title or "").strip()
        for title in (script_data.get("titles") or [])
        if str(title or "").strip()
    ]
    if not titles:
        titles = [str(script_data.get("title") or "Selected story").strip() or "Selected story"]
    try:
        recommended_index = int(script_data.get("recommended_title_index", 1))
    except (TypeError, ValueError):
        recommended_index = 1
    recommended_index = max(1, min(recommended_index, len(titles)))

    edit_mode = st.toggle(
        "Edit script before approval",
        value=False,
        key=f"script_review_edit_{run_id}",
        help="Edit narration or title candidates only when you see something worth changing.",
    )

    with st.form(key=f"script_review_{run_id}"):
        working_titles = list(titles[:3])
        if edit_mode:
            st.markdown("#### Title candidates")
            working_titles = [
                st.text_input(
                    f"Title {index}",
                    value=title,
                    key=f"script_review_title_{run_id}_{index}",
                )
                for index, title in enumerate(working_titles, 1)
            ]
        else:
            st.markdown("#### Title candidates")
            for index, title in enumerate(working_titles, 1):
                marker = " · recommended" if index == recommended_index else ""
                st.caption(f"{index}. {title}{marker}")

        clean_working_titles = [
            str(title or "").strip()
            for title in working_titles
            if str(title or "").strip()
        ]
        if not clean_working_titles:
            clean_working_titles = ["Selected story"]

        selected_title = st.selectbox(
            "Working title",
            options=clean_working_titles,
            index=max(
                0,
                min(
                    recommended_index - 1,
                    len(clean_working_titles) - 1,
                ),
            ),
            key=f"script_review_title_choice_{run_id}",
        )
        selected_index = clean_working_titles.index(selected_title) + 1

        angle = str(script_data.get("editorial_angle") or "").strip()
        if edit_mode:
            angle = st.text_area(
                "Editorial angle",
                value=angle,
                height=84,
                key=f"script_review_angle_{run_id}",
                help="Keep this aligned with what the evidence actually supports.",
            )
        elif angle:
            st.caption(f"Editorial angle · {angle}")

        voiceovers = []
        visual_edits = []
        st.markdown("#### Narration")
        if edit_mode:
            for index, scene in enumerate(scenes, 1):
                if not isinstance(scene, dict):
                    continue
                with st.container(border=True):
                    st.caption(f"Slide {index:02d} visual identity")
                    primary_entity = st.text_input(
                        "Primary entity",
                        value=str(scene.get("primary_entity") or "").strip(),
                        key=f"script_review_entity_{run_id}_{index}",
                    )
                    visual_query = st.text_input(
                        "Visual search query",
                        value=str(
                            scene.get("specific_search_prompt")
                            or scene.get("manual_visual_query")
                            or ""
                        ).strip(),
                        key=f"script_review_visual_query_{run_id}_{index}",
                        help="Use this to correct an ambiguous identity before visuals are fetched.",
                    )
                    visual_intent = st.text_input(
                        "Visual intent",
                        value=str(scene.get("visual_intent") or "").strip(),
                        key=f"script_review_visual_intent_{run_id}_{index}",
                    )
                    voiceovers.append(
                        st.text_area(
                            f"Slide {index:02d} narration",
                            value=str(scene.get("voiceover") or "").strip(),
                            height=120,
                            key=f"script_review_voice_{run_id}_{index}",
                        )
                    )
                    visual_edits.append(
                        {
                            "primary_entity": primary_entity,
                            "specific_search_prompt": visual_query,
                            "visual_intent": visual_intent,
                        }
                    )
        else:
            for index, scene in enumerate(scenes, 1):
                if not isinstance(scene, dict):
                    continue
                voiceover = str(scene.get("voiceover") or "").strip()
                if not voiceover:
                    continue
                with st.container(border=True):
                    st.markdown(
                        f"<div class='story-rank'>SLIDE {index:02d}</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<div style='font-size:.92rem;line-height:1.6;margin:7px 0 4px;overflow-wrap:anywhere'>{_ui_html(voiceover)}</div>",
                        unsafe_allow_html=True,
                    )
                    role = _ui_text(scene.get("narrative_role"), "")
                    if role:
                        st.caption(f"Beat · {role}")

        submitted = st.form_submit_button(
            "Save edits & approve script" if edit_mode else "Approve script & continue",
            type="primary",
            width="stretch",
        )

    if not submitted:
        return

    reviewed = dict(script_data)
    reviewed["titles"] = clean_working_titles[:3]
    reviewed["recommended_title_index"] = selected_index
    reviewed["editorial_angle"] = angle

    if edit_mode:
        review_scenes = []
        voice_index = 0
        for scene in scenes:
            if not isinstance(scene, dict):
                continue
            updated = dict(scene)
            updated["voiceover"] = (
                voiceovers[voice_index]
                if voice_index < len(voiceovers)
                else str(scene.get("voiceover") or "")
            )
            metadata_edit = (
                visual_edits[voice_index]
                if voice_index < len(visual_edits)
                else {}
            )
            visual_changed = False
            for field in ("primary_entity", "specific_search_prompt", "visual_intent"):
                incoming_value = str(metadata_edit.get(field) or "").strip()
                if incoming_value != str(scene.get(field) or "").strip():
                    visual_changed = True
                updated[field] = incoming_value
            if visual_changed:
                updated["human_visual_metadata_override"] = True
                if updated.get("specific_search_prompt"):
                    updated["manual_visual_query"] = updated["specific_search_prompt"]
                    updated["manual_visual_query_source"] = "dashboard_script_review"
            review_scenes.append(updated)
            voice_index += 1
        reviewed["script"] = review_scenes

    ok, message = controller.submit_script_review(reviewed)
    if ok:
        st.success(message)
        st.rerun()
    else:
        st.error(message)

def _visual_items(snapshot: Dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for index, package in enumerate(snapshot.get("visual_packages") or [], 1):
        layer = package[0] if isinstance(package, list) and package else package
        if not isinstance(layer, dict):
            layer = {}

        path = str(layer.get("image") or "").strip()
        missing = not path or not os.path.isfile(path)
        verified = bool(layer.get("visual_verified", False))
        bank = []
        for bank_item in layer.get("visual_asset_bank") or []:
            if not isinstance(bank_item, dict):
                continue
            bank_path = str(bank_item.get("path") or "").strip()
            if bank_path and os.path.isfile(bank_path):
                bank.append(dict(bank_item))
        # This is an explicit human-QC checkpoint. AI identity verdicts,
        # context suitability and soft resolution remain visible as diagnostics,
        # but they must not disable a present image that the reviewer can inspect.
        unused_verified = [dict(item) for item in bank]
        items.append(
            {
                "index": index,
                "path": path if not missing else "",
                "missing": missing,
                "source": str(layer.get("source_type") or "visual"),
                "visual_type": str(layer.get("visual_type") or "visual"),
                "verified": verified,
                "qc_passed": not missing,
                "qc_reason": (
                    "Rendered image file is missing from the dashboard host."
                    if missing
                    else str(
                        layer.get("visual_qc_block_reason")
                        or layer.get("visual_rescue_reason")
                        or "Visual has no verified entity QC verdict."
                    ).strip()
                ),
                "qc_attempts": int(
                    layer.get("visual_verification_attempts")
                    or sum(
                        int(stat.get("qa_requests") or 0)
                        for stat in (layer.get("visual_manual_pool_query_stats") or [])
                        if isinstance(stat, dict)
                    )
                ),
                "rejection_counts": dict(layer.get("visual_rejection_counts") or {}),
                "manual_query": str(layer.get("manual_visual_query") or "").strip(),
                "query_used": str(layer.get("visual_query_used") or "").strip(),
                "rescue_reason": str(layer.get("visual_rescue_reason") or "").strip(),
                "crop_zoom": float(layer.get("visual_crop_zoom") or 1.0),
                "crop_x": float(layer.get("visual_crop_x") if layer.get("visual_crop_x") is not None else 0.5),
                "crop_y": float(layer.get("visual_crop_y") if layer.get("visual_crop_y") is not None else 0.5),
                "crop_box": dict(layer.get("visual_crop_box") or {}),
                "original_path": str(layer.get("visual_original_path") or "").strip(),
                "manual_pool_mode": bool(layer.get("visual_manual_pool_mode", False)),
                "manual_pool_size": int(layer.get("visual_manual_pool_size") or 0),
                "manual_pool_query_stats": list(layer.get("visual_manual_pool_query_stats") or []),
                "search_options": [
                    dict(option)
                    for option in (layer.get("visual_search_options") or [])
                    if isinstance(option, dict)
                    and str(option.get("path") or "").strip()
                    and os.path.isfile(str(option.get("path") or "").strip())
                ],
                "bank": unused_verified,
                "all_bank": bank,
            }
        )
    return items



def _recommended_shorts_crop_box(img, aspect_ratio=None) -> dict[str, int]:
    """Return a large, centered 9:16 starter frame in original-image pixels."""
    width = max(2, int(getattr(img, "width", 2)))
    height = max(2, int(getattr(img, "height", 2)))
    target_aspect = 9 / 16
    if isinstance(aspect_ratio, tuple) and len(aspect_ratio) == 2:
        try:
            target_aspect = float(aspect_ratio[0]) / float(aspect_ratio[1])
        except (TypeError, ValueError, ZeroDivisionError):
            target_aspect = 9 / 16

    usable_width = max(2, int(round(width * 0.60)))
    usable_height = max(2, int(round(height * 0.60)))
    if usable_width / max(1, usable_height) > target_aspect:
        crop_height = usable_height
        crop_width = max(2, int(round(crop_height * target_aspect)))
    else:
        crop_width = usable_width
        crop_height = max(2, int(round(crop_width / target_aspect)))

    crop_width = min(width, crop_width)
    crop_height = min(height, crop_height)
    return {
        "left": max(0, int(round((width - crop_width) / 2))),
        "top": max(0, int(round((height - crop_height) / 2))),
        "width": crop_width,
        "height": crop_height,
    }


@st.dialog("Crop / reframe selected image", width="large")
def _render_crop_dialog(
    controller: DashboardWorkflowController,
    snapshot: Dict[str, Any],
    target: str,
) -> None:
    target = str(target or "").strip()
    source_path = ""
    stored_box: dict[str, Any] = {}
    crop_label = "selected image"
    slide_index = None
    asset_hash = ""

    if target.startswith("slide:"):
        try:
            slide_index = int(target.split(":", 1)[1])
        except (TypeError, ValueError):
            slide_index = None
        item = next((row for row in _visual_items(snapshot) if row.get("index") == slide_index), None)
        if item:
            source_path = str(item.get("original_path") or item.get("path") or "").strip()
            stored_box = dict(item.get("crop_box") or {})
            crop_label = f"slide {slide_index}"
    elif target.startswith("asset:"):
        asset_hash = target.split(":", 1)[1].strip()
        candidates = [
            item for item in (snapshot.get("visual_pool") or []) if isinstance(item, dict)
        ]
        for group in snapshot.get("visual_search_groups") or []:
            candidates.extend(
                item for item in (group.get("items") or []) if isinstance(item, dict)
            )
        asset = next(
            (item for item in candidates if str(item.get("hash") or "").strip() == asset_hash),
            None,
        )
        if asset:
            source_path = str(asset.get("original_path") or asset.get("path") or "").strip()
            stored_box = dict(asset.get("crop_box") or {})
            crop_label = "selected pool image"

    if not source_path or not os.path.isfile(source_path):
        st.error("The original image is no longer available.")
        if st.button("Close", width="stretch", key=f"close_crop_missing_{target[:32]}"):
            st.session_state["visual_crop_target"] = ""
            st.rerun()
        return

    try:
        from streamlit_cropper import st_cropper as cropper
    except ModuleNotFoundError:
        st.error("Interactive cropping is unavailable in this Python environment.")
        if st.button("Close", width="stretch", key=f"close_crop_unavailable_{target[:32]}"):
            st.session_state["visual_crop_target"] = ""
            st.rerun()
        return

    from PIL import Image

    image = Image.open(source_path).convert("RGB")
    default_coords = None
    try:
        if all(key in stored_box for key in ("left", "top", "width", "height")):
            left = int(stored_box["left"])
            top = int(stored_box["top"])
            width = int(stored_box["width"])
            height = int(stored_box["height"])
            default_coords = (left, left + width, top, top + height)
    except (TypeError, ValueError):
        default_coords = None

    st.caption(
        f"{crop_label}. Drag and resize the frame. The original image remains preserved for future crops."
    )
    crop_mode = st.selectbox(
        "Crop mode",
        ["Shorts 9:16", "Rectangle (free)"],
        key=f"crop_dialog_mode_{snapshot.get('run_id','active')}_{target[:32]}",
    )
    crop_is_shorts = crop_mode == "Shorts 9:16"
    crop_result = cropper(
        img_file=image,
        realtime_update=True,
        default_coords=default_coords,
        box_color="#177fd1",
        aspect_ratio=(9, 16) if crop_is_shorts else None,
        box_algorithm=_recommended_shorts_crop_box if crop_is_shorts else None,
        return_type="box",
        key=f"crop_dialog_{snapshot.get('run_id','active')}_{target[:32]}",
        should_resize_image=True,
        stroke_width=3,
    )
    crop_box = crop_result if isinstance(crop_result, dict) else {}
    crop_preview = None
    if crop_box:
        try:
            crop_preview = _manual_crop_box_to_shorts(
                image,
                crop_box,
                free_size=not crop_is_shorts,
            )
        except (TypeError, ValueError):
            crop_preview = None

    if crop_preview is not None:
        st.image(crop_preview, width=280)

    action_cols = st.columns([1, 1])
    with action_cols[0]:
        if st.button(
            "Apply crop",
            type="primary",
            width="stretch",
            disabled=not isinstance(crop_box, dict) or not crop_box,
            key=f"apply_crop_dialog_{snapshot.get('run_id','active')}_{target[:32]}",
        ):
            mode_value = "free" if not crop_is_shorts else "shorts"
            if slide_index is not None:
                ok, message = controller.crop_visual(
                    slide_index,
                    crop_box=crop_box,
                    crop_mode=mode_value,
                )
            else:
                ok, message = controller.crop_visual_pool_asset(
                    asset_hash,
                    crop_box,
                    crop_mode=mode_value,
                )
            if ok:
                st.session_state["visual_crop_target"] = ""
                st.rerun()
            st.error(message)
    with action_cols[1]:
        if st.button(
            "Close",
            width="stretch",
            key=f"close_crop_dialog_{snapshot.get('run_id','active')}_{target[:32]}",
        ):
            st.session_state["visual_crop_target"] = ""
            st.rerun()


def render_visual_review(controller: DashboardWorkflowController, snapshot: Dict[str, Any]) -> None:
    items = _visual_items(snapshot)
    if not items:
        return

    run_id = str(snapshot.get("run_id") or "active")
    history = snapshot.get("visual_replacement_history") or {}
    pool = [
        dict(item) for item in (snapshot.get("visual_pool") or [])
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]
    search_groups = [
        dict(group) for group in (snapshot.get("visual_search_groups") or [])
        if isinstance(group, dict)
    ]
    # Every retained image that passed AI identity verification is displayed.
    # Provider rights/provenance stay visible as metadata for the human reviewer;
    # they are not an automatic manual-QC acceptance filter.
    available = [
        item for item in pool
        if not bool(item.get("used"))
    ]
    ready_count = sum(1 for item in items if item.get("qc_passed"))
    manual_approved = set(
        int(x) for x in (snapshot.get("visual_manual_approved") or []) if str(x).isdigit()
    )
    active_search_count = sum(
        1 for group in search_groups
        for item in (group.get("items") or [])
        if isinstance(item, dict) and not bool(item.get("used"))
    )

    st.markdown(
        "<div class='section-kicker'>Visual QC</div>"
        "<h2 style='margin-top:0'>Choose from the visual pool</h2>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Every slide already has an image. The pools below collect identity-checked choices from the available image sources. "
        "Assign any unused image to one slide, then reframe it with the cropper when needed."
    )

    metric_cols = st.columns(4, gap="small")
    metric_cols[0].metric("Slides", len(items))
    metric_cols[1].metric("Ready", ready_count)
    metric_cols[2].metric("Pool images", len(available))
    metric_cols[3].metric("New search", active_search_count)

    crop_target = str(st.session_state.get("visual_crop_target") or "").strip()
    if crop_target:
        _render_crop_dialog(controller, snapshot, crop_target)

    st.markdown("### Current slide images")
    for row_start in range(0, len(items), 3):
        row = items[row_start:row_start + 3]
        cols = st.columns(len(row), gap="medium")
        for local_index, item in enumerate(row):
            with cols[local_index]:
                with st.container(border=True):
                    st.markdown(f"<div class='story-rank'>SLIDE {item['index']:02d}</div>", unsafe_allow_html=True)
                    if item.get("missing"):
                        st.error("No image file is available for this slide.", icon="⛔")
                    else:
                        st.image(item["path"], width=240)
                    query = str(item.get("query_used") or item.get("manual_query") or "").strip()
                    source = str(item.get("source") or "").strip()
                    if source or query:
                        caption = source or "visual"
                        if query:
                            caption += f" · {query}"
                        st.caption(caption)
                    if not item.get("qc_passed"):
                        st.caption(item.get("qc_reason") or "This slide still needs a usable image.")
                    if item["index"] in manual_approved:
                        st.success("✓ Manually approved", icon="✅")
                    else:
                        if st.button(
                            "✅ Approve this image",
                            type="primary",
                            width="stretch",
                            disabled=bool(item.get("missing")),
                            key=f"approve_visual_{run_id}_{item['index']}",
                        ):
                            ok, message = controller.approve_visual(item["index"])
                            if ok:
                                st.rerun()
                            st.error(message)

                    with st.popover("Replace image", width="stretch"):
                        with st.form(key=f"replace_visual_form_{run_id}_{item['index']}"):
                            replacement_query = st.text_input(
                                "Search",
                                value=str(item.get("manual_query") or item.get("query_used") or "").strip(),
                                placeholder="e.g. Smriti Mandhana batting",
                                label_visibility="collapsed",
                            )
                            replacement_submitted = st.form_submit_button(
                                "Find up to 10 alternatives",
                                width="stretch",
                            )
                        if replacement_submitted:
                            ok, message = controller.search_visual_options(
                                item["index"],
                                replacement_query,
                            )
                            if ok:
                                st.rerun()
                            st.error(message)

                    replacement_options = [
                        option
                        for option in (item.get("search_options") or [])
                        if isinstance(option, dict)
                        and str(option.get("path") or "").strip()
                        and os.path.isfile(str(option.get("path") or "").strip())
                    ]
                    if replacement_options:
                        with st.expander(
                            f"Replacement options · {len(replacement_options)}",
                            expanded=True,
                        ):
                            option_cols = st.columns(min(3, len(replacement_options)), gap="small")
                            for option_index, option in enumerate(replacement_options, 1):
                                with option_cols[(option_index - 1) % len(option_cols)]:
                                    st.image(option["path"], width=140)
                                    st.caption(
                                        str(option.get("source") or "visual source").strip()
                                    )
                                    if st.button(
                                        "Use",
                                        width="stretch",
                                        key=f"use_search_option_{run_id}_{item['index']}_{option_index}_{str(option.get('hash') or '')[:10]}",
                                    ):
                                        ok, message = controller.replace_visual_from_search_option(
                                            item["index"],
                                            option_index,
                                        )
                                        if ok:
                                            st.rerun()
                                        st.error(message)

                    original_path = str(item.get("original_path") or "").strip()
                    if original_path and os.path.isfile(original_path):
                        if st.button(
                            "Crop",
                            width="stretch",
                            key=f"crop_slide_{run_id}_{item['index']}",
                        ):
                            st.session_state["visual_crop_target"] = f"slide:{item['index']}"
                            st.rerun()


    def render_pool_section(title: str, description: str, assets: list[dict], section_key: str) -> None:
        st.markdown(
            f"<div class='qc-pool-heading'><b>{_ui_html(title)}</b><span>{_ui_html(description)}</span></div>",
            unsafe_allow_html=True,
        )
        if not assets:
            st.caption("No images in this group.")
            return
        for row_start in range(0, len(assets), 3):
            row = assets[row_start:row_start + 3]
            cols = st.columns(len(row), gap="medium")
            for local_index, asset in enumerate(row):
                with cols[local_index]:
                    asset_hash = str(asset.get("hash") or "").strip()
                    path = str(asset.get("path") or "").strip()
                    with st.container(border=True):
                        if path and os.path.isfile(path):
                            st.image(path, width=220)
                        query = str(asset.get("query") or "").strip()
                        source = str(asset.get("source") or "").strip()
                        caption = source or "visual source"
                        if query:
                            caption += f" · {query}"
                        st.caption(caption)
                        choices = ["Choose slide"] + [f"Slide {index}" for index in range(1, len(items) + 1)]
                        target_key = f"pool_target_{run_id}_{section_key}_{asset_hash[:12]}"
                        target = st.selectbox(
                            "Use on slide",
                            choices,
                            index=0,
                            disabled=bool(asset.get("used")),
                            key=target_key,
                            label_visibility="collapsed",
                        )
                        assign_cols = st.columns([1.1, 0.9], gap="small")
                        with assign_cols[0]:
                            if st.button(
                                "Use image",
                                type="primary",
                                width="stretch",
                                disabled=bool(asset.get("used")) or target == "Choose slide",
                                key=f"assign_pool_{run_id}_{section_key}_{asset_hash[:12]}",
                            ):
                                slide_index = int(target.split()[-1])
                                ok, message = controller.assign_visual_pool_asset(asset_hash, slide_index)
                                if ok:
                                    st.rerun()
                                st.error(message)
                        with assign_cols[1]:
                            if st.button(
                                "Crop",
                                width="stretch",
                                key=f"crop_pool_{run_id}_{section_key}_{asset_hash[:12]}",
                            ):
                                st.session_state["visual_crop_target"] = f"asset:{asset_hash}"
                                st.rerun()
                        if bool(asset.get("used")):
                            st.caption(f"Used on slide {int(asset.get('assigned_slide') or 0)}")

    st.markdown("### Available manual-search images")
    render_pool_section(
        "Manual-search image pool",
        "Images returned by your manual search are shown here. AI identity verdicts, context and soft-resolution flags are diagnostics; your visual review decides what is usable.",
        available,
        "verified",
    )

    # All retained images are already shown in the single pool above.
    st.markdown("### Find 10 more images")
    st.caption(
        "Search all available image sources for up to 10 new AI-checked results. "
        "Licensing/provenance is shown as source metadata; you decide what to use."
    )
    with st.form(
        key=f"global_visual_search_form_{run_id}",
        clear_on_submit=True,
        border=True,
    ):
        search_query = st.text_input(
            "New image search",
            placeholder="e.g. Virat Kohli BCCI India, BCCI logo, India women's cricket",
        )
        search_submitted = st.form_submit_button(
            "Search up to 10 new images",
            type="secondary",
            width="stretch",
        )

    if search_submitted:
        ok, message = controller.search_visual_pool(search_query)
        if ok:
            st.success(message)
        else:
            st.error(message)
        search_groups = [
            dict(group)
            for group in (controller.snapshot().get("visual_search_groups") or [])
            if isinstance(group, dict)
        ]

    for group in search_groups:
        group_id = str(group.get("id") or "search")
        group_items = [dict(item) for item in (group.get("items") or []) if isinstance(item, dict)]
        st.markdown(
            f"<div class='qc-pool-heading'><b>Search · {_ui_html(group.get('query',''))}</b><span>{len(group_items)} result(s)</span></div>",
            unsafe_allow_html=True,
        )
        for row_start in range(0, len(group_items), 3):
            row = group_items[row_start:row_start + 3]
            cols = st.columns(len(row), gap="medium")
            for local_index, asset in enumerate(row):
                with cols[local_index]:
                    asset_hash = str(asset.get("hash") or "").strip()
                    path = str(asset.get("path") or "").strip()
                    with st.container(border=True):
                        if path and os.path.isfile(path):
                            st.image(path, width=260)
                        provenance_state = str(asset.get("provenance_status") or "commercial-verified").strip()
                        caption = str(asset.get("source") or "visual source").strip()
                        if provenance_state == "provenance-review":
                            caption += " · licence review"
                        st.caption(caption)
                        used = bool(asset.get("used"))
                        if used:
                            st.caption(f"Used on slide {int(asset.get('assigned_slide') or 0)}")
                        else:
                            choices = ["Choose slide"] + [f"Slide {index}" for index in range(1, len(items) + 1)]
                            target = st.selectbox(
                                "Use on slide",
                                choices,
                                index=0,
                                key=f"search_target_{run_id}_{group_id}_{asset_hash[:12]}",
                                label_visibility="collapsed",
                            )
                            action_cols = st.columns([1.1, 0.9], gap="small")
                            with action_cols[0]:
                                if st.button(
                                    "Use image",
                                    type="primary",
                                    width="stretch",
                                    disabled=target == "Choose slide",
                                    key=f"assign_search_{run_id}_{group_id}_{asset_hash[:12]}",
                                ):
                                    slide_index = int(target.split()[-1])
                                    ok, message = controller.assign_visual_pool_asset(asset_hash, slide_index)
                                    if ok:
                                        st.rerun()
                                    st.error(message)
                            with action_cols[1]:
                                if st.button(
                                    "Crop",
                                    width="stretch",
                                    key=f"crop_search_{run_id}_{group_id}_{asset_hash[:12]}",
                                ):
                                    st.session_state["visual_crop_target"] = f"asset:{asset_hash}"
                                    st.rerun()

    st.markdown("---")
    total_slides = len(items)
    approved_count = len(manual_approved.intersection(set(range(1, total_slides + 1))))
    approve_col, reject_col = st.columns([1.35, 1], gap="medium")
    with approve_col:
        if st.button(
            "✅ Continue after reviewing all slides",
            type="primary",
            width="stretch",
            key="approve_visuals",
            disabled=approved_count != total_slides,
        ):
            ok = controller.approve_visuals()
            if ok:
                st.rerun()
            st.error("Review and individually approve every slide before continuing.")
        unresolved = sum(1 for item in items if not item.get("qc_passed"))
        if unresolved:
            st.caption(f"{unresolved} slide(s) still need a usable image.")
        elif approved_count < total_slides:
            st.caption(
                f"{approved_count}/{total_slides} slides manually approved. "
                "Approve each image above to continue."
            )
        else:
            st.caption("All slides have been individually approved. Continue to final rendering.")
    with reject_col:
        if st.button("Stop production", width="stretch", key="reject_visuals"):
            controller.reject_visuals()
            st.rerun()
    replacement_count = sum(
        len(history.get(str(item["index"])) or history.get(item["index"]) or [])
        for item in items
    )
    if replacement_count:
        st.caption(
            f"Session activity: {replacement_count} replacement change"
            + ("s" if replacement_count != 1 else "")
        )


def render_research_summary(snapshot: Dict[str, Any]) -> None:
    story = snapshot.get("selected_story") or {}
    if not story:
        return
    headline = str(story.get("title") or "").strip()
    source = str(story.get("source_label") or story.get("source") or story.get("publisher") or "").strip()
    url = str(story.get("story_url") or story.get("url") or story.get("link") or "").strip()
    if not any((headline, source, url)):
        return

    _render_section_header("Research", "Selected story")
    with st.container(border=True):
        st.markdown(
            f"<div style='font-size:1.08rem;font-weight:780;line-height:1.4;overflow-wrap:anywhere'>{_ui_html(headline)}</div>",
            unsafe_allow_html=True,
        )
        if source:
            st.caption(f"Source · {source}")
        if url.startswith(("http://", "https://")):
            st.link_button("Open source article", url, width="content")

def render_audio_preview(snapshot: Dict[str, Any]) -> None:
    paths = [
        str(path).strip()
        for path in (snapshot.get("audio_paths") or [])
        if str(path or "").strip()
    ]
    existing = [path for path in paths if os.path.isfile(path)]
    if not existing:
        return

    _render_section_header(
        "Audio output",
        "Voiceover",
        f"{len(existing)} narration track(s) are ready to listen to.",
    )
    cols = st.columns(min(2, len(existing)))
    for index, path in enumerate(existing, 1):
        with cols[(index - 1) % len(cols)]:
            with st.container(border=True):
                st.caption(f"SCENE {index}")
                st.audio(path, format="audio/mpeg")

def render_powershell_widget(snapshot: Dict[str, Any]) -> None:
    """Render the worker's exact stdout/stderr in the collapsible dashboard sidebar."""
    lines = list(snapshot.get("console_lines") or [])
    worker_alive = bool(snapshot.get("thread_alive"))
    stage = str(snapshot.get("stage") or "").strip()
    if worker_alive:
        status = "LIVE"
    elif lines:
        status = "IDLE"
    else:
        status = "WAITING"

    with st.sidebar:
        with st.expander(f"🖥️ PowerShell · {status}", expanded=False):
            st.caption("Live worker output · newest lines appear at the bottom.")
            if lines:
                visible = lines[-100:]
                st.code("\n".join(visible), language="powershell")
                st.caption(f"{len(visible)} lines · stage: {_ui_text(stage, 'ready')}")
            else:
                st.info("No worker output captured yet.")

def render_console(snapshot: Dict[str, Any]) -> None:
    lines = snapshot.get("console_lines") or []
    if not lines:
        return

    # The worker often writes informational lines after a progress line. Search
    # backwards so the dashboard keeps showing the latest known operation instead
    # of making the progress indicator disappear on the next log message.
    operation_percent = None
    operation_label = ""
    for latest in reversed(lines[-100:]):
        upload_match = re.search(r"\[Upload Progress\]\s*(\d+)%", latest)
        render_match = re.search(r"(?:Rendering Video Scenes|Writing video file).*?(\d{1,3})%", latest)
        if upload_match:
            operation_percent = int(upload_match.group(1))
            operation_label = "YouTube upload"
            break
        if render_match:
            operation_percent = int(render_match.group(1))
            operation_label = "Final video render"
            break

    st.markdown("### Live activity")
    if operation_percent is not None:
        st.markdown(f"**{operation_label}** · {operation_percent}%")
        st.progress(max(0.0, min(1.0, operation_percent / 100)))
    # Detailed worker stdout/stderr is shown in the collapsible sidebar PowerShell widget.


def render_logs(snapshot: Dict[str, Any]) -> None:
    logs = snapshot.get("dashboard_logs") or []
    if not logs:
        return
    with st.expander("Technical details", expanded=False):
        for index, message in enumerate(logs):
            prefix = "Latest" if index == len(logs) - 1 else "Done"
            st.caption(f"{prefix} · {message}")

def render_generated_outputs(snapshot: Dict[str, Any]) -> None:
    """Show a compact output dashboard without duplicating dedicated review sections."""
    script_data = snapshot.get("script_data") or {}
    metadata = snapshot.get("final_metadata") or {}
    story = snapshot.get("selected_story") or {}
    title = str(metadata.get("title") or script_data.get("title") or story.get("title") or "").strip()
    description = str(metadata.get("description") or script_data.get("seo_description") or "").strip()
    comment = str(metadata.get("pinned_comment") or script_data.get("pinned_comment") or "").strip()
    visuals = _visual_items(snapshot)
    audio = [
        str(path).strip()
        for path in (snapshot.get("audio_paths") or [])
        if str(path or "").strip() and os.path.isfile(str(path).strip())
    ]
    video_path = str(snapshot.get("video_path") or "").strip()

    if not any((title, script_data, description, comment, visuals, audio, video_path)):
        return

    _render_section_header(
        "Output summary",
        "Your Short",
        "A compact view of what is ready without repeating the review panels above.",
    )
    cols = st.columns(4)
    cols[0].metric("Script", "Ready" if script_data else "Waiting")
    cols[1].metric("Voiceover", f"{len(audio)} tracks" if audio else "Waiting")
    ready_visuals = sum(1 for item in visuals if item.get("qc_passed"))
    cols[2].metric("Visuals", f"{ready_visuals}/{len(visuals)} ready" if visuals else "Waiting")
    cols[3].metric("Final video", "Ready" if video_path and os.path.isfile(video_path) else "Waiting")

    if title or description or comment:
        with st.expander("YouTube metadata", expanded=False):
            if title:
                st.markdown(f"**Title**  \n{title}")
            if description:
                st.text_area(
                    "Description",
                    value=description,
                    height=120,
                    disabled=True,
                    label_visibility="collapsed",
                    key="generated_output_description",
                )
            if comment:
                st.text_area(
                    "Pinned comment",
                    value=comment,
                    height=90,
                    disabled=True,
                    label_visibility="collapsed",
                    key="generated_output_comment",
                )

    if video_path and os.path.isfile(video_path):
        with st.expander("Watch final Short", expanded=False):
            st.video(video_path)

def render_upload_panel(controller: DashboardWorkflowController, snapshot: Dict[str, Any]) -> None:
    video_path = str(snapshot.get("video_path") or "").strip()
    if not upload_ready_for_manual_decision(snapshot):
        return

    uploaded_video_id = str(
        snapshot.get("uploaded_video_id")
        or st.session_state.get("upload_result")
        or ""
    ).strip()
    upload_mode = str(st.session_state.get("upload_mode") or "").strip().lower()
    upload_notice = str(st.session_state.get("upload_notice") or "").strip()
    upload_notice_kind = str(st.session_state.get("upload_notice_kind") or "").strip().lower()

    if uploaded_video_id:
        if upload_notice_kind == "visibility_blocked":
            heading = "YouTube kept the video private"
            copy = upload_notice or "YouTube accepted the upload but did not allow public visibility."
        else:
            heading = "Published to YouTube" if upload_mode == "public" else "Upload complete"
            copy = (
                "The Short is public and the creator comment was submitted."
                if upload_mode == "public"
                else "The Short is stored on YouTube as a private upload."
                if upload_mode == "private"
                else "YouTube has accepted the video for this production run."
            )
        with st.container(border=True):
            st.markdown(
                f"<div class='release-success'>"
                f"<div class='release-success-icon'>✓</div>"
                f"<div class='release-success-copy'>"
                f"<div class='release-success-kicker'>RELEASE COMPLETE</div>"
                f"<div class='release-success-title'>{_ui_html(heading)}</div>"
                f"<div class='release-success-detail'>{_ui_html(copy)}</div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )
            st.markdown("<div class='release-id-label'>VIDEO ID</div>", unsafe_allow_html=True)
            st.code(uploaded_video_id, language="text")
            st.link_button(
                "Open video on YouTube",
                f"https://www.youtube.com/watch?v={uploaded_video_id}",
                width="content",
            )
        return

    script_data = snapshot.get("script_data") or {}
    metadata = snapshot.get("final_metadata") or {}
    run_id = str(snapshot.get("run_id") or "")
    pending_metadata = st.session_state.pop("metadata_pending_values", None)
    if (
        isinstance(pending_metadata, dict)
        and str(pending_metadata.get("run_id") or "").strip() == run_id
    ):
        approved = {
            "title": str(pending_metadata.get("title") or "").strip(),
            "description": str(pending_metadata.get("description") or "").strip(),
            "comment": str(pending_metadata.get("comment") or "").strip(),
        }
        st.session_state["approved_metadata"] = approved
        st.session_state["final_title"] = approved["title"]
        st.session_state["final_description"] = approved["description"]
        st.session_state["final_comment"] = approved["comment"]
        st.session_state["metadata_approved"] = True
        st.session_state["metadata_editing"] = False

    if st.session_state.get("metadata_loaded_run_id") != run_id:
        st.session_state["final_title"] = str(
            metadata.get("title")
            or script_data.get("title")
            or (snapshot.get("selected_story") or {}).get("title")
            or ""
        ).strip()
        st.session_state["final_description"] = str(
            metadata.get("description") or script_data.get("seo_description") or ""
        ).strip()
        st.session_state["final_comment"] = str(
            metadata.get("pinned_comment") or script_data.get("pinned_comment") or ""
        ).strip()
        st.session_state["metadata_loaded_run_id"] = run_id
        st.session_state["approved_metadata"] = {}
        st.session_state["metadata_approved"] = False
        st.session_state["metadata_editing"] = False

    _render_section_header(
        "Final step",
        "Publish",
        "Approve the final metadata, then choose how the Short is published.",
    )

    metadata_approved = bool(st.session_state.get("metadata_approved"))
    approved_metadata = st.session_state.get("approved_metadata") or {}
    if metadata_approved and not isinstance(approved_metadata, dict):
        approved_metadata = {}
    with st.container(border=True):
        if metadata_approved and not st.session_state.get("metadata_editing"):
            st.markdown(
                f"<div class='approved-meta-row'>"
                f"<div><span class='approved-meta-icon'>✓</span>"
                f"<div><div class='approved-meta-kicker'>METADATA APPROVED</div>"
                f"<div class='approved-meta-title'>{_ui_html(st.session_state.get('final_title') or metadata.get('title') or script_data.get('title') or 'Untitled')}</div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )
            action_cols = st.columns([1, 1, 1.35])
            with action_cols[0]:
                st.caption("Title, description and comment are locked.")
            with action_cols[1]:
                with st.popover("View metadata", width="stretch"):
                    st.markdown(f"**Title**  \n{_ui_html(st.session_state.get('final_title') or metadata.get('title') or script_data.get('title') or '')}")
                    st.text_area(
                        "Description",
                        value=str(st.session_state.get("final_description") or metadata.get("description") or ""),
                        height=120,
                        disabled=True,
                        key=f"view_metadata_description_{run_id}",
                    )
                    st.text_area(
                        "Pinned comment",
                        value=str(st.session_state.get("final_comment") or metadata.get("pinned_comment") or ""),
                        height=90,
                        disabled=True,
                        key=f"view_metadata_comment_{run_id}",
                    )
            with action_cols[2]:
                if st.button("Edit metadata", width="stretch", key="edit_metadata"):
                    st.session_state["final_title"] = str(approved_metadata.get("title") or "").strip()
                    st.session_state["final_description"] = str(approved_metadata.get("description") or "").strip()
                    st.session_state["final_comment"] = str(approved_metadata.get("comment") or "").strip()
                    st.session_state["metadata_approved"] = False
                    st.session_state["metadata_editing"] = True
                    st.rerun()
        else:
            st.markdown(
                "<div class='release-section-head'>"
                "<div><span class='release-step-dot'>1</span><b>Metadata</b></div>"
                f"<span class='release-state {'ready' if metadata_approved else 'waiting'}'>"
                f"{'Approved' if metadata_approved else 'Needs approval'}</span>"
                "</div>",
                unsafe_allow_html=True,
            )
            st.caption("Approve the exact title, description and pinned comment used for upload.")

            title = st.text_input(
                "YouTube title",
                max_chars=100,
                key="final_title",
                disabled=metadata_approved and not st.session_state.get("metadata_editing"),
            )
            meta_cols = st.columns(2)
            with meta_cols[0]:
                description = st.text_area(
                    "YouTube description",
                    height=140,
                    key="final_description",
                    disabled=metadata_approved and not st.session_state.get("metadata_editing"),
                )
            with meta_cols[1]:
                comment = st.text_area(
                    "Pinned comment",
                    height=140,
                    key="final_comment",
                    disabled=metadata_approved and not st.session_state.get("metadata_editing"),
                )

            if not metadata_approved:
                approve_col, note_col = st.columns([1, 2])
                with approve_col:
                    if st.button(
                        "Approve metadata",
                        type="primary",
                        width="stretch",
                        key="approve_metadata",
                    ):
                        try:
                            from final_qc_runtime import validate_final_upload_metadata
                            clean_title, clean_description, clean_comment = validate_final_upload_metadata(
                                title, description, comment
                            )
                            controller.persist_approved_metadata(
                                clean_title,
                                clean_description,
                                clean_comment,
                            )
                            st.session_state["metadata_pending_values"] = {
                                "run_id": run_id,
                                "title": clean_title,
                                "description": clean_description,
                                "comment": clean_comment,
                            }
                            st.session_state["approved_metadata"] = {
                                "title": clean_title,
                                "description": clean_description,
                                "comment": clean_comment,
                            }
                            st.session_state["metadata_approved"] = True
                            st.session_state["metadata_editing"] = False
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Metadata needs attention: {type(exc).__name__}: {exc}")
                with note_col:
                    st.caption("Nothing uploads until this approval succeeds.")
            else:
                st.success("Metadata approved.", icon="✅")

    if not (video_path and os.path.isfile(video_path)):
        st.error("The final video path is recorded, but the file is not accessible from the dashboard process.")
        st.code(video_path or "No final video path recorded.", language="text")
        return

    preview_col, publish_col = st.columns([1.35, .65], gap="large")
    with preview_col:
        st.markdown(
            "<div class='release-subhead'><span class='release-step-dot'>2</span><b>Watch</b></div>",
            unsafe_allow_html=True,
        )
        st.video(video_path)
    with publish_col:
        st.markdown(
            "<div class='release-subhead'><span class='release-step-dot'>3</span><b>Publish</b></div>",
            unsafe_allow_html=True,
        )
        st.caption("Public asks for one final confirmation. Private uploads remain hidden.")
        upload_unlocked = (
            metadata_approved
            and not bool(st.session_state.get("upload_result"))
            and not bool(snapshot.get("uploaded_video_id"))
        )
        if not metadata_approved:
            st.info("Approve metadata to unlock upload.")
        if st.button(
            "Upload Publicly",
            type="primary",
            width="stretch",
            key="upload_public",
            disabled=not upload_unlocked,
        ):
            _perform_upload(
                controller,
                snapshot,
                str(approved_metadata.get("title") or "").strip(),
                str(approved_metadata.get("description") or "").strip(),
                str(approved_metadata.get("comment") or "").strip(),
                "public",
            )

        if st.button(
            "Upload Privately",
            width="stretch",
            key="upload_private",
            disabled=not upload_unlocked,
        ):
            _perform_upload(
                controller,
                snapshot,
                str(approved_metadata.get("title") or "").strip(),
                str(approved_metadata.get("description") or "").strip(),
                str(approved_metadata.get("comment") or "").strip(),
                "private",
            )

    result = st.session_state.get("upload_result", "")
    if result:
        st.success(f"Last upload completed: {result}")

def _perform_upload(
    controller: DashboardWorkflowController,
    snapshot: Dict[str, Any],
    title: str,
    description: str,
    comment: str,
    publish_mode: str,
) -> None:
    try:
        category_key = st.session_state.get("web_config", {}).get("category", "national_global_affairs")
        genre_cfg = ultimate_bot.CONTENT_CATEGORIES.get(
            category_key,
            ultimate_bot.CONTENT_CATEGORIES["national_global_affairs"],
        )
        result = controller.upload_manual(
            str(snapshot.get("video_path") or ""),
            snapshot.get("script_data") or {},
            title,
            description,
            comment,
            publish_mode,
            genre_cfg,
            st.session_state.get("web_config", {}).get("trend_keyword", ""),
        )
        st.session_state.upload_result = str(result)
        st.session_state.upload_mode = str(publish_mode).strip().lower()
        st.session_state.upload_notice = ""
        st.session_state.upload_notice_kind = ""
        st.rerun()
    except Exception as exc:
        message = str(exc)
        match = re.search(r"accepted video\s+([A-Za-z0-9_-]+)", message)
        video_id = str(getattr(exc, "video_id", "") or (match.group(1) if match else "")).strip()
        if video_id and "kept it private instead of public" in message:
            st.session_state["upload_notice"] = message
            st.session_state["upload_notice_kind"] = "visibility_blocked"
            st.session_state["upload_mode"] = "youtube_private"
            st.error(message)
            st.info(
                "YouTube blocked public visibility for this upload. The video already exists and is private, "
                "so keep this upload private rather than creating a duplicate."
            )
            st.link_button(
                "Open the YouTube video",
                f"https://www.youtube.com/watch?v={video_id}",
                width="content",
            )
        else:
            if publish_mode == "public":
                st.error(
                    f"YouTube did not complete the public upload: "
                    f"{type(exc).__name__}: {message}"
                )
                st.info(
                    "No private fallback upload was created. The Private button remains available "
                    "for this run."
                )
            else:
                st.error(f"Upload failed: {type(exc).__name__}: {exc}")


def render_live_monitor(controller: DashboardWorkflowController) -> None:
    def _render_content(snapshot: Dict[str, Any]) -> None:
        snapshot["_controller"] = controller
        render_powershell_widget(snapshot)
        render_stage_progress(snapshot)

    snapshot = controller.snapshot()

    if live_monitor_should_poll(snapshot):
        poll_stage = str(snapshot.get("stage") or "").strip()

        @st.fragment(run_every="2s")
        def _live_monitor_fragment():
            live_snapshot = controller.snapshot()
            live_stage = str(live_snapshot.get("stage") or "").strip()
            if (
                not live_snapshot.get("thread_alive")
                or live_stage != poll_stage
                or not live_monitor_should_poll(live_snapshot)
            ):
                st.rerun()
                return
            _render_content(live_snapshot)

        _live_monitor_fragment()
        return

    _render_content(snapshot)


def render_live_factory(config: Dict[str, Any], controller: DashboardWorkflowController) -> None:
    if not st.session_state.get("live_path_ready"):
        return

    problems = check_required_local_assets()
    live_blockers = [item for item in problems if "provider key" in item]
    if live_blockers:
        st.warning(
            "Some live provider keys are not configured. Discovery or production may stop when that provider is required."
        )

    if st.session_state.production_started:
        render_live_monitor(controller)
        return

    if st.session_state.candidates:
        _render_section_header(
            "Headline stage",
            "Choose a topic",
            "Current, event-backed stories are ready.",
        )
    else:
        _render_section_header(
            "Headline stage",
            "Find today's headlines",
            "Choose a story from the current event radar.",
        )

    if not st.session_state.candidates:
        mode_label = str(config.get("display_format") or config.get("editorial_mode") or "Deep Dive")
        language_label = str(config.get("language_label") or "English")
        category_label = str(config.get("category") or "Automatic").replace("_", " ").title()
        with st.container():
            st.markdown(
                "<div class='empty-state'><div class='empty-title'>Ready for a new Short</div>"
                "<div class='empty-copy'>Choose a current story. Ranking is automatic; the final choice is yours.</div></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div class='meta-row'><span class='meta-chip'>Mode · {_ui_html(mode_label)}</span>"
                f"<span class='meta-chip'>Category · {_ui_html(category_label)}</span></div>",
                unsafe_allow_html=True,
            )
            st.write("")
            if st.button("Find today's ranked topics", type="primary", width="stretch"):
                from dashboard_topic_discovery_runtime import clear_dashboard_discovery_cache
                clear_dashboard_discovery_cache()
                controller.reset()
                try:
                    controller.update("discovery", 10, "Finding current stories and building the ranked topic list.")
                    conn = sqlite3.connect(ultimate_bot.DB_PATH)
                    try:
                        migrate_vault(conn)
                        if config.get("editorial_mode") == "AI":
                            candidates = discover_ai_topics(
                                ultimate_bot,
                                config,
                                conn,
                                max_candidates=MAX_DASHBOARD_DISCOVERY_HEADLINES,
                                retained_candidates=st.session_state.get("retained_topics", []),
                            )
                        else:
                            candidates = discover_ranked_topics(
                                ultimate_bot,
                                config,
                                conn,
                                max_candidates=MAX_DASHBOARD_DISCOVERY_HEADLINES,
                                retained_candidates=st.session_state.get("retained_topics", []),
                            )
                    finally:
                        conn.close()
                    for candidate in candidates:
                        candidate["dashboard_discovery_pool"] = True
                    st.session_state.candidates = candidates
                    st.session_state.web_config = config
                    st.session_state.production_started = False
                    st.session_state.upload_result = ""
                    st.session_state.candidate_page = 0
                    st.success(f"Found {len(candidates)} ranked headlines. Choose one below.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Topic discovery failed: {type(exc).__name__}: {exc}")
        return

    pending_candidate = st.session_state.get("pending_candidate")
    if pending_candidate:
        headline = str(pending_candidate.get("title") or "Untitled story").strip()
        source = _ui_text(pending_candidate.get("source_label"), "News source")
        url = str(pending_candidate.get("story_url") or "").strip()

        config_for_queries = dict(st.session_state.web_config or {})
        query_story_key, query_suggestions = _ensure_visual_query_plan(pending_candidate, config_for_queries)

        _render_section_header(
            "Step 02",
            "Review your story & search terms",
            "Review the prepared image-search phrases before production.",
        )
        with st.container(border=True):
            st.markdown("<div class='story-rank'>SELECTED TOPIC</div>", unsafe_allow_html=True)
            st.markdown(
                f"<div class='story-title' style='font-size:1.35rem;overflow-wrap:anywhere'>{_ui_html(headline)}</div>",
                unsafe_allow_html=True,
            )
            st.caption(f"Source · {source}")
            if url.startswith(("http://", "https://")):
                st.link_button("Open source article", url, width="content")

            st.markdown("### Ranked visual search terms")
            st.caption(
                "These are query suggestions only. No image search starts here. Keep, edit, clear, or add terms; "
                "their top-to-bottom order becomes the fetch budget rank."
            )
            field_count = max(
                1,
                int(st.session_state.get("visual_query_field_count") or len(query_suggestions) or 1),
            )
            for index in range(field_count):
                field_key = f"visual_query_field_{query_story_key}_{index}"
                st.text_input(
                    f"Query {index + 1}",
                    key=field_key,
                    placeholder="e.g. Virat Kohli India cricket, BCCI logo, India women's cricket team",
                )
                if index < len(query_suggestions):
                    suggestion = query_suggestions[index]
                    hint = _ui_text(suggestion.get("source_hint"), "Configured image source")
                    reason = _ui_text(suggestion.get("reason"))
                    if hint or reason:
                        st.caption(
                            (f"{hint}" if hint else "")
                            + (f" · {reason}" if reason else "")
                        )

            if st.button(
                "＋ Add another query",
                type="secondary",
                width="content",
                key=f"add_visual_query_{query_story_key}",
            ):
                next_index = field_count
                st.session_state.visual_query_field_count = field_count + 1
                st.session_state[f"visual_query_field_{query_story_key}_{next_index}"] = ""
                st.rerun()

        start_col, cancel_col = st.columns([1.5, 1])
        with start_col:
            if st.button("Start production", type="primary", width="stretch", key="start_selected_topic"):
                config = dict(st.session_state.web_config)
                accepted_queries = [
                    str(
                        st.session_state.get(
                            f"visual_query_field_{query_story_key}_{index}",
                            "",
                        )
                        or ""
                    ).strip()
                    for index in range(field_count)
                ]
                accepted_queries = [query for query in accepted_queries if query]
                config["visual_search_queries"] = "\n".join(accepted_queries)
                config["visual_search_query_list"] = list(accepted_queries)
                if config.get("editorial_mode") == "AI":
                    config["category"] = str(pending_candidate.get("recommended_category") or "national_global_affairs")
                    config["format_mode"] = str(pending_candidate.get("recommended_format") or "regular")
                st.session_state.production_started = True
                st.session_state.upload_result = ""
                controller.start_production(config, dict(pending_candidate))
                st.rerun()
        with cancel_col:
            if st.button("Choose another headline", width="stretch", key="cancel_selected_topic"):
                st.session_state.pending_candidate = None
                st.session_state.visual_search_queries = ""
                st.session_state.visual_query_story_key = ""
                st.session_state.visual_query_suggestions = []
                st.session_state.visual_query_field_count = 0
                st.rerun()
        return


    candidates = st.session_state.candidates
    sports_desk = (
        (
            str(config.get("category") or "").strip() == "sports_stories_of_day"
            and str(config.get("format_mode") or "").strip() == "cricket"
        )
        or (
            str(config.get("category") or "").strip() == "sports"
            and str(config.get("editorial_mode") or "").strip() == "Niche Sports"
        )
    )
    if sports_desk:
        from sports_topic_desk_runtime import render_sports_topic_desk
        sports_scope = (
            str(config.get("cricket_category") or "").strip()
            if str(config.get("format_mode") or "").strip() == "cricket"
            else "Niche Sports"
        )
        render_sports_topic_desk(
            candidates,
            _ui_text,
            _ui_html,
            _remember_unpublished_topic,
            scope=sports_scope or "India / Asia",
        )
        return
    total = min(len(candidates), MAX_DASHBOARD_DISCOVERY_HEADLINES)
    page_size = 6
    page_count = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(int(st.session_state.get("candidate_page", 0) or 0), page_count - 1))
    start_index = page * page_size
    visible = candidates[start_index:start_index + page_size]

    event_backed = sum(
        1 for item in candidates
        if float(item.get("topic_actionability_score") or 0.0) >= 3.0
    )
    india_led = sum(
        1 for item in candidates
        if float(item.get("india_relevance_score") or 0.0) >= 5.0
    )
    niche_led = sum(
        1 for item in candidates
        if float(item.get("niche_opportunity_score") or 0.0) >= 6.0
    )
    st.markdown(
        f"<div class='live-bar'><div class='live-bar-copy'><b>Story radar</b> · "
        f"Showing {start_index + 1}–{start_index + len(visible)} of {total} · "
        f"{event_backed} event-backed · {niche_led} niche opportunities · {india_led} India-led</div></div>",
        unsafe_allow_html=True,
    )

    for row_start in range(0, len(visible), 3):
        row = visible[row_start:row_start + 3]
        cols = st.columns(len(row), gap="medium")
        for local_index, candidate in enumerate(row):
            absolute_index = start_index + row_start + local_index
            with cols[local_index]:
                rank = absolute_index + 1
                title = str(candidate.get("title") or "Untitled story").strip()
                reason = str(candidate.get("discovery_reason") or "").strip()
                score = float(candidate.get("candidate_score") or 0.0)
                evidence = build_discovery_evidence(candidate)
                channel_fit = float(candidate.get("channel_fit_score") or 0.0)
                channel_fit_samples = int(candidate.get("channel_fit_samples") or 0)
                source = str(candidate.get("source_label") or "News source").strip()
                url = str(candidate.get("story_url") or "").strip()

                actionability = float(candidate.get("topic_actionability_score") or 0.0)
                shorts_viability = float(candidate.get("shorts_viability_score") or 0.0)
                india_focus = float(candidate.get("india_relevance_score") or 0.0)
                niche_opportunity = float(candidate.get("niche_opportunity_score") or 0.0)
                development = _ui_text(candidate.get("event_development_state") or "event", "event")
                source_count = int(evidence.get("independent_publishers") or evidence.get("independent_domains") or 0)
                topic_descriptor = (
                    f"{source_count} independent publisher(s) · "
                    f"{evidence['articles']} supporting article(s)"
                )
                with st.container():
                    st.markdown(
                        "<div class='topic-card'>"
                        f"<div class='topic-kicker'><span>TOPIC #{rank:02d}</span>"
                        f"<span>{_ui_html(development.upper())}</span></div>"
                        f"<div class='topic-title'>{_ui_html(title)}</div>"
                        f"<div class='topic-subtitle'>{_ui_html(topic_descriptor)}</div>"
                        f"<div class='topic-chips'>"
                        f"<span class='topic-chip strong'>Event {actionability:.1f}</span>"
                        f"<span class='topic-chip strong'>Shorts {shorts_viability:.1f}</span>"
                        f"<span class='topic-chip'>Niche {niche_opportunity:.1f}</span>"
                        f"<span class='topic-chip'>India {india_focus:.1f}</span>"
                        + (
                            f"<span class='topic-chip learning-chip'>Learning {channel_fit:.1f}/10 · {channel_fit_samples} upload(s)</span>"
                            if channel_fit_samples > 0
                            else ""
                        )
                        + (
                            f"<span class='topic-chip learning-chip'>Held · previous run</span>"
                            if candidate.get("retained_from_previous_run")
                            else ""
                        )
                        + f"</div>"
                        "</div>",
                        unsafe_allow_html=True,
                    )
                    if reason:
                        with st.expander("Why this topic", expanded=False):
                            st.caption(_ui_text(reason))
                    st.caption(f"Source · {_ui_text(source)}")
                    action_cols = st.columns([1, 1])
                    with action_cols[0]:
                        if url.startswith(("http://", "https://")):
                            st.link_button("Open source", url, width="stretch")
                    with action_cols[1]:
                        if st.button("Use topic →", type="primary", width="stretch", key=f"use_candidate_{absolute_index}"):
                            _remember_unpublished_topic(candidate)
                            st.session_state.pending_candidate = dict(candidate)
                            st.session_state.visual_search_queries = ""
                            st.session_state.visual_query_story_key = ""
                            st.session_state.visual_query_suggestions = []
                            st.session_state.visual_query_field_count = 0
                            st.rerun()

    nav_left, nav_center, nav_right = st.columns([1, 2, 1])
    with nav_left:
        if st.button("Previous", disabled=page <= 0, width="stretch", key="candidate_previous"):
            st.session_state.candidate_page = page - 1
            st.rerun()
    with nav_center:
        st.markdown(
            f"<div style='text-align:center;padding-top:10px' class='small-muted'>Page {page + 1} of {page_count}</div>",
            unsafe_allow_html=True,
        )
    with nav_right:
        if st.button("Next", disabled=page >= page_count - 1, width="stretch", key="candidate_next"):
            st.session_state.candidate_page = page + 1
            st.rerun()

def render_channel_statistics() -> None:
    _render_section_header(
        "Analytics",
        "Channel performance",
        "Live YouTube totals plus performance history, retention and CTR data.",
    )

    if not st.session_state.get("live_channel_stats"):
        st.session_state.live_channel_stats = collect_live_channel_statistics(ultimate_bot)

    try:
        stats = collect_channel_statistics(ultimate_bot.DB_PATH)
    except Exception as exc:
        st.error(f"Statistics could not be loaded: {type(exc).__name__}: {exc}")
        return

    metric_cols = st.columns(5)
    metric_cols[0].metric("Recorded runs", stats["total_runs"])
    metric_cols[1].metric("Completed runs", stats["completed_runs"])
    metric_cols[2].metric("Recorded views", f"{stats['total_views']:,}")
    metric_cols[3].metric(
        "Average view %",
        f"{stats['avg_view_percentage']:.1f}%" if stats["avg_view_percentage"] is not None else "—",
    )
    metric_cols[4].metric(
        "Average CTR",
        f"{stats['avg_ctr']:.2f}%" if stats["avg_ctr"] is not None else "—",
    )

    st.markdown(
        "<div class='learning-strip'>"
        "<div class='learning-strip-dot'></div>"
        "<div><div class='learning-strip-title'>Channel learning is active</div>"
        "<div class='learning-strip-copy'>Published results with synced retention data influence topic ranking and format/category selection. Analytics syncs automatically at most once every 24 hours; refresh here is manual.</div></div>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("### Live channel")
    live = st.session_state.get("live_channel_stats") or {}
    if live.get("error"):
        st.warning(f"Live YouTube totals could not be loaded: {live['error']}")
    elif live:
        live_cols = st.columns(4)
        live_cols[0].metric("Channel", live.get("channel_title", "Connected channel"))
        live_cols[1].metric(
            "Subscribers",
            "Hidden" if live.get("hidden_subscriber_count") else f"{live.get('subscriber_count', 0):,}",
        )
        live_cols[2].metric("Videos", f"{live.get('video_count', 0):,}")
        live_cols[3].metric("All-time views", f"{live.get('view_count', 0):,}")

    refresh_col, sync_col = st.columns([1, 1], gap="small")
    with refresh_col:
        if st.button("Refresh live YouTube totals", width="stretch", key="refresh_live_channel_stats"):
            st.session_state.live_channel_stats = collect_live_channel_statistics(ultimate_bot)
            st.rerun()
    with sync_col:
        if st.button("Refresh analytics", width="stretch", key="refresh_factory_analytics"):
            try:
                from learning_runtime import sync_factory_analytics
                conn = sqlite3.connect(ultimate_bot.DB_PATH)
                try:
                    migrate_vault(conn)
                    result = sync_factory_analytics(ultimate_bot, conn)
                finally:
                    conn.close()
                st.session_state.analytics_refresh_result = result
                st.rerun()
            except Exception as exc:
                st.error(f"Analytics refresh failed: {type(exc).__name__}: {exc}")

    refresh_result = st.session_state.get("analytics_refresh_result")
    if isinstance(refresh_result, dict):
        st.caption(
            f"Analytics refresh: {int(refresh_result.get('updated', 0) or 0)} videos updated; "
            f"{int(refresh_result.get('retention_ready', 0) or 0)} with retention; "
            f"{int(refresh_result.get('analytics_errors', 0) or 0)} analytics issue(s)."
        )

    for label, table in (
        ("By format", stats["by_format"]),
        ("By language", stats["by_language"]),
        ("Recent history", stats["recent"]),
    ):
        with st.expander(label, expanded=(label == "Recent history")):
            if table:
                st.dataframe(table, width="stretch", hide_index=True)
            else:
                st.info(f"No {label.lower()} data yet.")

    try:
        from channel_intelligence_runtime import build_intelligence
        conn = sqlite3.connect(ultimate_bot.DB_PATH)
        try:
            migrate_vault(conn)
            intelligence = build_intelligence(conn)
        finally:
            conn.close()

        st.markdown("### Channel learning")
        learning_cols = st.columns(3)
        learning_cols[0].metric("Published videos", intelligence["videos"])
        learning_cols[1].metric("Videos with view data", intelligence["reported"])
        learning_cols[2].metric("Videos with retention", intelligence["retention_ready"])

        for label, table in intelligence["tables"].items():
            if table:
                with st.expander(f"Learning · {label}", expanded=False):
                    st.dataframe(table[:8], width="stretch", hide_index=True)
                    st.caption(
                        "These historical patterns are shown here for analysis. Active ranking currently uses retention, category/format/language fit and prior-topic similarity; deeper hook/structure patterns are analytics only. "
                        "Small samples are directional rather than causal."
                    )
    except Exception as exc:
        st.warning(f"Learning summaries could not be loaded: {type(exc).__name__}: {exc}")


def render_offline_page() -> None:
    _render_section_header(
        "Engineering",
        "Offline diagnostics",
        "Safe checks for the dashboard and production contracts. No provider/API calls are made.",
    )

    if st.button("Run offline diagnostics", type="primary", width="content"):
        with st.spinner("Running offline checks..."):
            st.session_state.offline_diagnostics = run_offline_diagnostics()
        st.rerun()

    report = st.session_state.get("offline_diagnostics") or {}
    if not report:
        with st.container(border=True):
            st.info("No diagnostic run yet. Run the checks when you want a fresh contract snapshot.")
        return

    cols = st.columns(3)
    cols[0].metric("Passed", report.get("passed", 0))
    cols[1].metric("Failed", report.get("failed", 0))
    cols[2].metric("API calls", report.get("api_calls", 0))

    if report.get("all_passed"):
        st.success(f"All {report.get('total', 0)} checks passed.")
    else:
        st.error(f"{report.get('failed', 0)} check(s) failed.")

    checks = []
    for item in report.get("results", []):
        passed = item.get("status") == "PASS"
        gate_class = "pass" if passed else "block"
        checks.append(
            f"<div class='release-gate {gate_class}'><div class='release-gate-name'>"
            f"{'✓' if passed else '✕'} {item.get('name', '')}</div>"
            f"<div class='release-gate-detail'>{item.get('detail', '')}</div></div>"
        )
    st.markdown("<div class='release-gates'>" + "".join(checks) + "</div>", unsafe_allow_html=True)

def render_factory_function_coverage() -> None:
    report = factory_function_coverage()
    _render_section_header(
        "Engineering",
        "Function coverage",
        "A read-only map of the functions exposed by ultimate_bot.py.",
    )
    if report.get("complete"):
        st.success(f"All {report['total']} functions are accounted for.")
    else:
        st.error(
            f"Coverage is incomplete: {len(report.get('unmapped', []))} unmapped and "
            f"{len(report.get('stale_map', []))} stale entries."
        )

    buckets = report.get("by_surface") or {}
    cols = st.columns(4)
    labels = ["Live", "Analytics", "Demo / Diagnostics", "Internal"]
    for col, label in zip(cols, labels):
        col.metric(label, len(buckets.get(label, [])))

    for label in labels:
        names = buckets.get(label, [])
        with st.expander(f"{label} · {len(names)}", expanded=False):
            st.code("\\n".join(names), language="text") if names else st.caption("None")

def render_final_branding_preview() -> None:
    _render_section_header(
        "Branding",
        "Final branding preview",
        "A synthetic 1080×1920 frame using the same canonical branding compositor as production.",
    )

    if st.button("Render final branding preview", type="primary", width="content"):
        with st.spinner("Rendering the canonical branding overlay..."):
            result = run_demo_section("scene_branding")
        st.session_state.last_demo_results["scene_branding"] = result
        st.rerun()

    result = (st.session_state.get("last_demo_results") or {}).get("scene_branding")
    if not result:
        with st.container(border=True):
            st.info("Run the preview to inspect the final logo and source overlay.")
        return

    if result.get("status") == "PASS":
        st.success(result.get("detail", "Final branding preview rendered."))
    else:
        st.error(result.get("detail", "Final branding preview failed."))

    preview_path = (result.get("artifacts") or {}).get("final_branding_preview")
    if preview_path and os.path.isfile(preview_path):
        with st.container(border=True):
            st.image(preview_path, caption="Canonical compositor · 1080×1920", width="stretch")

def render_test_page() -> None:
    """Keep engineering/diagnostic tools out of the Live production flow."""
    _render_section_header(
        "Test workspace",
        "Test",
        "Safe diagnostics and previews live here. They never replace the Live production flow.",
    )

    options = [
        "Channel Statistics",
        "Offline Diagnostics",
        "Demo",
        "Final Branding Preview",
    ]
    current = st.session_state.get("test_menu_selection") or "Offline Diagnostics"
    if current not in options:
        current = options[0]
    selected = st.pills(
        "Test tools",
        options,
        selection_mode="single",
        default=current,
        key="test_menu",
        label_visibility="collapsed",
        width="stretch",
        wrap=True,
    )
    st.session_state.test_menu_selection = selected or current

    if st.session_state.test_menu_selection == "Channel Statistics":
        render_channel_statistics()
    elif st.session_state.test_menu_selection == "Offline Diagnostics":
        render_offline_page()
    elif st.session_state.test_menu_selection == "Demo":
        render_demo_page()
    else:
        render_final_branding_preview()


def render_demo_page() -> None:
    _render_section_header(
        "Engineering lab",
        "Demo",
        "Controlled component checks. These never perform a production upload.",
    )

    sections = [
        ("imports", "Imports"),
        ("environment", "Environment"),
        ("database", "Database"),
        ("visual_strategy", "Visual strategy & identity"),
        ("visual_queries", "Visual queries"),
        ("scene_branding", "Scene overlay"),
        ("script_audio", "Script cleaning & audio timing"),
        ("runtime_bindings", "Runtime bindings"),
        ("provider_boundary", "Raw provider boundary"),
        ("premium_renderers", "Subtitles, Top-5 card & glass logo"),
        ("dashboard_architecture", "Dashboard architecture"),
        ("factory_function_coverage", "Function coverage"),
    ]

    if st.button("Run all demo checks", type="primary", width="content"):
        results = {}
        with st.spinner("Running all demo sections..."):
            for key, _label in sections:
                results[key] = run_demo_section(key)
        st.session_state.last_demo_results = results
        st.rerun()

    columns = st.columns(3, gap="medium")
    for index, (key, label) in enumerate(sections):
        with columns[index % 3]:
            with st.container(border=True):
                st.markdown(
                    f"<div class='story-rank'>CHECK {index + 1:02d}</div>"
                    f"<div class='story-title'>{label}</div>",
                    unsafe_allow_html=True,
                )
                if st.button("Run check", key=f"demo_{key}", width="stretch"):
                    result = run_demo_section(key)
                    st.session_state.last_demo_results[key] = result
                    st.rerun()

                result = (st.session_state.get("last_demo_results") or {}).get(key)
                if result:
                    if result.get("status") == "PASS":
                        st.success(result.get("detail", "Passed"), icon="✅")
                    else:
                        st.error(result.get("detail", "Failed"), icon="⛔")
                    artifacts = result.get("artifacts") or {}
                    for artifact_name, artifact_path in artifacts.items():
                        if artifact_path and os.path.isfile(artifact_path):
                            st.caption(artifact_name.replace("_", " ").title())
                            st.image(artifact_path, width="stretch")

    st.divider()
    render_factory_function_coverage()

def main() -> None:
    load_streamlit_secrets_into_runtime()
    _remote_startup_guard()
    initialise_runtime()

    if not getattr(ultimate_bot, "_dashboard_db_migrated", False):
        try:
            db = sqlite3.connect(ultimate_bot.DB_PATH)
            migrate_vault(db)
            db.close()
            ultimate_bot._dashboard_db_migrated = True
        except Exception as exc:
            st.warning(f"Database migration check failed: {exc}")

    _init_state()
    controller: DashboardWorkflowController = st.session_state.workflow_controller
    workspace = render_workspace_navigation()

    if workspace == "Live":
        snapshot = controller.snapshot()
        render_header("Live Factory")
        if (
            not st.session_state.get("production_started")
            and not st.session_state.get("candidates")
            and not st.session_state.get("pending_candidate")
        ):
            render_powershell_widget(snapshot)
            config = render_live_navigation()
        else:
            config = build_config()
        render_live_factory(config, controller)
        if (
            st.session_state.get("production_started")
            and not snapshot.get("thread_alive")
            and (
                snapshot.get("completed")
                or snapshot.get("stage") == "error"
            )
        ):
            st.divider()
            if st.button("Start a new Live run", width="content", key="reset_live_run_main"):
                reset_run()
                st.rerun()
    else:
        render_powershell_widget(controller.snapshot())
        render_header("Test")
        render_test_page()

    st.markdown(
        "<div class='dashboard-footer'>Shorts Studio · human review gates remain in your hands; "
        "the underlying production logic remains the source of truth.</div>",
        unsafe_allow_html=True,
    )

main()