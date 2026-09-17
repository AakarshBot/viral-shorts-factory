"""Streamlit dashboard for the Viral Shorts Factory.

This is the single supported UI entrypoint. Production logic remains in the
runtime modules; the dashboard only orchestrates configuration, discovery,
explicit story selection, production progress and final upload approval.
"""
from __future__ import annotations

import inspect
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
from visual_content_runtime import patch_content_first_visuals as patch_visual_pipeline
from visual_qa_runtime import install_visual_qa_bridge
import visual_runtime
from workflow_runtime import (
    CRICKET_CATEGORIES,
    FORMAT_OPTIONS,
    MAX_DISCOVERY_CANDIDATES,
    WorkflowController,
    discover_three_candidates,
)


st.set_page_config(page_title="Viral Shorts Factory", page_icon="🎬", layout="wide")


REQUIRED_SECRET_NAMES = (
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "GNEWS_API_KEY",
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
    print(
        "[Dashboard] Provider secrets loaded: " + ", ".join(sorted(loaded))
        if loaded
        else "[Dashboard] WARNING: No provider secrets were found.",
        flush=True,
    )
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
            problems.append(
                f"Missing channel logo: put `logo.png` or `channels4_profile.jpg` inside "
                f"`{os.path.relpath(brand_dir, base)}/`."
            )

    for font_name in ("NotoSansDevanagari-Bold.ttf", "NotoSansTelugu-Bold.ttf"):
        if not os.path.exists(os.path.join(base, font_name)):
            problems.append(f"Missing font file: `{font_name}` should be in the repository root.")

    for env_key in ("GEMINI_API_KEY", "GROQ_API_KEY", "GNEWS_API_KEY"):
        if not os.getenv(env_key):
            problems.append(f"Missing API key: `{env_key}` is not set in local env or Streamlit secrets.")
    return problems


def initialise_runtime() -> None:
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
        ultimate_bot.run_analytics_sweep = lambda _conn: print(
            "   [Learning] Automatic analytics sync disabled in newsroom workflow.", flush=True
        )
        ultimate_bot._dashboard_runtime_initialized = True
    else:
        harden_editorial_defaults(ultimate_bot)
        install_visual_qa_bridge(visual_runtime)
        patch_provider_adapters(ultimate_bot)

    bind_dashboard_patches(ultimate_bot)


def _init_state() -> None:
    defaults = {
        "workflow_controller": WorkflowController(ultimate_bot),
        "candidates": [],
        "web_config": {},
        "production_started": False,
        "final_qc": False,
        "upload_result": "",
        "candidate_page": 0,
        "show_offline_diagnostics": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_run() -> None:
    controller: WorkflowController = st.session_state.workflow_controller
    controller.reset()
    for key, value in {
        "candidates": [],
        "web_config": {},
        "production_started": False,
        "final_qc": False,
        "upload_result": "",
        "candidate_page": 0,
    }.items():
        st.session_state[key] = value


def category_options(format_mode: str) -> Dict[str, str]:
    output: Dict[str, str] = {}
    for key, cfg in ultimate_bot.CONTENT_CATEGORIES.items():
        if key == "sports_stories_of_day":
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
    format_label = st.session_state.get("format_label", "Deep Dive")

    if format_label == "Cricket":
        return {
            "format_mode": "regular",
            "display_format": "Cricket",
            "category": "sports_stories_of_day",
            "language": language_key,
            "cricket_pipeline": True,
            "cricket_category": st.session_state.get("cricket_category", "AI-assisted top story in cricket"),
            "requested_topic": str(st.session_state.get("requested_topic", "") or "").strip(),
            "language_label": language_label,
        }

    options = category_options(FORMAT_OPTIONS[format_label])
    category_key = st.session_state.get("category_key", next(iter(options.values())))
    return {
        "format_mode": FORMAT_OPTIONS[format_label],
        "display_format": format_label,
        "category": category_key,
        "language": language_key,
        "cricket_pipeline": False,
        "language_label": language_label,
    }


def render_progress(snapshot: Dict[str, Any]) -> None:
    stages = [
        ("Discovery", "discovery", 5, 14),
        ("Research", "research", 15, 23),
        ("Script", "script", 24, 40),
        ("Audio", "audio", 41, 54),
        ("Visuals", "visuals", 55, 76),
        ("Render", "render", 77, 95),
        ("Final QC", "qc", 96, 100),
    ]
    current = str(snapshot.get("stage") or "idle")
    percent = int(snapshot.get("percent", 0) or 0)
    st.markdown('<div class="panel"><div class="qc-title">Factory progress</div></div>', unsafe_allow_html=True)
    for label, key, lo, hi in stages:
        if percent >= hi:
            value, icon = 1.0, "✅"
        elif current == key:
            value, icon = max(0.02, min(1.0, (percent - lo) / max(1, hi - lo))), "⚙️"
        else:
            value, icon = 0.0, "○"
        st.markdown(f"**{icon} {label}**")
        st.progress(value)
    st.caption(str(snapshot.get("message") or ""))


def poll_production(controller: WorkflowController) -> None:
    progress_slot = st.empty()
    detail_slot = st.empty()
    while True:
        snapshot = controller.snapshot()
        with progress_slot.container():
            render_progress(snapshot)
        with detail_slot.container():
            story = snapshot.get("selected_story") or {}
            if story:
                title = str(story.get("title") or "")
                st.markdown(
                    f"<div class='panel'><div class='small-muted'>CURRENT STORY</div><b>{title}</b></div>",
                    unsafe_allow_html=True,
                )
            script = snapshot.get("script_data")
            if isinstance(script, dict):
                scenes = script.get("script", [])
                text = "\n\n".join(
                    str(scene.get("voiceover", "")).strip()
                    for scene in scenes
                    if isinstance(scene, dict)
                )
                if text:
                    st.markdown("### Script")
                    st.text_area("Generated script", text, height=260, disabled=True, key="live_script_preview")
        if not snapshot.get("thread_alive"):
            break
        time.sleep(0.7)


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### Factory controls")

        language_options = {cfg["label"]: key for key, cfg in ultimate_bot.LANGUAGES.items()}
        language_labels = list(language_options.keys())
        current_language = st.session_state.get("language_label", language_labels[0])
        st.selectbox(
            "Language",
            language_labels,
            index=language_labels.index(current_language),
            key="language_label",
        )

        format_labels = list(FORMAT_OPTIONS.keys())
        current_format = st.session_state.get("format_label", format_labels[0])
        st.selectbox(
            "Format",
            format_labels,
            index=format_labels.index(current_format),
            key="format_label",
        )

        if st.session_state.format_label == "Cricket":
            st.selectbox(
                "Cricket category",
                list(CRICKET_CATEGORIES.keys()),
                key="cricket_category",
            )
            st.text_input(
                "Specific cricket topic (optional)",
                placeholder="e.g. BCCI to suspend Impact Player rule",
                key="requested_topic",
            )
        else:
            options = category_options(FORMAT_OPTIONS[st.session_state.format_label])
            labels = list(options.keys())
            current_key = st.session_state.get("category_key", next(iter(options.values())))
            current_label = next((label for label, key in options.items() if key == current_key), labels[0])
            selected_label = st.selectbox(
                "Category",
                labels,
                index=labels.index(current_label),
                key="category_label",
            )
            st.session_state.category_key = options[selected_label]

        st.divider()
        if st.button("🧪 Offline Factory Test", use_container_width=True):
            with st.spinner("Running local checks — no API calls…"):
                report = run_offline_diagnostics()
            st.session_state.show_offline_diagnostics = True
            st.session_state.offline_diagnostics = report

        if st.session_state.get("show_offline_diagnostics"):
            report = st.session_state.get("offline_diagnostics", {})
            if report.get("all_passed"):
                st.success(f"Offline test passed: {report.get('passed', 0)}/{report.get('total', 0)}")
            else:
                st.error(f"Offline test found {report.get('failed', 0)} issue(s).")
            for item in report.get("results", []):
                icon = "✅" if item.get("status") == "PASS" else "❌"
                st.write(f"{icon} **{item.get('name', '')}** — {item.get('detail', '')}")

        if st.button("🔄 Reset current run", use_container_width=True):
            reset_run()
            st.session_state.show_offline_diagnostics = False
            st.rerun()


def render_candidate_selection(controller: WorkflowController) -> None:
    candidates = st.session_state.get("candidates", [])
    if not candidates:
        return

    total = min(len(candidates), MAX_DISCOVERY_CANDIDATES)
    page_size = 3
    page_count = max(1, (total + page_size - 1) // page_size)
    page = int(st.session_state.get("candidate_page", 0) or 0)
    page = max(0, min(page, page_count - 1))
    st.session_state.candidate_page = page

    start = page * page_size
    end = min(start + page_size, total)
    st.caption(f"Showing candidates {start + 1}–{end} of {total}")

    cols = st.columns(3, gap="medium")
    for local_index, candidate in enumerate(candidates[start:end]):
        global_index = start + local_index
        label_number = global_index + 1
        with cols[local_index]:
            title = str(candidate.get("title") or "Untitled story")
            reason = str(candidate.get("discovery_reason") or "")
            source = str(candidate.get("source_label") or "News source")
            score = candidate.get("candidate_score")
            score_line = (
                f"Opportunity signal: {float(score):.1f}"
                if score is not None
                else "Opportunity signal: live"
            )
            st.markdown(
                f"<div class='candidate'><div class='candidate-rank'>CANDIDATE {label_number}</div>"
                f"<div class='candidate-title'>{title}</div>"
                f"<div class='candidate-reason'>{reason}</div>"
                f"<div class='small-muted' style='margin-top:10px'>Source: {source}<br>{score_line}</div></div>",
                unsafe_allow_html=True,
            )
            if candidate.get("story_url"):
                st.link_button("Open source", str(candidate["story_url"]), use_container_width=True)
            if st.button(
                f"Use Candidate {label_number}",
                key=f"use_candidate_{global_index}",
                use_container_width=True,
            ):
                selected = dict(candidate)
                production_config = dict(st.session_state.get("web_config", {}))
                st.session_state.production_started = True
                st.session_state.final_qc = False
                st.session_state.upload_result = ""
                controller.start_production(production_config, selected)
                poll_production(controller)
                snapshot = controller.snapshot()
                if snapshot.get("error"):
                    st.error(snapshot["error"])
                elif snapshot.get("completed"):
                    st.session_state.final_qc = True

    nav_left, nav_right = st.columns(2)
    if page > 0:
        with nav_left:
            if st.button("← Previous 3 stories", key="candidate_previous_page", use_container_width=True):
                st.session_state.candidate_page = page - 1
                st.rerun()
    if end < total:
        remaining = total - end
        next_count = min(page_size, remaining)
        with nav_right:
            if st.button(
                f"See next {next_count} stories ({end + 1}–{min(end + next_count, total)})",
                key="candidate_next_page",
                use_container_width=True,
            ):
                st.session_state.candidate_page = page + 1
                st.rerun()


def render_final_qc(controller: WorkflowController) -> None:
    snapshot = controller.snapshot()
    if not (st.session_state.get("final_qc") or snapshot.get("stage") == "qc"):
        return

    st.markdown("---")
    st.markdown("### 3. Final QC — you control the upload")
    st.caption("Nothing is uploaded automatically. Edit the metadata below, choose visibility, then press Upload.")

    script_data = snapshot.get("script_data") or {}
    title = str(
        snapshot.get("final_metadata", {}).get("title")
        or script_data.get("title")
        or snapshot.get("selected_story", {}).get("title")
        or ""
    ).strip()
    description = str(
        snapshot.get("final_metadata", {}).get("description")
        or script_data.get("seo_description")
        or ""
    ).strip()
    comment = str(
        snapshot.get("final_metadata", {}).get("creator_comment")
        or snapshot.get("final_metadata", {}).get("pinned_comment")
        or script_data.get("creator_comment")
        or script_data.get("pinned_comment")
        or ""
    ).strip()

    title = st.text_input("Final title", value=title, max_chars=100, key="final_title")
    description = st.text_area("Final description", value=description, height=150, key="final_description")
    comment = st.text_area("Creator comment", value=comment, height=110, key="final_comment")
    visibility = st.selectbox("YouTube visibility", ["Private", "Public"], index=0, key="final_visibility")

    video_path = snapshot.get("video_path") or os.path.join(ultimate_bot.ASSETS_DIR, "final_video_output.mp4")
    if os.path.isfile(video_path):
        st.video(video_path)
    else:
        st.warning("The final video file could not be found.")

    if st.button("⬆️ Upload to YouTube", type="primary", use_container_width=True):
        try:
            category_key = st.session_state.get("web_config", {}).get("category", "national_global_affairs")
            genre_cfg = ultimate_bot.CONTENT_CATEGORIES.get(
                category_key,
                ultimate_bot.CONTENT_CATEGORIES["national_global_affairs"],
            )
            result = controller.upload_manual(
                video_path,
                script_data,
                title,
                description,
                comment,
                "public" if visibility == "Public" else "private",
                genre_cfg,
                st.session_state.get("web_config", {}).get("trend_keyword", ""),
            )
            st.session_state.upload_result = result
            st.success(f"Uploaded successfully. Video ID: {result}")
        except Exception as exc:
            st.error(f"Upload failed: {type(exc).__name__}: {exc}")

    if st.session_state.upload_result:
        st.success(f"Last upload: {st.session_state.upload_result}")


# ---------------------------------------------------------------------------
# Dashboard startup
# ---------------------------------------------------------------------------

load_streamlit_secrets_into_runtime()
_asset_problems = check_required_local_assets()
if _asset_problems:
    st.error(
        "⚠️ The factory cannot start until these local files/keys are in place:\n\n"
        + "\n".join(f"- {problem}" for problem in _asset_problems)
    )
    st.stop()

initialise_runtime()

try:
    db = sqlite3.connect(ultimate_bot.DB_PATH)
    migrate_vault(db)
    db.close()
except Exception as exc:
    st.warning(f"Database migration check failed: {exc}")

_init_state()
controller: WorkflowController = st.session_state.workflow_controller

st.markdown(
    """
<style>
:root { --ink:#19212b; --muted:#6c7480; --line:rgba(25,33,43,.10); --panel:rgba(255,255,255,.88); --accent:#1287d7; }
.stApp {
  background: radial-gradient(circle at 8% 0%, rgba(83,184,255,.13), transparent 30%),
              radial-gradient(circle at 92% 8%, rgba(255,183,77,.12), transparent 26%),
              linear-gradient(180deg, #f7fafc 0%, #eef3f7 100%);
  color:var(--ink);
}
.block-container { max-width:1500px; padding-top:2rem; }
.brand-card { border:1px solid var(--line); background:linear-gradient(135deg,rgba(255,255,255,.96),rgba(245,249,252,.84)); box-shadow:0 16px 45px rgba(32,48,64,.08); border-radius:24px; padding:22px 26px; margin-bottom:18px; }
.brand-title { font-size:2rem; font-weight:800; letter-spacing:-.03em; }
.brand-sub { color:var(--muted); margin-top:4px; }
.panel { border:1px solid var(--line); background:var(--panel); border-radius:20px; padding:18px; box-shadow:0 12px 34px rgba(32,48,64,.06); margin-bottom:16px; }
.candidate { border:1px solid var(--line); background:#fff; border-radius:18px; padding:18px; min-height:210px; box-shadow:0 8px 24px rgba(32,48,64,.05); }
.candidate-rank { color:var(--accent); font-weight:800; font-size:.82rem; letter-spacing:.08em; }
.candidate-title { font-size:1.12rem; line-height:1.35; font-weight:750; margin:8px 0 10px; }
.candidate-reason { color:var(--muted); font-size:.92rem; line-height:1.45; }
.small-muted { color:var(--muted); font-size:.86rem; }
.qc-title { font-size:1.4rem; font-weight:800; }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="brand-card">
  <div class="brand-title">🎬 Viral Shorts Factory</div>
  <div class="brand-sub">A newsroom-style Shorts production system. The factory finds the opportunity; you remain the final editor.</div>
</div>
""",
    unsafe_allow_html=True,
)

render_sidebar()

with st.container():
    config = build_config()
    st.markdown("### 1. Find today's best stories")
    st.caption(
        "Discovery only. No script, TTS, visual QA or upload stage starts until you choose one story."
    )

    if st.button("🚀 Start Factory — Find 3 Stories", type="primary", use_container_width=True):
        try:
            controller.reset()
            controller.update(
                "discovery",
                10,
                "Searching today's stories and filtering duplicates/safety issues…",
            )
            conn = sqlite3.connect(ultimate_bot.DB_PATH)
            try:
                migrate_vault(conn)
                candidates = discover_three_candidates(ultimate_bot, config, conn)
            finally:
                conn.close()
            st.session_state.candidates = candidates
            st.session_state.web_config = config
            st.session_state.final_qc = False
            st.session_state.upload_result = ""
            st.session_state.production_started = False
            st.session_state.candidate_page = 0
            if candidates:
                total = min(len(candidates), MAX_DISCOVERY_CANDIDATES)
                st.success(
                    f"Found {total} candidate stories. Showing 1–{min(3, total)} initially."
                )
            else:
                st.warning(
                    "No suitable stories survived the discovery filters. Try another category or run again later."
                )
        except Exception as exc:
            st.error(f"Discovery failed: {type(exc).__name__}: {exc}")

    render_candidate_selection(controller)

    if st.session_state.production_started and controller.snapshot().get("thread_alive"):
        poll_production(controller)

    snapshot = controller.snapshot()
    if snapshot.get("error") and not snapshot.get("thread_alive"):
        st.error(snapshot["error"])

    render_final_qc(controller)

st.divider()
st.caption(
    "Viral Shorts Factory · single supported newsroom dashboard · production never publishes without explicit final QC upload action"
)
