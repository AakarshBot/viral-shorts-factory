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

try:
    from streamlit_cropper import st_cropper
except ModuleNotFoundError:
    st_cropper = None

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

from dashboard_runtime import (
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


MAX_DASHBOARD_DISCOVERY_HEADLINES = 28

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
.topic-card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:17px 17px 15px;box-shadow:var(--shadow);height:100%;min-height:232px;display:flex;flex-direction:column}
.topic-card-actions{margin-top:auto}
.progress-hero{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:16px 18px;box-shadow:var(--shadow);margin:12px 0 16px}
.progress-hero-head{display:flex;justify-content:space-between;gap:16px;align-items:baseline}
.progress-hero-title{font-size:.95rem;font-weight:900;color:var(--text)}
.progress-hero-value{font-size:1.45rem;font-weight:900;color:var(--accent-deep);letter-spacing:-.04em}
.progress-track{height:9px;background:#ebe8e3;border-radius:999px;overflow:hidden;margin:11px 0 14px}
.progress-track-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,#3d6f74,#7e9f96);transition:width .35s ease}
.progress-meta{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:.71rem}
.progress-steps{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:7px;margin-top:14px}
.progress-step{min-width:0;background:#f7f3ed;border:1px solid #e7ddd1;border-radius:11px;padding:8px 9px}
.progress-step.active{background:#eaf2f1;border-color:#95b5b2}
.progress-step.done{background:#eef7f1;border-color:#bcd9ca}
.progress-step.stopped{background:#fcf1e6;border-color:#ebcbb1}
.progress-step-index{font-size:.58rem;font-weight:900;letter-spacing:.12em;color:var(--muted-2)}
.progress-step-name{font-size:.69rem;font-weight:850;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.progress-step-state{font-size:.61rem;color:var(--muted);margin-top:3px}
@media(max-width:1100px){.progress-steps{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:700px){.progress-steps{grid-template-columns:repeat(2,minmax(0,1fr))}}
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
.brand-card{
  position:relative;
  overflow:hidden;
  isolation:isolate;
}
.brand-card::after{
  content:"";
  position:absolute;
  left:0;
  top:0;
  width:7px;
  height:100%;
  background:linear-gradient(180deg,#77a8a3,#3d6f74 55%,#294f53);
  border-radius:22px 0 0 22px;
}
.brand-eyebrow{
  display:flex;
  align-items:center;
  gap:7px;
  color:#6a625a;
  font-size:.62rem;
  font-weight:900;
  letter-spacing:.13em;
  text-transform:uppercase;
  margin-bottom:8px;
}
.brand-dot{
  width:7px;
  height:7px;
  border-radius:50%;
  background:#4e9188;
  box-shadow:0 0 0 4px rgba(78,145,136,.12);
}
.brand-trust-row{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
  margin-top:10px;
}
.brand-trust-chip{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:6px 10px;
  border-radius:999px;
  border:1px solid #dfd4c9;
  background:rgba(255,253,249,.78);
  color:#665e55;
  font-size:.66rem;
  font-weight:850;
  box-shadow:0 4px 14px rgba(69,49,31,.04);
}
.brand-trust-chip strong{color:#31565a}
.factory-status{
  position:relative;
  overflow:hidden;
}
.factory-status::before{
  content:"";
  position:absolute;
  inset:0 0 auto auto;
  width:92px;
  height:92px;
  border-radius:50%;
  background:radial-gradient(circle,rgba(78,145,136,.13) 0%,rgba(78,145,136,0) 68%);
  pointer-events:none;
}
.topic-card,.story-card,.output-card,.release-card,.panel{
  transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease;
}
.topic-card:hover,.story-card:hover,.output-card:hover,.release-card:hover,.panel:hover{
  transform:translateY(-2px);
  border-color:#d4c7ba;
  box-shadow:0 16px 34px rgba(69,49,31,.10);
}
.topic-kicker{margin-bottom:7px}
.topic-title{letter-spacing:-.025em}
.topic-subtitle{margin-top:7px}
.topic-chips{margin-top:12px}
.topic-chip{box-shadow:inset 0 1px 0 rgba(255,255,255,.72)}
[data-testid="stMetric"]{
  transition:transform .16s ease,box-shadow .16s ease;
}
[data-testid="stMetric"]:hover{
  transform:translateY(-1px);
  box-shadow:0 14px 28px rgba(69,49,31,.09);
}
[data-testid="stProgress"] div[role="progressbar"]{
  height:10px!important;
  border-radius:999px!important;
  overflow:hidden!important;
}
[data-testid="stProgress"] div[role="progressbar"] > div{
  border-radius:999px!important;
}
.stButton>button,.stLinkButton>a{
  transition:transform .14s ease,box-shadow .14s ease,border-color .14s ease,background .14s ease!important;
}
.stButton>button:hover,.stLinkButton>a:hover{
  transform:translateY(-1px);
  box-shadow:0 8px 18px rgba(69,49,31,.08);
}
.stButton>button[kind="primary"]{
  letter-spacing:-.01em;
}
section[data-testid="stSidebar"]{
  box-shadow:12px 0 30px rgba(69,49,31,.035);
}
section[data-testid="stSidebar"] .stRadio > div{
  gap:7px!important;
}
section[data-testid="stSidebar"] .stRadio label{
  border-radius:12px!important;
  padding:7px 9px!important;
}
.live-settings{
  position:relative;
  overflow:hidden;
}
.live-settings::before{
  content:"";
  position:absolute;
  inset:0 auto auto 0;
  width:100%;
  height:1px;
  background:linear-gradient(90deg,transparent,#d7c8bb 30%,#d7c8bb 70%,transparent);
}
.empty-state{
  position:relative;
  overflow:hidden;
}
.empty-state::after{
  content:"SS";
  position:absolute;
  right:22px;
  bottom:-12px;
  font-size:5.5rem;
  line-height:1;
  font-weight:950;
  letter-spacing:-.08em;
  color:rgba(47,93,98,.05);
  pointer-events:none;
}
.timeline{
}
.dashboard-footer{
  border-top:1px solid #e7ddd1;
  margin-top:24px;
}
.learning-strip{
  display:flex;
  align-items:flex-start;
  gap:11px;
  margin:16px 0 18px;
  padding:13px 15px;
  border:1px solid #d9e3e0;
  border-radius:16px;
  background:linear-gradient(135deg,#f5faf8 0%,#eef5f2 100%);
  box-shadow:0 8px 22px rgba(47,93,98,.055);
}
.learning-strip-dot{
  width:9px;
  height:9px;
  margin-top:4px;
  flex:0 0 9px;
  border-radius:50%;
  background:#4e9188;
  box-shadow:0 0 0 5px rgba(78,145,136,.10);
}
.learning-strip-title{
  font-size:.78rem;
  font-weight:900;
  color:#31565a;
  letter-spacing:.01em;
}
.learning-strip-copy{
  margin-top:2px;
  color:#6b655d;
  font-size:.72rem;
  line-height:1.45;
}
.learning-chip{
  background:#edf5f2;
  border-color:#cfe0dc;
  color:#31565a;
}

/* 2026 studio layer: visual-only polish. No widget state or production behaviour is changed. */
:root{
  --studio-ink:#182023;
  --studio-teal:#2f6f70;
  --studio-mint:#7fb7aa;
  --studio-lilac:#c1b5ff;
  --studio-cream:#fbf6ee;
  --studio-glass:rgba(255,253,249,.68);
  --studio-glass-strong:rgba(255,253,249,.84);
  --studio-shadow:0 24px 70px rgba(40,31,22,.10);
}
html{scroll-behavior:smooth}
::selection{background:rgba(78,145,136,.20);color:var(--studio-ink)}
body{
  background:
    radial-gradient(circle at 12% 5%,rgba(127,183,170,.14),transparent 27%),
    radial-gradient(circle at 86% 4%,rgba(193,181,255,.14),transparent 30%),
    radial-gradient(circle at 64% 78%,rgba(255,211,155,.11),transparent 25%),
    var(--bg)!important;
}
.stApp{
  background:
    radial-gradient(circle at 15% 18%,rgba(127,183,170,.09),transparent 22%),
    radial-gradient(circle at 88% 28%,rgba(193,181,255,.08),transparent 24%),
    transparent!important;
  position:relative;
}
[data-testid="stAppViewContainer"] .main{position:relative;z-index:1}
.block-container{padding-top:1.2rem!important}
.brand-card{
  background:
    linear-gradient(rgba(255,253,249,.86),rgba(248,241,233,.78)) padding-box,
    conic-gradient(from 210deg,#5d958e,#b9c9ff,#e7ca96,#78aaa0,#5d958e) border-box!important;
  border:1px solid transparent!important;
  box-shadow:var(--studio-shadow),inset 0 1px 0 rgba(255,255,255,.92)!important;
  min-height:100px!important;
}
.brand-card::before{
  content:"";
  position:absolute;
  width:180px;height:180px;
  right:-70px;top:-88px;
  border-radius:50%;
  background:radial-gradient(circle,rgba(127,183,170,.18),rgba(127,183,170,0) 68%);
  pointer-events:none;
}
.brand-title-row{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
}
.brand-signature{
  position:relative;
  display:inline-flex;
  align-items:center;
  border:1px solid rgba(49,86,90,.12);
  background:rgba(255,255,255,.48);
  color:#6d655c;
  padding:5px 8px;
  border-radius:999px;
  font-size:.57rem;
  line-height:1;
  font-weight:900;
  letter-spacing:.13em;
  text-transform:uppercase;
  white-space:nowrap;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.76);
}
.brand-signature::after{
  content:"control deck unlocked";
  position:absolute;
  right:0;top:calc(100% + 8px);
  padding:7px 9px;
  border:1px solid #d9cec1;
  border-radius:9px;
  background:rgba(31,39,41,.94);
  color:#f6f1ea;
  font-size:.58rem;
  letter-spacing:.04em;
  font-weight:800;
  text-transform:none;
  opacity:0;
  transform:translateY(-3px);
  transition:opacity .16s ease,transform .16s ease;
  pointer-events:none;
  white-space:nowrap;
  z-index:20;
}
.brand-signature:hover::after{opacity:1;transform:translateY(0)}
.brand-trust-row{margin-top:8px}
.brand-trust-chip{
  padding:5px 9px!important;
  background:rgba(255,253,249,.58)!important;
  border-color:rgba(95,126,125,.18)!important;
}
.brand-trust-chip::first-letter{color:#3f7f78}
.factory-status{
  min-height:100px;
  background:
    linear-gradient(rgba(255,253,249,.82),rgba(248,243,236,.72)) padding-box,
    conic-gradient(from 100deg,#a8cfc8,#d8cfff,#efddba,#a8cfc8) border-box!important;
  border:1px solid transparent!important;
  box-shadow:var(--studio-shadow),inset 0 1px 0 rgba(255,255,255,.9)!important;
  backdrop-filter:blur(22px) saturate(1.08);
  -webkit-backdrop-filter:blur(22px) saturate(1.08);
  display:flex;
  flex-direction:column;
  justify-content:center;
  text-align:left!important;
}
.factory-status.status-running .factory-status-label,
.factory-status.status-running .factory-status-value{color:#2f6c67}
.factory-status.status-running::after{
  content:"LIVE PULSE";
  position:absolute;
  right:12px;bottom:10px;
  color:#4f817c;
  font-size:.51rem;
  letter-spacing:.12em;
  font-weight:900;
}
.factory-status.status-ready::after{content:"READY TO CREATE"}
.factory-status.status-done::after{content:"COMPLETE"}
.factory-status.status-ready::after,
.factory-status.status-done::after{
  position:absolute;
  right:12px;bottom:10px;
  color:#81776b;
  font-size:.49rem;
  letter-spacing:.10em;
  font-weight:900;
}
.factory-status-label{position:relative;z-index:1}
.factory-status-value{position:relative;z-index:1}
.factory-status-detail{
  margin-top:4px;
  color:#756c61;
  font-size:.65rem;
  font-weight:750;
  line-height:1.35;
  max-width:12rem;
}
.status-orb{
  display:inline-block;
  width:7px;height:7px;
  margin-right:6px;
  border-radius:50%;
  vertical-align:middle;
  background:#7d8f8c;
  box-shadow:0 0 0 4px rgba(125,143,140,.10);
}
.status-orb.running{
  background:#4e9188;
  box-shadow:0 0 0 4px rgba(78,145,136,.12),0 0 18px rgba(78,145,136,.26);
  animation:status-pulse 1.9s ease-in-out infinite;
}
@keyframes status-pulse{
  0%,100%{transform:scale(1);opacity:.86}
  50%{transform:scale(1.25);opacity:1}
}
section[data-testid="stSidebar"]{
  background:
    linear-gradient(180deg,rgba(247,240,230,.92),rgba(233,224,212,.82))!important;
}
section[data-testid="stSidebar"]>div{
  position:relative;
}
.sidebar-brand{
  position:relative;
  display:flex;
  align-items:center;
  gap:11px;
  padding:11px 11px 12px;
  margin-bottom:14px;
  border:1px solid rgba(125,106,85,.15);
  border-radius:16px;
  background:rgba(255,253,249,.50);
  box-shadow:0 12px 28px rgba(74,52,34,.05),inset 0 1px 0 rgba(255,255,255,.9);
  overflow:visible;
}
.sidebar-brand-mark{
  display:grid;
  place-items:center;
  width:37px;height:37px;
  border-radius:12px;
  color:#fff;
  font-size:.73rem;
  font-weight:950;
  letter-spacing:-.06em;
  background:
    linear-gradient(135deg,#355e62,#5b8e89 58%,#7384c1);
  box-shadow:0 9px 20px rgba(47,93,98,.18),inset 0 1px 0 rgba(255,255,255,.36);
}
.sidebar-brand-copy{display:flex;flex-direction:column;gap:2px;min-width:0}
.sidebar-brand-name{
  color:#252a2d;
  font-size:.79rem;
  line-height:1;
  font-weight:950;
  letter-spacing:-.02em;
}
.sidebar-brand-meta{
  color:#80766b;
  font-size:.54rem;
  line-height:1.2;
  font-weight:850;
  letter-spacing:.12em;
  text-transform:uppercase;
}
.sidebar-brand::after{
  content:"quietly making better shorts";
  position:absolute;
  left:10px;bottom:-25px;
  color:#948a7e;
  font-size:.51rem;
  letter-spacing:.05em;
  opacity:.72;
}
section[data-testid="stSidebar"] .stRadio [role="radiogroup"]{
  gap:8px!important;
}
section[data-testid="stSidebar"] .stRadio label{
  position:relative;
  border:1px solid transparent!important;
  background:rgba(255,253,249,.24)!important;
  box-shadow:none!important;
  transition:transform .15s ease,border-color .15s ease,background .15s ease,box-shadow .15s ease!important;
}
section[data-testid="stSidebar"] .stRadio label:hover{
  transform:translateX(2px);
  background:rgba(255,253,249,.58)!important;
  border-color:rgba(47,93,98,.10)!important;
}
section[data-testid="stSidebar"] .stRadio label:has(input[aria-checked="true"]),
section[data-testid="stSidebar"] .stRadio label:has(input:checked){
  background:linear-gradient(90deg,rgba(255,253,249,.86),rgba(226,239,236,.70))!important;
  border-color:rgba(47,93,98,.18)!important;
  box-shadow:0 7px 17px rgba(47,93,98,.07)!important;
}
section[data-testid="stSidebar"] .stRadio label:has(input[aria-checked="true"])::before,
section[data-testid="stSidebar"] .stRadio label:has(input:checked)::before{
  content:"";
  position:absolute;
  left:-1px;top:7px;bottom:7px;
  width:3px;
  border-radius:99px;
  background:linear-gradient(180deg,#6b9b95,#385e61);
}
[data-testid="stVerticalBlockBorderWrapper"]{
  border-radius:18px!important;
  border-color:rgba(210,197,183,.78)!important;
  background:
    linear-gradient(180deg,rgba(255,253,249,.74),rgba(252,247,240,.58))!important;
  box-shadow:0 13px 34px rgba(69,49,31,.06),inset 0 1px 0 rgba(255,255,255,.84)!important;
}
[data-testid="stVerticalBlockBorderWrapper"] > div{
  border-radius:inherit;
}
.topic-card,.story-card,.output-card,.release-card,.panel,.progress-hero,.live-settings{
  box-shadow:var(--studio-shadow),inset 0 1px 0 rgba(255,255,255,.75)!important;
  background:
    linear-gradient(145deg,rgba(255,253,249,.84),rgba(247,240,232,.66))!important;
}
.topic-card:hover,.story-card:hover,.output-card:hover,.release-card:hover,.panel:hover{
  transform:translateY(-3px)!important;
  box-shadow:0 28px 58px rgba(58,46,34,.12),inset 0 1px 0 rgba(255,255,255,.85)!important;
}
.stButton>button,.stLinkButton>a{
  position:relative;
  overflow:hidden;
}
.stButton>button::after,.stLinkButton>a::after{
  content:"";
  position:absolute;
  inset:0 auto 0 -38%;
  width:34%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.18),transparent);
  transform:skewX(-18deg);
  transition:left .5s ease;
  pointer-events:none;
}
.stButton>button:hover::after,.stLinkButton>a:hover::after{left:115%}
.stButton>button:focus-visible,.stLinkButton>a:focus-visible,
.stTextInput input:focus-visible,.stTextArea textarea:focus-visible,
[data-baseweb="select"]:focus-within{
  outline:2px solid rgba(65,118,114,.54)!important;
  outline-offset:2px!important;
}
.stButton>button[kind="primary"]{
  background:
    linear-gradient(135deg,#355f63 0%,#477f7b 55%,#5f78a7 120%)!important;
  border-color:rgba(38,83,86,.9)!important;
  box-shadow:0 13px 28px rgba(47,93,98,.19),inset 0 1px 0 rgba(255,255,255,.20)!important;
}
.stButton>button[kind="primary"]:hover{
  background:
    linear-gradient(135deg,#2f595d 0%,#3e716e 55%,#536990 120%)!important;
}
.stButton>button[kind="primary"]:active{transform:translateY(1px)}
.st-key-live_format_menu button,
.st-key-live_topic_menu button,
.st-key-live_sports_menu button,
.st-key-live_cricket_scope_menu button,
.st-key-test_menu button{
  position:relative;
  overflow:hidden;
  background:
    linear-gradient(rgba(255,253,249,.72),rgba(245,239,232,.62)) padding-box,
    conic-gradient(from 120deg,#e58d92,#e7cc83,#82b99f,#79a9db,#a49be0,#e58d92) border-box!important;
  border:1.5px solid transparent!important;
  box-shadow:0 14px 30px rgba(69,49,31,.09),inset 0 1px 0 rgba(255,255,255,.88)!important;
}
.st-key-live_format_menu button::after,
.st-key-live_topic_menu button::after,
.st-key-live_sports_menu button::after,
.st-key-live_cricket_scope_menu button::after,
.st-key-test_menu button::after{
  content:"";
  position:absolute;
  top:0;bottom:0;left:-40%;
  width:28%;
  transform:skewX(-16deg);
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.30),transparent);
  transition:left .6s ease;
  pointer-events:none;
}
.st-key-live_format_menu button:hover::after,
.st-key-live_topic_menu button:hover::after,
.st-key-live_sports_menu button:hover::after,
.st-key-live_cricket_scope_menu button:hover::after,
.st-key-test_menu button:hover::after{left:115%}
.st-key-live_format_menu button[aria-pressed="true"],
.st-key-live_topic_menu button[aria-pressed="true"],
.st-key-live_sports_menu button[aria-pressed="true"],
.st-key-live_cricket_scope_menu button[aria-pressed="true"],
.st-key-test_menu button[aria-pressed="true"]{
  box-shadow:0 18px 36px rgba(47,93,98,.16),inset 0 1px 0 rgba(255,255,255,.92)!important;
}
.stSelectbox [data-baseweb="select"]>div,
.stMultiSelect [data-baseweb="select"]>div{
  min-height:44px!important;
  border-radius:13px!important;
  background:rgba(255,253,249,.72)!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.75)!important;
}
[data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea{
  background:rgba(255,253,249,.72)!important;
  border-radius:13px!important;
  border-color:#ddd1c4!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.76)!important;
  transition:border-color .15s ease,box-shadow .15s ease,background .15s ease;
}
[data-testid="stTextInput"] input:hover,[data-testid="stTextArea"] textarea:hover{
  background:rgba(255,253,249,.84)!important;
}
[data-testid="stExpander"]{
  background:rgba(255,253,249,.42)!important;
  border-radius:16px!important;
  overflow:hidden;
}
[data-testid="stExpander"] summary:hover{
  background:rgba(255,253,249,.48)!important;
}
[data-testid="stMetric"]{
  background:
    linear-gradient(145deg,rgba(255,253,249,.86),rgba(241,247,245,.66))!important;
  border-color:rgba(205,213,207,.78)!important;
  box-shadow:0 13px 30px rgba(47,93,98,.065),inset 0 1px 0 rgba(255,255,255,.88)!important;
}
[data-testid="stMetricValue"]{letter-spacing:-.055em!important}
[data-testid="stProgress"] div[role="progressbar"]{
  background:rgba(217,211,202,.66)!important;
  box-shadow:inset 0 1px 2px rgba(51,44,35,.07);
}
[data-testid="stProgress"] div[role="progressbar"] > div{
  background:linear-gradient(90deg,#477f7b,#79a9db,#9b92d2)!important;
  box-shadow:0 0 18px rgba(89,125,157,.22);
}
.progress-track-fill{
  background:linear-gradient(90deg,#3c706e 0%,#74a89d 48%,#7a7bb8 100%)!important;
  box-shadow:0 0 18px rgba(90,122,151,.18);
}
.progress-hero{
  border-color:rgba(202,211,208,.72)!important;
  overflow:hidden;
}
.progress-hero::after{
  content:"";
  position:absolute;
  top:0;left:-24%;
  width:22%;height:1px;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.96),transparent);
  animation:progress-sheen 4.4s linear infinite;
}
@keyframes progress-sheen{
  to{left:120%}
}
.progress-step{
  background:rgba(246,241,234,.62)!important;
  border-color:rgba(211,201,190,.74)!important;
}
.progress-step.active{
  background:linear-gradient(135deg,rgba(231,244,241,.86),rgba(234,235,250,.72))!important;
  border-color:rgba(107,152,148,.38)!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.80),0 8px 22px rgba(77,110,113,.06);
}
.progress-step.done{
  background:linear-gradient(135deg,rgba(237,247,242,.84),rgba(236,243,238,.70))!important;
}
.progress-step.stopped{
  background:linear-gradient(135deg,rgba(252,239,226,.88),rgba(251,232,222,.70))!important;
}
.crop-shell{
  background:linear-gradient(145deg,rgba(255,253,249,.84),rgba(239,245,242,.66))!important;
  border-color:rgba(198,211,206,.80)!important;
  box-shadow:var(--studio-shadow)!important;
}
[data-testid="stImage"] img{
  border-radius:14px;
  box-shadow:0 11px 26px rgba(36,29,22,.10);
}
[data-testid="stDataFrame"]{
  box-shadow:0 13px 30px rgba(69,49,31,.06)!important;
  background:rgba(255,253,249,.70)!important;
}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{
  background:linear-gradient(180deg,#bdb3a6,#809e99);
  border:3px solid transparent;
  background-clip:padding-box;
  border-radius:999px;
}
@media (max-width:900px){
  .brand-title-row{align-items:flex-start}
  .brand-signature{display:none}
  .sidebar-brand::after{display:none}
  .factory-status{min-height:86px}
}
@media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  .status-orb.running,.progress-hero::after{animation:none!important}
  .topic-card,.story-card,.output-card,.release-card,.panel,
  .stButton>button,.stLinkButton>a,.brand-signature::after{transition:none!important}
}

/* Final composition pass: shared rails, section rhythm and tactile focus states. */
.stApp{
  isolation:isolate;
}
.section-title{
  position:relative;
  display:inline-block;
  padding-bottom:7px;
}
.section-title::after{
  content:"";
  position:absolute;
  left:0;bottom:0;
  width:46px;height:2px;
  border-radius:999px;
  background:linear-gradient(90deg,#4c8882,#8c82c5,transparent);
}
[data-testid="stPills"]{
  margin-top:3px;
}
[data-testid="stPills"] [role="group"]{
  padding:5px!important;
  gap:8px!important;
  border-radius:21px!important;
  background:rgba(255,253,249,.34)!important;
  border:1px solid rgba(210,197,183,.46)!important;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.72)!important;
  -webkit-backdrop-filter:blur(11px);
}
[data-testid="stPills"] button{
  min-height:48px!important;
  border-radius:15px!important;
}
[data-testid="stPills"] button[aria-pressed="true"]{
  transform:translateY(-1px)!important;
  border-color:rgba(70,112,110,.28)!important;
  background:linear-gradient(135deg,rgba(237,247,243,.90),rgba(235,234,249,.84))!important;
  box-shadow:0 11px 23px rgba(47,93,98,.10),inset 0 1px 0 rgba(255,255,255,.94)!important;
}
.brand-card:hover,.factory-status:hover{
  transform:translateY(-1px);
  box-shadow:0 28px 76px rgba(40,31,22,.12),inset 0 1px 0 rgba(255,255,255,.94)!important;
}
.stButton>button:disabled,.stLinkButton>a[aria-disabled="true"]{
  opacity:.58!important;
  filter:saturate(.72);
  box-shadow:none!important;
}
[data-testid="stAlert"]{
  border-radius:15px!important;
  box-shadow:0 10px 28px rgba(69,49,31,.055)!important;
}
</style>""", unsafe_allow_html=True)

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
    return loaded



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

    defaults = {
        "candidates": [],
        "retained_topics": [],
        "web_config": {},
        "production_started": False,
        "upload_result": "",
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


def _forget_unpublished_topic(candidate: Dict[str, Any]) -> None:
    if not isinstance(candidate, dict):
        return
    key = _topic_identity(candidate)
    if not key:
        return
    st.session_state.retained_topics = [
        dict(item)
        for item in (st.session_state.get("retained_topics") or [])
        if isinstance(item, dict) and _topic_identity(item) != key
    ]


def reset_run() -> None:
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
        "Live": ("Live", "Current stories, review controls and release."),
        "Test": ("Test", "Diagnostics, previews and engineering checks."),
        "Channel Statistics": ("Analytics", "Connected-channel totals and performance history."),
        "Offline Diagnostics": ("Diagnostics", "Safe code and runtime checks."),
        "Demo": ("Demo", "Controlled checks for selected components."),
        "Final Branding Preview": ("Brand preview", "Inspect the canonical branding compositor."),
    }
    title, subtitle = titles.get(action_mode, ("Studio", "Editorial workspace"))

    logo_path = ""
    brand_dir = getattr(ultimate_bot, "BRAND_ASSETS_DIR", None)
    if brand_dir:
        for candidate in ("logo.png", "channels4_profile.jpg"):
            candidate_path = os.path.join(brand_dir, candidate)
            if os.path.isfile(candidate_path):
                logo_path = candidate_path
                break

    left, middle, right = st.columns([0.55, 5.8, 1.35], gap="medium")
    with left:
        if logo_path:
            st.image(logo_path, width=62)
        else:
            st.markdown("<div style='font-size:2rem;padding-top:10px'>🎬</div>", unsafe_allow_html=True)
    snapshot = st.session_state.workflow_controller.snapshot() if "workflow_controller" in st.session_state else {}
    status = "RUNNING" if snapshot.get("thread_alive") else ("READY" if not snapshot.get("completed") else "DONE")
    status_class = status.lower()
    percent = max(0, min(100, int(snapshot.get("percent", 0) or 0)))
    stage = _ui_text(snapshot.get("stage"), "ready").replace("_", " ").title()
    with middle:
        st.markdown(
            f"<div class='brand-card' title='Shorts Studio · editorial control deck'>"
            f"<div class='brand-eyebrow'><span class='brand-dot'></span> Editorial workspace</div>"
            f"<div class='brand-title-row'><span class='brand-pill'>{title}</span>"
            f"<span class='brand-signature'>2026 editorial OS</span></div>"
            f"<div class='brand-title'>Shorts Studio</div>"
            f"<div class='brand-sub'>{_ui_html(subtitle)}</div>"
            f"<div class='brand-trust-row'>"
            f"<span class='brand-trust-chip'>◉ <strong>Human review</strong></span>"
            f"<span class='brand-trust-chip'>◌ Release gate</span>"
            f"<span class='brand-trust-chip'>✦ Originality aware</span>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
    with right:
        detail = f"{percent}% · {stage}" if status == "RUNNING" else ("Review controls ready" if status == "READY" else "Final state recorded")
        orb_class = "running" if status == "RUNNING" else ""
        st.markdown(
            f"<div class='factory-status status-{status_class}'>"
            f"<div class='factory-status-label'><span class='status-orb {orb_class}'></span>WORKSPACE STATUS</div>"
            f"<div class='factory-status-value'>{_ui_html(status)}</div>"
            f"<div class='factory-status-detail'>{_ui_html(detail)}</div></div>",
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
    st.session_state.confirm_public_upload = False
    st.session_state.candidate_page = 0
    st.session_state.visual_query_story_key = ""
    st.session_state.visual_query_suggestions = []
    st.session_state.visual_query_field_count = 0


def render_workspace_navigation() -> str:
    options = ["Live", "Test"]
    current = st.session_state.get("workspace_mode", "Live")
    if current not in options:
        current = "Live"
    st.sidebar.markdown(
        "<div class='sidebar-brand' title='You found the control deck.'>"
        "<div class='sidebar-brand-mark'>SS</div>"
        "<div class='sidebar-brand-copy'><div class='sidebar-brand-name'>Shorts Studio</div>"
        "<div class='sidebar-brand-meta'>Editorial control deck</div></div></div>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("<div class='sidebar-kicker'>Workspace</div>", unsafe_allow_html=True)
    selected = st.sidebar.radio(
        "Workspace",
        options,
        index=options.index(current),
        key="workspace_mode",
        label_visibility="collapsed",
    )
    return selected


def render_live_navigation() -> Dict[str, Any]:
    """Render the deliberate Live hierarchy and return the production config."""
    st.session_state["live_path_ready"] = False
    _render_section_header(
        "Live",
        "Build a Short",
        "Choose a format, then a topic lane.",
    )

    format_choice = st.pills(
        "Live format",
        ["Deep Dive", "Top 5", "Sports"],
        selection_mode="single",
        default=st.session_state.get("live_format_selection") or None,
        key="live_format_menu",
        required=False,
        label_visibility="collapsed",
        width="stretch",
    )
    previous_format = st.session_state.get("live_format_selection") or ""
    if format_choice and format_choice != previous_format:
        st.session_state.live_format_selection = format_choice
        _clear_live_downstream()
        _clear_live_run_selection()

    live_format = st.session_state.get("live_format_selection") or ""
    if not live_format:
        st.caption("Choose a format to continue.")
        return build_config()

    st.markdown("<div class='live-choice-label'>Topic lane</div>", unsafe_allow_html=True)

    final_path_ready = False
    if live_format in {"Deep Dive", "Top 5"}:
        format_mode = "top5" if live_format == "Top 5" else "regular"
        options = category_options(format_mode, live_format)
        topic_choice = st.pills(
            "Topic",
            list(options.keys()),
            selection_mode="single",
            default=st.session_state.get("live_topic_selection") or None,
            key="live_topic_menu",
            label_visibility="collapsed",
            width="stretch",
            wrap=True,
        )
        previous_topic = st.session_state.get("live_topic_selection") or ""
        if topic_choice and topic_choice != previous_topic:
            st.session_state.live_topic_selection = topic_choice
            _clear_live_run_selection()
        final_path_ready = bool(st.session_state.get("live_topic_selection"))
        st.session_state["live_path_ready"] = final_path_ready

    elif live_format == "Sports":
        sports_choice = st.pills(
            "Sports mode",
            ["Cricket", "Niche Sports", "AI"],
            selection_mode="single",
            default=st.session_state.get("live_sports_selection") or None,
            key="live_sports_menu",
            label_visibility="collapsed",
            width="stretch",
        )
        previous_sports = st.session_state.get("live_sports_selection") or ""
        if sports_choice and sports_choice != previous_sports:
            st.session_state.live_sports_selection = sports_choice
            st.session_state.live_cricket_scope = ""
            st.session_state.live_cricket_scope_menu = None
            _clear_live_run_selection()

        sports_mode = st.session_state.get("live_sports_selection") or ""
        if sports_mode == "Cricket":
            cricket_choice = st.pills(
                "Cricket scope",
                ["India / Asia", "Global"],
                selection_mode="single",
                default=st.session_state.get("live_cricket_scope") or None,
                key="live_cricket_scope_menu",
                label_visibility="collapsed",
                width="stretch",
            )
            if cricket_choice and cricket_choice != (st.session_state.get("live_cricket_scope") or ""):
                st.session_state.live_cricket_scope = cricket_choice
                _clear_live_run_selection()
            final_path_ready = bool(st.session_state.get("live_cricket_scope"))
            st.session_state["live_path_ready"] = final_path_ready
        elif sports_mode in {"Niche Sports", "AI"}:
            final_path_ready = True
            st.session_state["live_path_ready"] = True

    if not final_path_ready:
        st.caption("Choose a topic lane to continue.")
        return build_config()

    with st.expander("Production settings", expanded=False):
        st.caption("Optional release settings.")
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
        f"<div class='meta-row'><span class='meta-chip'>Path · {_ui_html(config.get('display_format'))}</span>"
        f"<span class='meta-chip'>Language · {_ui_html(config.get('language_label'))}</span>"
        f"<span class='meta-chip'>Channel · {_ui_html(config.get('channel'))}</span></div>",
        unsafe_allow_html=True,
    )
    return config


def render_stage_progress(snapshot: Dict[str, Any]) -> None:
    stages = [
        ("Headlines", "discovery"),
        ("Research", "research"),
        ("Script", "script_review"),
        ("Voiceover", "audio"),
        ("Visuals", "visual_approval"),
        ("Render", "render"),
        ("Final QC", "qc"),
    ]
    current = str(snapshot.get("stage") or "idle").strip()
    percent = max(0, min(100, int(snapshot.get("percent", 0) or 0)))
    current_key = {"script": "script_review", "visuals": "visual_approval"}.get(current, current)
    bounds = {
        "discovery": (0, 14),
        "research": (15, 23),
        "script_review": (24, 40),
        "audio": (41, 54),
        "visual_approval": (55, 76),
        "render": (77, 95),
        "qc": (96, 100),
    }
    if current == "error":
        current_key = next((key for key, (lo, hi) in bounds.items() if lo <= percent <= hi), "qc")

    active_index = next((i for i, (_label, key) in enumerate(stages) if key == current_key), -1)
    if snapshot.get("completed"):
        current_key = "qc"
        active_index = len(stages) - 1

    label_map = dict((key, label) for label, key in stages)
    current_label = label_map.get(current_key, "Ready")
    message = _ui_text(snapshot.get("message"), "Ready.")
    if current == "error":
        message = _ui_text(snapshot.get("error") or message, "The run stopped before completion.")

    cards = []
    for index, (label, key) in enumerate(stages):
        if snapshot.get("completed") or (active_index >= 0 and index < active_index):
            state, status = "done", "Done"
        elif key == current_key:
            state = "stopped" if current == "error" else "active"
            status = "Stopped" if current == "error" else "Now"
        else:
            state, status = "", "Next"
        cards.append(
            f"<div class='progress-step {state}'>"
            f"<div class='progress-step-index'>{index + 1:02d}</div>"
            f"<div class='progress-step-name'>{_ui_html(label)}</div>"
            f"<div class='progress-step-state'>{status}</div></div>"
        )

    _render_section_header("Progress", "Production", "")
    st.markdown(
        f"<div class='progress-hero'>"
        f"<div class='progress-hero-head'><div class='progress-hero-title'>{_ui_html(current_label)}</div>"
        f"<div class='progress-hero-value'>{percent}%</div></div>"
        f"<div class='progress-track'><div class='progress-track-fill' style='width:{percent}%'></div></div>"
        f"<div class='progress-meta'><span>{_ui_html(message)}</span><span>Step {max(1, active_index + 1)} of {len(stages)}</span></div>"
        f"<div class='progress-steps'>{''.join(cards)}</div>"
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


def render_script(snapshot: Dict[str, Any]) -> None:
    script_data = snapshot.get("script_data") or {}
    text = _script_text(script_data)
    if not text:
        return

    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    scene_count = len(scenes) if isinstance(scenes, list) else 0
    word_count = len(re.findall(r"\b[\w’'-]+\b", text))
    _render_section_header(
        "Story output",
        "Script",
        "The narration generated from the selected story.",
    )

    with st.container(border=True):
        metric_cols = st.columns(3)
        metric_cols[0].metric("Scenes", scene_count)
        metric_cols[1].metric("Words", word_count)
        metric_cols[2].metric("Status", "Ready")

        previews = [scene for scene in scenes if isinstance(scene, dict)][:2] if isinstance(scenes, list) else []
        for index, scene in enumerate(previews, 1):
            voiceover = _ui_text(scene.get("voiceover"))
            if voiceover:
                st.markdown(
                    f"<div class='output-card'><div class='small-muted'>SCENE {index}</div>"
                    f"<div style='margin-top:5px;line-height:1.5;overflow-wrap:anywhere'>{_ui_html(voiceover)}</div></div>",
                    unsafe_allow_html=True,
                )
        with st.expander("Open full narration", expanded=False):
            st.text_area(
                "Generated narration",
                value=text,
                height=300,
                disabled=True,
                label_visibility="collapsed",
                key="dashboard_script_preview",
            )

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
        st.info("The run is paused at script review. Once the script payload is available, the review appears here; PowerShell remains active while it waits.")
        return

    run_id = str(snapshot.get("run_id") or "current-run").strip() or "current-run"
    visual_pipeline = str(snapshot.get("visual_pipeline") or "option1_scrape").strip()
    if visual_pipeline == "option2_storyboard":
        _render_section_header(
            "Step 04 · Script review",
            "Review the narration",
            "Option 2 will turn this verified script into an original editorial visual storyboard. Manual image-search fields are disabled for this pipeline.",
        )
        with st.container(border=True):
            st.markdown(
                "<div class='output-card'><div class='small-muted'>OPTION 2 · ORIGINAL STORYBOARD</div>"
                "<div style='margin-top:7px;line-height:1.55'>The visual engine will choose lead cards, fact graphics, stat panels, timelines, comparisons, scorecards, process layouts and tactical diagrams from the verified script. It does not scrape news photographs.</div></div>",
                unsafe_allow_html=True,
            )
        if st.button(
            "Continue with Option 2 storyboard",
            type="primary",
            width="stretch",
            key=f"continue_option2_script_{run_id}",
        ):
            controller.submit_script_visual_queries([""] * len(scenes))
            st.rerun()
        return

    _render_section_header(
        "Step 04 · Script review",
        "Review the narration",
        "Visual search terms were finalized before production. This checkpoint no longer searches or assigns images by slide.",
    )

    with st.form(key=f"script_review_{run_id}"):
        for index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict):
                continue
            voiceover = str(scene.get("voiceover") or "").strip()
            with st.container(border=True):
                st.markdown(f"<div class='story-rank'>SLIDE {index:02d}</div>", unsafe_allow_html=True)
                if voiceover:
                    st.markdown(
                        f"<div style='font-size:.92rem;line-height:1.6;margin:7px 0 4px;overflow-wrap:anywhere'>{_ui_html(voiceover)}</div>",
                        unsafe_allow_html=True,
                    )
        submitted = st.form_submit_button(
            "Approve script & continue",
            type="primary",
            width="stretch",
        )

    if submitted:
        if controller.submit_script_visual_queries([]):
            st.rerun()
        else:
            st.error("The script review is no longer active. Refreshing the dashboard.")
            st.rerun()


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
        # Dashboard display is intentionally simple: identity AI verification
        # is the acceptance boundary; provider rights/provenance remain visible
        # as metadata for the human reviewer. Context suitability and soft
        # resolution are deliberately not display filters.
        unused_verified = [dict(item) for item in bank]
        items.append(
            {
                "index": index,
                "path": path if not missing else "",
                "missing": missing,
                "source": str(layer.get("source_type") or "visual"),
                "visual_type": str(layer.get("visual_type") or "visual"),
                "verified": verified,
                "qc_passed": verified and not missing and not bool(layer.get("visual_qc_blocked", False)),
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

    if st_cropper is None:
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
    crop_result = st_cropper(
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

    st.markdown("### Available verified images")
    render_pool_section(
        "All AI-checked images",
        "Every unused image that passed the AI identity check and lenient monetization check is shown here, regardless of context or soft resolution.",
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
    approve_col, reject_col = st.columns([1.35, 1], gap="medium")
    with approve_col:
        if st.button(
            "✅ Approve visuals & continue",
            type="primary",
            width="stretch",
            key="approve_visuals",
            disabled=bool(sum(1 for item in items if not item.get("qc_passed"))),
        ):
            controller.approve_visuals()
            st.rerun()
        unresolved = sum(1 for item in items if not item.get("qc_passed"))
        if unresolved:
            st.caption(f"{unresolved} slide(s) still need a usable image.")
        else:
            st.caption("All slides have an image. Continue to final rendering when ready.")
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

    script_data = snapshot.get("script_data") or {}
    metadata = snapshot.get("final_metadata") or {}
    run_id = str(snapshot.get("run_id") or "")
    pending_metadata = st.session_state.pop("metadata_pending_values", None)
    if (
        isinstance(pending_metadata, dict)
        and str(pending_metadata.get("run_id") or "").strip() == run_id
    ):
        st.session_state["final_title"] = str(pending_metadata.get("title") or "").strip()
        st.session_state["final_description"] = str(pending_metadata.get("description") or "").strip()
        st.session_state["final_comment"] = str(pending_metadata.get("comment") or "").strip()
        st.session_state["metadata_approved"] = True

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
        st.session_state["metadata_approved"] = False

    _render_section_header(
        "Final step",
        "Publish",
        "Approve the final metadata, then choose how the Short is published.",
    )

    current_metadata = {
        "title": str(st.session_state.get("final_title") or metadata.get("title") or script_data.get("title") or ""),
        "description": str(st.session_state.get("final_description") or metadata.get("description") or script_data.get("seo_description") or ""),
        "comment": str(st.session_state.get("final_comment") or metadata.get("pinned_comment") or script_data.get("pinned_comment") or ""),
    }
    fallback_mode = str(script_data.get("fallback_mode") or "").strip()
    public_blocked = fallback_mode == "extractive_source_grounded"

    if public_blocked:
        st.error(
            "PUBLIC UPLOAD BLOCKED — this run used an extractive source-grounded fallback. "
            "Private upload remains available.",
            icon="⛔",
        )

    metadata_approved = bool(st.session_state.get("metadata_approved"))
    with st.container(border=True):
        st.markdown("#### 1 · Metadata")
        st.caption("Approve the exact title, description and pinned comment used for upload.")

        title = st.text_input("YouTube title", max_chars=100, key="final_title", disabled=metadata_approved)
        meta_cols = st.columns(2)
        with meta_cols[0]:
            description = st.text_area("YouTube description", height=140, key="final_description", disabled=metadata_approved)
        with meta_cols[1]:
            comment = st.text_area("Pinned comment", height=140, key="final_comment", disabled=metadata_approved)

        if not metadata_approved:
            approve_col, note_col = st.columns([1, 2])
            with approve_col:
                if st.button("Approve metadata", type="primary", width="stretch", key="approve_metadata"):
                    try:
                        from final_qc_runtime import validate_final_upload_metadata
                        clean_title, clean_description, clean_comment = validate_final_upload_metadata(
                            title, description, comment
                        )
                        st.session_state["metadata_pending_values"] = {
                            "run_id": run_id,
                            "title": clean_title,
                            "description": clean_description,
                            "comment": clean_comment,
                        }
                        st.session_state["metadata_approved"] = True
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Metadata needs attention: {type(exc).__name__}: {exc}")
            with note_col:
                st.caption("Nothing uploads until this approval succeeds.")

        if metadata_approved:
            st.success("Metadata approved.", icon="✅")
            if st.button("Edit metadata", width="content", key="edit_metadata"):
                st.session_state["metadata_approved"] = False
                st.rerun()

    if not (video_path and os.path.isfile(video_path)):
        st.error("The final video path is recorded, but the file is not accessible from the dashboard process.")
        st.code(video_path or "No final video path recorded.", language="text")
        return

    preview_col, publish_col = st.columns([1.35, .65], gap="large")
    with preview_col:
        st.markdown("#### 2 · Watch")
        st.video(video_path)
    with publish_col:
        st.markdown("#### 3 · Publish")
        st.caption("Private stays hidden. Public always requires a second confirmation.")
        upload_unlocked = metadata_approved and not bool(st.session_state.get("upload_result")) and not bool(snapshot.get("uploaded_video_id"))
        public_ready = upload_unlocked and not public_blocked
        if not metadata_approved:
            st.info("Approve metadata to unlock upload.")
        if st.button("Upload Publicly", type="primary", width="stretch", key="upload_public", disabled=not public_ready):
            if public_blocked:
                st.error("Public upload blocked by release policy.")
            else:
                st.session_state["confirm_public_upload"] = True
                st.rerun()
        if public_blocked:
            st.caption("Public publishing is currently blocked by a release policy gate.")
        if st.button("Upload Privately", width="stretch", key="upload_private", disabled=not upload_unlocked):
            st.session_state["confirm_public_upload"] = False
            _perform_upload(
                controller,
                snapshot,
                st.session_state["final_title"],
                st.session_state["final_description"],
                st.session_state["final_comment"],
                "private",
            )

        if st.session_state.get("confirm_public_upload"):
            st.warning("You are about to publish this video publicly. Continue?")
            confirm_col, cancel_col = st.columns(2)
            with confirm_col:
                if st.button("Yes, publish", type="primary", width="stretch", key="confirm_upload_public"):
                    st.session_state["confirm_public_upload"] = False
                    _perform_upload(
                        controller,
                        snapshot,
                        st.session_state["final_title"],
                        st.session_state["final_description"],
                        st.session_state["final_comment"],
                        "public",
                    )
            with cancel_col:
                if st.button("Cancel", width="stretch", key="cancel_upload_public"):
                    st.session_state["confirm_public_upload"] = False
                    st.rerun()

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
        st.rerun()
    except Exception as exc:
        message = str(exc)
        match = re.search(r"accepted video\s+([A-Za-z0-9_-]+)", message)
        video_id = str(getattr(exc, "video_id", "") or (match.group(1) if match else "")).strip()
        if video_id and "kept it private instead of public" in message:
            st.error(message)
            st.info(
                "The video was already created on YouTube. Do not retry this run; "
                "public API publishing requires an eligible/audited Google API project."
            )
            st.link_button(
                "Open the YouTube video",
                f"https://www.youtube.com/watch?v={video_id}",
                width="content",
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
    st.markdown(
        f"<div class='live-bar'><div class='live-bar-copy'><b>Event radar</b> · "
        f"Showing {start_index + 1}–{start_index + len(visible)} of {total} · "
        f"{event_backed} event-backed · {india_led} India-led</div></div>",
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

    try:
        db = sqlite3.connect(ultimate_bot.DB_PATH)
        migrate_vault(db)
        db.close()
    except Exception as exc:
        st.warning(f"Database migration check failed: {exc}")

    _init_state()
    controller: DashboardWorkflowController = st.session_state.workflow_controller
    workspace = render_workspace_navigation()

    if workspace == "Live":
        snapshot = controller.snapshot()
        render_header("Live")
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
