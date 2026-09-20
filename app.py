"""Single supported Streamlit dashboard for the Viral Shorts Factory.

Dashboard-only orchestration lives here. Factory generation modules remain
untouched: this file configures them, presents their progress and gates only
the user-facing visual approval/upload decisions.
"""
from __future__ import annotations

import hashlib
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
from factory_runtime import install_safe_exception_hook, patch_dashboard_runtime
from provider_runtime import patch_provider_adapters
from quality_runtime import patch_quality_control
from runtime_bindings import bind_dashboard_patches
from story_ranker import patch_story_selection
from visual_content_runtime import patch_content_first_visuals as patch_visual_pipeline
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
    evaluate_live_qc_gates,
    live_qc_passes,
)


MAX_DASHBOARD_DISCOVERY_HEADLINES = 28

st.set_page_config(page_title="Viral Shorts Factory", page_icon="🎬", layout="wide")

st.markdown("""<style>
:root{
  --bg:#f3f5f8;--surface:#ffffff;--surface-soft:#f8fafc;--line:#e1e5eb;--line-strong:#cfd5df;
  --text:#151a24;--muted:#667085;--muted-2:#8a93a3;--accent:#5b46e8;--accent-deep:#4632c7;
  --accent-soft:#efedff;--good:#147a50;--good-soft:#eaf7f0;--warn:#a35b04;--warn-soft:#fff4e2;
  --shadow:0 10px 30px rgba(20,27,39,.07);--shadow-lg:0 18px 48px rgba(20,27,39,.10);
}
html,body,[data-testid="stAppViewContainer"]{background:var(--bg);color:var(--text)}
[data-testid="stHeader"]{background:rgba(243,245,248,.94);border-bottom:1px solid rgba(207,213,223,.7)}
.block-container{max-width:1380px;padding-top:1.45rem;padding-bottom:3rem}
section[data-testid="stSidebar"]{background:#11151d;border-right:1px solid #252b35;color:#eef2f7}
section[data-testid="stSidebar"]>div{padding-top:1.15rem}
section[data-testid="stSidebar"] .stMarkdown p,section[data-testid="stSidebar"] label,section[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{color:#b9c1cf}
*{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif!important;letter-spacing:-.01em}
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
.release-gates{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.release-gate{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:11px 12px}
.release-gate.pass{border-color:#c6e8d6;background:linear-gradient(180deg,#fff,#f7fcf9)}
.release-gate.block{border-color:#f0d8b0;background:linear-gradient(180deg,#fff,#fffbf4)}
.release-gate-name{font-size:.75rem;font-weight:850;color:var(--text)}
.release-gate-detail{color:var(--muted);font-size:.69rem;line-height:1.4;margin-top:3px}
.live-bar{display:flex;align-items:center;justify-content:space-between;gap:12px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:10px 13px;margin:10px 0 16px;box-shadow:var(--shadow)}
.live-bar-copy{color:var(--muted);font-size:.75rem}.live-bar-copy b{color:var(--text)}
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
.crop-shell{background:#f7f8fb;border:1px solid var(--line);border-radius:16px;padding:12px}
.crop-caption{color:var(--muted);font-size:.73rem;line-height:1.45;margin-bottom:9px}
.dashboard-footer{text-align:center;color:var(--muted-2);font-size:.7rem;padding:10px 0}
@media(max-width:1100px){.stage-strip{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:900px){.qc-guide{grid-template-columns:1fr}.release-gates{grid-template-columns:1fr}.brand-title{font-size:1.6rem}}
@media(max-width:700px){.stage-strip{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>""", unsafe_allow_html=True)

REQUIRED_SECRET_NAMES = (
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "UNSPLASH_ACCESS_KEY",
    "HF_TOKEN",
    "PEXELS_API_KEY",
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
        install_safe_exception_hook()
        patch_dashboard_runtime(ultimate_bot)
        patch_story_selection(ultimate_bot)
        patch_quality_control(ultimate_bot)
        install_visual_qa_bridge(visual_runtime)
        patch_visual_pipeline(ultimate_bot)
        from audio_runtime import patch_audio_pipeline
        patch_audio_pipeline(ultimate_bot)
        patch_provider_adapters(ultimate_bot)
        ultimate_bot.run_analytics_sweep = lambda _conn: print(
            "[Learning] Automatic analytics sync disabled in newsroom workflow.", flush=True
        )
        ultimate_bot._dashboard_runtime_initialized = True
    else:
        install_visual_qa_bridge(visual_runtime)
        patch_provider_adapters(ultimate_bot)

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
    defaults = {
        "workflow_controller": DashboardWorkflowController(ultimate_bot),
        "candidates": [],
        "web_config": {},
        "production_started": False,
        "final_qc": False,
        "upload_result": "",
        "confirm_public_upload": False,
        "candidate_page": 0,
        "selected_channel": _channel_options()[0],
        "last_demo_results": {},
        "show_offline_diagnostics": False,
        "offline_diagnostics": {},
        "pending_candidate": None,
        "visual_search_queries": "",
        "discovery_headline_selection": None,
        "editorial_mode": "Deep Dive",
        "metadata_approved": False,
        "metadata_loaded_run_id": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_run() -> None:
    controller: DashboardWorkflowController = st.session_state.workflow_controller
    controller.reset()
    for key, value in {
        "candidates": [],
        "web_config": {},
        "production_started": False,
        "final_qc": False,
        "upload_result": "",
        "confirm_public_upload": False,
        "candidate_page": 0,
        "final_title": "",
        "final_description": "",
        "final_comment": "",
        "pending_candidate": None,
        "visual_search_queries": "",
        "discovery_headline_selection": None,
        "metadata_approved": False,
        "metadata_loaded_run_id": "",
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
    "sports",
)
TOP_FIVE_TOPICS = (
    "national_global_affairs",
    "technology",
    "business_finance",
    "entertainment",
    "viral_phenomenon",
    "sports",
)


def category_options(format_mode: str, editorial_mode: str = "Deep Dive") -> Dict[str, str]:
    keys = TOP_FIVE_TOPICS if editorial_mode == "Top Five" else DEEP_DIVE_TOPICS
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


def build_config() -> Dict[str, Any]:
    language_options = {cfg["label"]: key for key, cfg in ultimate_bot.LANGUAGES.items()}
    language_label = st.session_state.get("language_label", "English")
    language_key = language_options.get(language_label, "english")
    mode = str(st.session_state.get("editorial_mode", "Deep Dive"))
    if mode == "Top 5":
        mode = "Top Five"

    if mode == "Cricket":
        return {
            "format_mode": "regular",
            "display_format": "Cricket",
            "editorial_mode": "Cricket",
            "category": "sports_stories_of_day",
            "language": language_key,
            "language_label": language_label,
            "channel": st.session_state.get("selected_channel", _channel_options()[0]),
            "cricket_pipeline": True,
            "cricket_category": st.session_state.get("cricket_category", "AI-assisted top story in cricket"),
            "requested_topic": str(st.session_state.get("requested_topic", "") or "").strip(),
        }

    if mode == "AI":
        return {
            "format_mode": "regular",
            "display_format": "AI",
            "editorial_mode": "AI",
            "category": "ai_recommendation",
            "language": language_key,
            "language_label": language_label,
            "channel": st.session_state.get("selected_channel", _channel_options()[0]),
            "cricket_pipeline": False,
        }

    format_mode = "top5" if mode == "Top Five" else "regular"
    options = category_options(format_mode, mode)
    default_key = next(iter(options.values()))
    category_key = st.session_state.get("category_key", default_key)
    if category_key not in options.values():
        category_key = default_key
    return {
        "format_mode": format_mode,
        "display_format": mode,
        "editorial_mode": mode,
        "category": category_key,
        "language": language_key,
        "language_label": language_label,
        "channel": st.session_state.get("selected_channel", _channel_options()[0]),
        "cricket_pipeline": False,
    }


def _render_section_header(kicker: str, title: str, subtitle: str = "") -> None:
    st.markdown(
        f"<div class='section-kicker'>{kicker}</div>"
        f"<div class='section-title'>{title}</div>"
        + (f"<div class='section-subtitle'>{subtitle}</div>" if subtitle else ""),
        unsafe_allow_html=True,
    )

def render_header(action_mode: str) -> None:
    titles = {
        "Live Factory": ("Live Factory", "Create, review and release a Short."),
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

    left, middle, right = st.columns([0.75, 5.45, 1.4], gap="medium")
    with left:
        if logo_path:
            st.image(logo_path, width=72)
        else:
            st.markdown("<div style='font-size:2.3rem;padding-top:10px'>🎬</div>", unsafe_allow_html=True)
    with middle:
        st.markdown(
            f"<div class='brand-card'><span class='brand-pill'>{title}</span>"
            f"<div class='brand-title'>Viral Shorts Factory</div>"
            f"<div class='brand-sub'>{subtitle}</div></div>",
            unsafe_allow_html=True,
        )
    with right:
        snapshot = st.session_state.workflow_controller.snapshot() if "workflow_controller" in st.session_state else {}
        status = "RUNNING" if snapshot.get("thread_alive") else ("DONE" if snapshot.get("completed") else "READY")
        st.markdown(
            f"<div class='factory-status'><div class='factory-status-label'>FACTORY STATUS</div>"
            f"<div class='factory-status-value'>{status}</div></div>",
            unsafe_allow_html=True,
        )

def render_workspace_navigation() -> str:
    options = [
        "Live Factory",
        "Channel Statistics",
        "Run Offline Diagnostics",
        "Demo Factory",
        "Final Branding Preview",
    ]
    current = st.session_state.get("dashboard_utility", "Live Factory")
    if current == "None" or current not in options:
        current = "Live Factory"
    st.sidebar.markdown("<div class='sidebar-kicker'>Workspace</div>", unsafe_allow_html=True)
    selected = st.sidebar.selectbox(
        "Go to",
        options,
        index=options.index(current),
        key="dashboard_utility",
        label_visibility="collapsed",
    )
    return selected

def render_sidebar_controls() -> Dict[str, Any]:
    st.sidebar.markdown("<div class='sidebar-kicker'>Production workspace</div>", unsafe_allow_html=True)
    st.sidebar.markdown("<div class='sidebar-title'>Factory setup</div>", unsafe_allow_html=True)

    channel_options = _channel_options()
    st.sidebar.selectbox("Channel", channel_options, key="selected_channel")

    language_options = {cfg["label"]: key for key, cfg in ultimate_bot.LANGUAGES.items()}
    language_labels = list(language_options.keys())
    current_language = st.session_state.get("language_label", language_labels[0])
    st.sidebar.selectbox(
        "Language",
        language_labels,
        index=language_labels.index(current_language),
        key="language_label",
    )

    mode_labels = ["Deep Dive", "Top 5", "Cricket", "AI"]
    current_mode = st.session_state.get("editorial_mode", mode_labels[0])
    if current_mode == "Top Five":
        current_mode = "Top 5"
    st.sidebar.selectbox(
        "Editorial mode",
        mode_labels,
        index=mode_labels.index(current_mode),
        key="editorial_mode_label",
        help="Deep Dive and Top Five use curated topic menus. Cricket keeps its dedicated cricket intake. AI ranks current stories against channel history.",
    )
    st.session_state.editorial_mode = "Top Five" if st.session_state.editorial_mode_label == "Top 5" else st.session_state.editorial_mode_label

    if st.session_state.editorial_mode == "Cricket":
        st.sidebar.selectbox("Cricket category", list(CRICKET_CATEGORIES.keys()), key="cricket_category")
        st.sidebar.text_input(
            "Specific topic (optional)",
            placeholder="e.g. BCCI to suspend Impact Player rule",
            key="requested_topic",
        )
    elif st.session_state.editorial_mode == "AI":
        with st.sidebar.expander("How AI mode works", expanded=False):
            st.caption("AI mode blends current news, channel history, genre fit, vault topics and trend signals into a Top 10.")
    else:
        options = category_options(
            "top5" if st.session_state.editorial_mode == "Top Five" else "regular",
            st.session_state.editorial_mode,
        )
        labels = list(options.keys())
        current_key = st.session_state.get("category_key", next(iter(options.values())))
        current_label = next((label for label, key in options.items() if key == current_key), labels[0])
        selected_label = st.sidebar.selectbox(
            "Topic / category",
            labels,
            index=labels.index(current_label),
            key="category_label",
        )
        st.session_state.category_key = options[selected_label]

    sidebar_snapshot = st.session_state.workflow_controller.snapshot()
    st.sidebar.divider()
    if sidebar_snapshot.get("thread_alive"):
        status_title = f"Running · {int(sidebar_snapshot.get('percent', 0) or 0)}%"
        status_copy = str(sidebar_snapshot.get("message") or "Factory production is active.").strip()
    elif sidebar_snapshot.get("completed"):
        status_title = "Latest run complete"
        status_copy = "Review the generated Short below."
    elif sidebar_snapshot.get("stage") == "error":
        status_title = "Latest run stopped"
        status_copy = str(sidebar_snapshot.get("error") or "The factory stopped with an error.").strip()
    else:
        status_title = "Ready"
        status_copy = "No production run is active."

    st.sidebar.markdown(
        f"<div class='sidebar-status'><div class='sidebar-status-title'>{status_title}</div>"
        f"<div class='sidebar-status-copy'>{status_copy}</div></div>",
        unsafe_allow_html=True,
    )

    if st.sidebar.button(
        "Reset current run",
        width="stretch",
        disabled=bool(sidebar_snapshot.get("thread_alive")),
    ):
        reset_run()
        st.rerun()

    return build_config()

def render_stage_progress(snapshot: Dict[str, Any]) -> None:
    stages = [
        ("Discovery", "discovery", 10, 14),
        ("Research", "research", 15, 23),
        ("Script", "script", 24, 38),
        ("Review", "script_review", 39, 40),
        ("Voiceover", "audio", 41, 54),
        ("Visuals", "visuals", 55, 75),
        ("Visual QC", "visual_approval", 76, 76),
        ("Render", "render", 77, 95),
        ("Final QC", "qc", 96, 100),
    ]
    current = str(snapshot.get("stage") or "idle")
    percent = int(snapshot.get("percent", 0) or 0)

    _render_section_header(
        "Production pipeline",
        "Factory progress",
        "One overall progress bar, with each stage reduced to a simple status.",
    )
    st.progress(max(0.0, min(1.0, percent / 100)), text=f"{percent}% complete")

    cards = []
    for label, key, _lo, hi in stages:
        if current == "error":
            state, css_class, icon = "Stopped", "stopped", "⚠️"
        elif percent >= hi:
            state, css_class, icon = "Done", "done", "✓"
        elif current == key:
            state, css_class, icon = "Now", "active", "●"
        else:
            state, css_class, icon = "Next", "", "○"
        cards.append(
            f"<div class='stage-card {css_class}'><div class='stage-name'>{icon} {label}</div>"
            f"<div class='stage-state'>{state}</div></div>"
        )
    st.markdown("<div class='stage-strip'>" + "".join(cards) + "</div>", unsafe_allow_html=True)

    message = str(snapshot.get("message") or "").strip()
    if message:
        st.markdown(
            f"<div class='live-bar'><div class='live-bar-copy'><b>Now</b> · {message}</div></div>",
            unsafe_allow_html=True,
        )

def _script_text(script_data: Dict[str, Any]) -> str:
    scenes = script_data.get("script", [])
    if not isinstance(scenes, list):
        return ""
    blocks = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        voiceover = str(scene.get("voiceover", "") or "").strip()
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
            voiceover = str(scene.get("voiceover") or "").strip()
            if voiceover:
                st.markdown(
                    f"<div class='output-card'><div class='small-muted'>SCENE {index}</div>"
                    f"<div style='margin-top:5px;line-height:1.5'>{voiceover}</div></div>",
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
        return

    run_id = str(snapshot.get("run_id") or "current-run").strip() or "current-run"
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
                        f"<div style='font-size:.92rem;line-height:1.6;margin:7px 0 4px'>{voiceover}</div>",
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
        factory_rejected = [
            dict(item)
            for item in bank
            if str(item.get("status") or "").strip() == "factory-rejected-resolution"
            or str(item.get("scene_status") or "").strip() == "resolution-rejected"
        ]
        scene_rejected = [
            dict(item)
            for item in bank
            if str(item.get("scene_status") or "").strip() == "scene-rejected"
        ]
        unused_verified = [
            dict(item)
            for item in bank
            if str(item.get("scene_status") or "").strip() in {"", "good-unused"}
        ]
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
                "factory_rejected": factory_rejected,
                "scene_rejected": scene_rejected,
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
    available = [item for item in pool if not bool(item.get("used")) and str(item.get("status") or "") != "factory-rejected-resolution"]
    rejected = [item for item in pool if not bool(item.get("used")) and str(item.get("status") or "") == "factory-rejected-resolution"]

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
        "Every slide already has an image. The shared pools below contain additional choices; "
        "assign any unused image to one slide, then reframe it with the drag cropper when needed."
    )

    metric_cols = st.columns(4, gap="small")
    metric_cols[0].metric("Slides", len(items))
    metric_cols[1].metric("Ready", ready_count)
    metric_cols[2].metric("Pool images", len(available) + len(rejected))
    metric_cols[3].metric("New search", active_search_count)

    crop_target = str(st.session_state.get("visual_pool_crop_target") or "").strip()
    crop_asset = None
    if crop_target:
        for candidate in pool:
            if str(candidate.get("hash") or "").strip() == crop_target:
                crop_asset = candidate
                break
        if crop_asset is None:
            for group in search_groups:
                for candidate in group.get("items") or []:
                    if isinstance(candidate, dict) and str(candidate.get("hash") or "").strip() == crop_target:
                        crop_asset = candidate
                        break
                if crop_asset is not None:
                    break

    if crop_asset is not None:
        with st.container(border=True):
            st.markdown("<div class='qc-card-title'>Crop / reframe selected image</div>", unsafe_allow_html=True)
            crop_path = str(crop_asset.get("path") or "").strip()
            if crop_path and os.path.isfile(crop_path):
                from PIL import Image
                crop_image = Image.open(crop_path).convert("RGB")
                stored_box = crop_asset.get("crop_box") or {}
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
                if st_cropper is None:
                    st.warning("Interactive cropping is unavailable in this Python environment.")
                else:
                    crop_left, crop_right = st.columns([1.2, 0.8], gap="medium")
                    with crop_left:
                        crop_result = st_cropper(
                            img_file=crop_image,
                            realtime_update=True,
                            default_coords=default_coords,
                            box_color="#177fd1",
                            aspect_ratio=(9, 16),
                            box_algorithm=_recommended_shorts_crop_box,
                            return_type="both",
                            key=f"visual_pool_cropper_{run_id}_{crop_target[:12]}",
                            should_resize_image=False,
                            stroke_width=3,
                        )
                        if isinstance(crop_result, tuple) and len(crop_result) == 2:
                            crop_preview, crop_box = crop_result
                        else:
                            crop_preview, crop_box = crop_result, {}
                    with crop_right:
                        st.markdown("**Shorts preview**")
                        if crop_preview is not None:
                            st.image(crop_preview, width=240)
                        st.caption("Drag and resize the 9:16 frame. No AI or provider call.")
                    action_cols = st.columns([1, 1])
                    with action_cols[0]:
                        if isinstance(crop_box, dict) and crop_box and st.button(
                            "Apply crop",
                            type="primary",
                            width="stretch",
                            key=f"apply_pool_crop_{run_id}_{crop_target[:12]}",
                        ):
                            ok, message = controller.crop_visual_pool_asset(crop_target, crop_box)
                            if ok:
                                st.session_state.visual_pool_crop_target = ""
                                st.rerun()
                            st.error(message)
                    with action_cols[1]:
                        if st.button(
                            "Close crop editor",
                            width="stretch",
                            key=f"close_pool_crop_{run_id}_{crop_target[:12]}",
                        ):
                            st.session_state.visual_pool_crop_target = ""
                            st.rerun()

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
                        st.image(item["path"], width=280)
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
                        with st.expander("Crop / reframe", expanded=False):
                            from PIL import Image
                            original_image = Image.open(original_path).convert("RGB")
                            stored_box = item.get("crop_box") or {}
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
                            if st_cropper is not None:
                                crop_result = st_cropper(
                                    img_file=original_image,
                                    realtime_update=True,
                                    default_coords=default_coords,
                                    box_color="#177fd1",
                                    aspect_ratio=(9, 16),
                                    box_algorithm=_recommended_shorts_crop_box,
                                    return_type="both",
                                    key=f"chosen_cropper_{run_id}_{item['index']}",
                                    should_resize_image=False,
                                    stroke_width=3,
                                )
                                if isinstance(crop_result, tuple) and len(crop_result) == 2:
                                    crop_preview, crop_box = crop_result
                                else:
                                    crop_preview, crop_box = crop_result, {}
                                if crop_preview is not None:
                                    st.image(crop_preview, width=180)
                                if isinstance(crop_box, dict) and crop_box and st.button(
                                    "Apply crop",
                                    type="primary",
                                    width="stretch",
                                    key=f"apply_chosen_crop_{run_id}_{item['index']}",
                                ):
                                    ok, message = controller.crop_visual(item["index"], crop_box=crop_box)
                                    if ok:
                                        st.rerun()
                                    st.error(message)
                            else:
                                st.warning("Interactive cropping is unavailable in this Python environment.")

    def render_pool_section(title: str, description: str, assets: list[dict], section_key: str, rejected_section: bool = False) -> None:
        st.markdown(
            f"<div class='qc-pool-heading'><b>{title}</b><span>{description}</span></div>",
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
                            st.image(path, width=260)
                        query = str(asset.get("query") or "").strip()
                        source = str(asset.get("source") or "").strip()
                        caption = source or "visual source"
                        if query:
                            caption += f" · {query}"
                        st.caption(caption)
                        if rejected_section:
                            st.caption("Identity confirmed · low resolution")
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
                                st.session_state.visual_pool_crop_target = asset_hash
                                st.rerun()
                        if bool(asset.get("used")):
                            st.caption(f"Used on slide {int(asset.get('assigned_slide') or 0)}")

    st.markdown("### Available verified images")
    render_pool_section(
        "Unused verified pool",
        "Images that passed monetization and identity checks but are not currently assigned to a slide.",
        available,
        "verified",
    )

    st.markdown("### Identity-verified, lower-resolution images")
    render_pool_section(
        "Resolution review",
        "These passed the identity test but fell below the normal resolution target; you can still use them manually.",
        rejected,
        "rejected",
        rejected_section=True,
    )

    st.markdown("### New manual searches")
    st.caption("Each search returns up to five NEW monetization-safe images. Identity is intentionally left to your manual QC for these one-off searches.")
    for group in search_groups:
        group_id = str(group.get("id") or "search")
        group_items = [dict(item) for item in (group.get("items") or []) if isinstance(item, dict)]
        st.markdown(
            f"<div class='qc-pool-heading'><b>Search · {group.get('query','')}</b><span>{len(group_items)} result(s)</span></div>",
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
                        caption = str(asset.get("source") or "monetization-safe source").strip()
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
                                    st.session_state.visual_pool_crop_target = asset_hash
                                    st.rerun()

    search_query = st.text_input(
        "Search for five new images",
        placeholder="e.g. Virat Kohli BCCI India, BCCI logo, India women's cricket",
        key=f"global_visual_search_{run_id}",
    )
    if st.button(
        "Search 5 new images",
        type="secondary",
        width="stretch",
        key=f"global_visual_search_button_{run_id}",
    ):
        ok, message = controller.search_visual_pool(search_query)
        if ok:
            st.success(message)
            st.rerun()
        else:
            st.error(message)

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


def render_activity_timeline(snapshot: Dict[str, Any]) -> None:
    events = snapshot.get("activity_events") or []
    if not events:
        return

    _render_section_header(
        "Run activity",
        "What the factory is doing",
        "Recent milestones from the active production run.",
    )
    recent = events[-8:]
    rows = []
    for index, event in enumerate(recent):
        active = index == len(recent) - 1 and snapshot.get("thread_alive")
        icon = "●" if active else "✓"
        rows.append(
            f"<div class='timeline-row'><div class='timeline-dot'>{icon}</div>"
            f"<div class='timeline-main'><div class='timeline-head'>{event.get('stage', 'Factory')}"
            f"<span class='timeline-time'>{event.get('time', '')}</span></div>"
            f"<div class='timeline-message'>{event.get('message', '')}</div></div></div>"
        )
    st.markdown("<div class='timeline'>" + "".join(rows) + "</div>", unsafe_allow_html=True)

    if len(events) > len(recent):
        with st.expander(f"Earlier activity · {len(events) - len(recent)} events", expanded=False):
            for event in events[:-len(recent)]:
                st.caption(
                    f"{event.get('stage', 'Factory')} · {event.get('time', '')} · {event.get('message', '')}"
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
            f"<div style='font-size:1.08rem;font-weight:780;line-height:1.4'>{headline}</div>",
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

def render_visual_details(snapshot: Dict[str, Any]) -> None:
    items = _visual_items(snapshot)
    if not items:
        return
    with st.expander("Visual sourcing details", expanded=False):
        st.caption("Provider, manual-search and rescue details are kept here so the main review stays visual.")
        for item in items:
            details = [f"Visual {item['index']}: {item['visual_type']} · {item['source']}"]
            if item.get("manual_query"):
                details.append(f"Manual query: {item['manual_query']}")
            if item.get("rescue_reason"):
                details.append(f"Rescue: {item['rescue_reason']}")
            st.caption(" — ".join(details))

def render_powershell_output(lines: list[str]) -> None:
    with st.popover(
        "🖥️ Open exact PowerShell output",
        type="secondary",
        width="stretch",
        help="Open the exact stdout/stderr captured from the active factory worker.",
    ):
        st.caption("Exact stdout/stderr captured from the active factory worker.")
        if lines:
            st.code("\n".join(lines), language="powershell")
        else:
            st.info("No factory console output has been captured yet.")

def render_console(snapshot: Dict[str, Any]) -> None:
    lines = snapshot.get("console_lines") or []
    if not lines:
        return

    latest = lines[-1]
    upload_match = re.search(r"\[Upload Progress\]\s*(\d+)%", latest)
    render_match = re.search(r"(?:Rendering Video Scenes|Writing video file).*?(\d{1,3})%", latest)
    operation_percent = None
    operation_label = ""
    if upload_match:
        operation_percent = int(upload_match.group(1))
        operation_label = "YouTube upload"
    elif render_match:
        operation_percent = int(render_match.group(1))
        operation_label = "Final video render"

    st.markdown("### Live factory activity")
    if operation_percent is not None:
        st.markdown(f"**{operation_label}** · {operation_percent}%")
        st.progress(max(0.0, min(1.0, operation_percent / 100)))
    render_powershell_output(lines)


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
    cols[2].metric("Visuals", f"{len(visuals)} ready" if visuals else "Waiting")
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
        "QC & publish",
        "Review the gates, approve the metadata, then choose how the Short is published.",
    )

    current_metadata = {
        "title": str(st.session_state.get("final_title") or metadata.get("title") or script_data.get("title") or ""),
        "description": str(st.session_state.get("final_description") or metadata.get("description") or script_data.get("seo_description") or ""),
        "comment": str(st.session_state.get("final_comment") or metadata.get("pinned_comment") or script_data.get("pinned_comment") or ""),
    }
    gates = evaluate_live_qc_gates(snapshot, current_metadata)
    passed_count = sum(1 for gate in gates if gate["passed"])
    qc_ready = passed_count == len(gates)
    public_blocked = any(bool(gate.get("public_blocked")) for gate in gates)
    fallback_mode = str(script_data.get("fallback_mode") or "").strip()

    if fallback_mode == "extractive_source_grounded":
        st.error(
            "PUBLIC UPLOAD BLOCKED — this run used an extractive source-grounded fallback. "
            "Private upload remains available after the other QC gates pass.",
            icon="⛔",
        )

    st.markdown(
        f"<div class='live-bar'><div class='live-bar-copy'><b>Release QC</b> · "
        f"{passed_count}/{len(gates)} gates passing</div><div class='small-muted'>"
        f"{'READY' if qc_ready else 'LOCKED'}</div></div>",
        unsafe_allow_html=True,
    )

    gate_html = []
    for gate in gates:
        passed = bool(gate.get("passed"))
        gate_class = "pass" if passed else "block"
        gate_html.append(
            f"<div class='release-gate {gate_class}'><div class='release-gate-name'>"
            f"{'✓' if passed else '✕'} {gate.get('label', '')}</div>"
            f"<div class='release-gate-detail'>{gate.get('detail', '')}</div></div>"
        )
    st.markdown("<div class='release-gates'>" + "".join(gate_html) + "</div>", unsafe_allow_html=True)

    editing = not bool(st.session_state.get("metadata_approved"))
    with st.container(border=True):
        st.markdown("#### 1 · Metadata")
        st.caption("Approve the exact title, description and pinned comment used for upload.")

        title = st.text_input("YouTube title", max_chars=100, key="final_title", disabled=not editing)
        meta_cols = st.columns(2)
        with meta_cols[0]:
            description = st.text_area("YouTube description", height=140, key="final_description", disabled=not editing)
        with meta_cols[1]:
            comment = st.text_area("Pinned comment", height=140, key="final_comment", disabled=not editing)

        if editing:
            approve_col, note_col = st.columns([1, 2])
            with approve_col:
                if st.button("Approve metadata", type="primary", width="stretch", key="approve_metadata"):
                    try:
                        from final_qc_runtime import validate_final_upload_metadata
                        clean_title, clean_description, clean_comment = validate_final_upload_metadata(
                            title, description, comment
                        )
                        st.session_state["final_title"] = clean_title
                        st.session_state["final_description"] = clean_description
                        st.session_state["final_comment"] = clean_comment
                        st.session_state["metadata_approved"] = True
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Metadata needs attention: {type(exc).__name__}: {exc}")
            with note_col:
                st.caption("Nothing uploads until this approval succeeds.")
            return

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
        st.caption("Private stays hidden. Public needs explicit confirmation.")
        if st.button("Upload Publicly", type="primary", width="stretch", key="upload_public", disabled=not qc_ready):
            if not live_qc_passes(snapshot, {"title": title, "description": description, "comment": comment}):
                st.error("Public upload blocked: release QC is no longer passing.")
            else:
                st.session_state["confirm_public_upload"] = True
                st.rerun()
        if public_blocked:
            st.caption("Public publishing is currently blocked by a release policy gate.")
        if st.button("Upload Privately", width="stretch", key="upload_private", disabled=not qc_ready):
            if not live_qc_passes(snapshot, {"title": title, "description": description, "comment": comment}):
                st.error("Private upload blocked: release QC is no longer passing.")
            else:
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
                    if not live_qc_passes(snapshot, {"title": title, "description": description, "comment": comment}):
                        st.error("Upload blocked: one or more live QC gates are not passing.")
                    else:
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
        st.error(f"Upload failed: {type(exc).__name__}: {exc}")


def render_live_monitor(controller: DashboardWorkflowController) -> None:
    @st.fragment(run_every="1s")
    def _fragment():
        snapshot = controller.snapshot()
        render_stage_progress(snapshot)

        selected = snapshot.get("selected_story") or {}
        if selected:
            st.markdown(
                f"<div class='panel'><div class='small-muted'>SELECTED TOPIC</div><b>{selected.get('title', '')}</b></div>",
                unsafe_allow_html=True,
            )

        render_research_summary(snapshot)
        if snapshot.get("script_review_required"):
            render_script_visual_query_review(controller, snapshot)
        else:
            render_script(snapshot)
        render_audio_preview(snapshot)
        render_generated_outputs(snapshot)

        if snapshot.get("visual_review_required"):
            render_visual_review(controller, snapshot)
        render_visual_details(snapshot)

        render_activity_timeline(snapshot)
        render_console(snapshot)
        render_logs(snapshot)

        if snapshot.get("stage") == "error":
            st.error(snapshot.get("error") or "The factory stopped with an error.")

        render_upload_panel(controller, snapshot)

    _fragment()


def render_live_factory(config: Dict[str, Any], controller: DashboardWorkflowController) -> None:
    problems = check_required_local_assets()
    live_blockers = [item for item in problems if "provider key" in item]
    if live_blockers:
        st.warning(
            "Some live provider keys are not configured. Discovery or production may stop when that provider is required."
        )

    _render_section_header(
        "Step 01 · Discovery",
        "Build a Short",
        "Choose a ranked story, optionally guide the visual search, then start production.",
    )

    if not st.session_state.candidates:
        mode_label = str(config.get("display_format") or config.get("editorial_mode") or "Deep Dive")
        language_label = str(config.get("language_label") or "English")
        category_label = str(config.get("category") or "Automatic").replace("_", " ").title()
        with st.container():
            st.markdown(
                "<div class='empty-state'><div class='empty-title'>Ready for a new Short</div>"
                "<div class='empty-copy'>Search current stories and keep the final story choice in your hands. "
                "The factory handles the ranking; you handle the decision.</div></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div class='meta-row'><span class='meta-chip'>Mode · {mode_label}</span>"
                f"<span class='meta-chip'>Language · {language_label}</span>"
                f"<span class='meta-chip'>Category · {category_label}</span></div>",
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
                            )
                        else:
                            candidates = discover_ranked_topics(
                                ultimate_bot,
                                config,
                                conn,
                                max_candidates=MAX_DASHBOARD_DISCOVERY_HEADLINES,
                            )
                    finally:
                        conn.close()
                    for candidate in candidates:
                        candidate["dashboard_discovery_pool"] = True
                    st.session_state.candidates = candidates
                    st.session_state.web_config = config
                    st.session_state.production_started = False
                    st.session_state.final_qc = False
                    st.session_state.upload_result = ""
                    st.session_state.candidate_page = 0
                    st.session_state.discovery_headline_selection = None
                    st.success(f"Found {len(candidates)} ranked headlines. Choose one below.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Topic discovery failed: {type(exc).__name__}: {exc}")
        return

    if st.session_state.production_started:
        render_live_monitor(controller)
        return

    pending_candidate = st.session_state.get("pending_candidate")
    if pending_candidate:
        headline = str(pending_candidate.get("title") or "Untitled story").strip()
        source = str(pending_candidate.get("source_label") or "News source").strip()
        url = str(pending_candidate.get("story_url") or "").strip()

        _render_section_header(
            "Step 02",
            "Review your story",
            "Confirm the headline before the factory spends time producing it.",
        )
        with st.container(border=True):
            st.markdown("<div class='story-rank'>SELECTED HEADLINE</div>", unsafe_allow_html=True)
            st.markdown(
                f"<div class='story-title' style='font-size:1.35rem'>{headline}</div>",
                unsafe_allow_html=True,
            )
            st.caption(f"Source · {source}")
            if url.startswith(("http://", "https://")):
                st.link_button("Open source article", url, width="content")
            with st.expander("Optional visual-search hints", expanded=False):
                st.caption("Add exact image searches up front. Leave blank to keep automatic visual search.")
                st.text_input(
                    "Search queries",
                    placeholder="e.g. India Afghanistan cricket match; Shubman Gill batting; New Delhi cricket stadium",
                    key="visual_search_queries",
                )

        start_col, cancel_col = st.columns([1.5, 1])
        with start_col:
            if st.button("Start production", type="primary", width="stretch", key="start_selected_topic"):
                config = dict(st.session_state.web_config)
                config["visual_search_queries"] = str(st.session_state.get("visual_search_queries", "") or "").strip()
                if config.get("editorial_mode") == "AI":
                    config["category"] = str(pending_candidate.get("recommended_category") or "national_global_affairs")
                    config["format_mode"] = str(pending_candidate.get("recommended_format") or "regular")
                st.session_state.production_started = True
                st.session_state.final_qc = False
                st.session_state.upload_result = ""
                controller.start_production(config, dict(pending_candidate))
                st.rerun()
        with cancel_col:
            if st.button("Choose another headline", width="stretch", key="cancel_selected_topic"):
                st.session_state.pending_candidate = None
                st.session_state.visual_search_queries = ""
                st.rerun()
        return

    candidates = st.session_state.candidates
    total = min(len(candidates), MAX_DASHBOARD_DISCOVERY_HEADLINES)
    page_size = 5
    page_count = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(int(st.session_state.get("candidate_page", 0) or 0), page_count - 1))
    start_index = page * page_size
    visible = candidates[start_index:start_index + page_size]

    st.markdown(
        f"<div class='live-bar'><div class='live-bar-copy'><b>Ranked headlines</b> · "
        f"Showing {start_index + 1}–{start_index + len(visible)} of {total}</div></div>",
        unsafe_allow_html=True,
    )

    for row_start in range(0, len(visible), 2):
        row = visible[row_start:row_start + 2]
        cols = st.columns(len(row), gap="medium")
        for local_index, candidate in enumerate(row):
            absolute_index = start_index + row_start + local_index
            with cols[local_index]:
                rank = absolute_index + 1
                title = str(candidate.get("title") or "Untitled story").strip()
                reason = str(candidate.get("discovery_reason") or "").strip()
                score = float(candidate.get("candidate_score") or 0.0)
                evidence = build_discovery_evidence(candidate)
                history_fit = float(evidence.get("channel_history") or 0.0)
                source = str(candidate.get("source_label") or "News source").strip()
                url = str(candidate.get("story_url") or "").strip()

                with st.container(border=True):
                    st.markdown(f"<div class='story-rank'>#{rank:02d}</div>", unsafe_allow_html=True)
                    st.markdown(f"<div class='story-title'>{title}</div>", unsafe_allow_html=True)
                    article_label = "article" if evidence["articles"] == 1 else "articles"
                    publisher_label = f" · {evidence['independent_publishers']} publishers" if evidence["independent_publishers"] else ""
                    fit_label = f" · Channel fit {history_fit:.1f}/10" if candidate.get("ai_recommendation") else ""
                    st.markdown(
                        f"<span class='score-chip'>Score {score:.1f}</span> "
                        f"<span class='story-meta'>{evidence['articles']} {article_label}{publisher_label}{fit_label}</span>",
                        unsafe_allow_html=True,
                    )
                    if reason:
                        st.markdown(f"<div class='story-reason'>{reason}</div>", unsafe_allow_html=True)
                    st.caption(f"Source · {source}")
                    action_cols = st.columns([1, 1])
                    with action_cols[0]:
                        if url.startswith(("http://", "https://")):
                            st.link_button("Open source", url, width="stretch")
                    with action_cols[1]:
                        if st.button("Use headline →", type="primary", width="stretch", key=f"use_candidate_{absolute_index}"):
                            st.session_state.pending_candidate = dict(candidate)
                            st.session_state.visual_search_queries = ""
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
        "Recorded factory history and optional live YouTube totals.",
    )
    try:
        stats = collect_channel_statistics(ultimate_bot.DB_PATH)
    except Exception as exc:
        st.error(f"Statistics could not be loaded: {type(exc).__name__}: {exc}")
        return

    metric_cols = st.columns(4)
    metric_cols[0].metric("Recorded runs", stats["total_runs"])
    metric_cols[1].metric("Completed runs", stats["completed_runs"])
    metric_cols[2].metric("Recorded views", f"{stats['total_views']:,}")
    metric_cols[3].metric(
        "Average view %",
        f"{stats['avg_view_percentage']:.1f}%" if stats["avg_view_percentage"] is not None else "—",
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
    else:
        st.caption("Recorded metrics are available now. Live totals are optional.")

    if st.button("Refresh live YouTube totals", width="content", key="refresh_live_channel_stats"):
        st.session_state.live_channel_stats = collect_live_channel_statistics(ultimate_bot)
        st.rerun()

    for label, table in (
        ("By format", stats["by_format"]),
        ("By language", stats["by_language"]),
        ("Recent factory history", stats["recent"]),
    ):
        with st.expander(label, expanded=(label == "Recent factory history")):
            if table:
                st.dataframe(table, width="stretch", hide_index=True)
            else:
                st.info(f"No {label.lower()} data yet.")

def render_offline_page() -> None:
    _render_section_header(
        "Engineering",
        "Offline diagnostics",
        "Safe checks for the dashboard and factory contracts. No provider/API calls are made.",
    )

    if st.button("Run offline diagnostics", type="primary", width="content"):
        with st.spinner("Running offline factory checks..."):
            st.session_state.offline_diagnostics = run_offline_diagnostics()
            st.session_state.show_offline_diagnostics = True
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
        "Factory function coverage",
        "A read-only map of the functions exposed by ultimate_bot.py.",
    )
    if report.get("complete"):
        st.success(f"All {report['total']} factory functions are accounted for.")
    else:
        st.error(
            f"Coverage is incomplete: {len(report.get('unmapped', []))} unmapped and "
            f"{len(report.get('stale_map', []))} stale entries."
        )

    buckets = report.get("by_surface") or {}
    cols = st.columns(4)
    labels = ["Live Factory", "Channel Statistics", "Demo / Diagnostics", "Internal"]
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

def render_demo_page() -> None:
    _render_section_header(
        "Engineering lab",
        "Demo Factory",
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
        ("factory_function_coverage", "Factory function coverage"),
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

    if workspace == "Live Factory":
        config = render_sidebar_controls()
        render_header("Live Factory")
        render_live_factory(config, controller)
    elif workspace == "Channel Statistics":
        render_header("Channel Statistics")
        render_channel_statistics()
    elif workspace == "Run Offline Diagnostics":
        render_header("Run Offline Diagnostics")
        render_offline_page()
    elif workspace == "Demo Factory":
        render_header("Demo Factory")
        render_demo_page()
    elif workspace == "Final Branding Preview":
        render_header("Final Branding Preview")
        render_final_branding_preview()

    st.markdown(
        "<div class='dashboard-footer'>Viral Shorts Factory · dashboard controls the human review gates; "
        "the underlying factory generation logic remains the production source of truth.</div>",
        unsafe_allow_html=True,
    )

main()
