"""Single supported Streamlit dashboard for the Viral Shorts Factory.

Dashboard-only orchestration lives here. Factory generation modules remain
untouched: this file configures them, presents their progress and gates only
the user-facing visual approval/upload decisions.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict

import streamlit as st

import ultimate_bot
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
)


MAX_DASHBOARD_DISCOVERY_HEADLINES = 20

st.set_page_config(page_title="Viral Shorts Factory", page_icon="🎬", layout="wide")

st.markdown("<style>\n.block-container{padding-top:1.5rem;padding-bottom:3rem;max-width:1500px}\nsection[data-testid=\"stSidebar\"]{border-right:1px solid rgba(255,255,255,.08)}\n.brand-card{background:linear-gradient(135deg,#161e35,#10172a 60%,#1a1434);border:1px solid rgba(255,255,255,.09);border-radius:22px;padding:26px 30px;margin-bottom:22px;box-shadow:0 16px 50px rgba(0,0,0,.18)}\n.brand-title{font-size:2rem;font-weight:800;letter-spacing:-.04em}.brand-sub{color:#91a0bb;margin-top:6px}\n.section-kicker{color:#9eacc4;text-transform:uppercase;letter-spacing:.12em;font-size:.72rem;font-weight:700;margin-bottom:4px}\n.panel,.candidate{background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,.018));border:1px solid rgba(255,255,255,.09);border-radius:16px;padding:16px 18px;box-shadow:0 8px 30px rgba(0,0,0,.1)}\n.candidate{min-height:245px}.candidate-rank{color:#9aa9c4;font-size:.72rem;font-weight:800;letter-spacing:.1em}.candidate-title{font-size:1.06rem;font-weight:750;line-height:1.35;margin:9px 0 10px}.candidate-reason{color:#aebbd0;font-size:.88rem;line-height:1.45;min-height:74px}.small-muted{color:#91a0bb;font-size:.82rem}\n.stage-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:12px 0 18px}.stage-card{border:1px solid rgba(255,255,255,.09);border-radius:12px;padding:11px 12px;background:rgba(255,255,255,.02)}.stage-card.active{border-color:rgba(124,92,255,.55);background:rgba(124,92,255,.08)}.stage-card.done{border-color:rgba(45,212,191,.3)}.stage-name{font-size:.8rem;font-weight:700}.stage-state{color:#91a0bb;font-size:.72rem;margin-top:3px}\ndiv[data-testid=\"stMetric\"]{background:rgba(255,255,255,.025);border:1px solid rgba(255,255,255,.09);border-radius:14px;padding:12px 14px}.stButton>button,.stLinkButton>a{border-radius:10px;font-weight:650;min-height:42px}.dashboard-footer{text-align:center;color:#91a0bb;font-size:.78rem;padding:8px 0}\n@media(max-width:900px){.stage-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.brand-title{font-size:1.55rem}}\n</style>", unsafe_allow_html=True)


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
    for env_key in ("GEMINI_API_KEY", "GROQ_API_KEY", "GNEWS_API_KEY"):
        if not os.getenv(env_key):
            problems.append(f"Live provider key is not configured: {env_key}.")
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
        from audio_runtime import patch_audio_pipeline
        patch_audio_pipeline(ultimate_bot)
        patch_provider_adapters(ultimate_bot)
        ultimate_bot.token_overlap_ratio = lambda _a, _b: 0.0
        ultimate_bot.run_analytics_sweep = lambda _conn: print(
            "[Learning] Automatic analytics sync disabled in newsroom workflow.", flush=True
        )
        ultimate_bot._dashboard_runtime_initialized = True
    else:
        harden_editorial_defaults(ultimate_bot)
        install_visual_qa_bridge(visual_runtime)
        patch_provider_adapters(ultimate_bot)

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


def render_header(action_mode: str) -> None:
    titles = {
        "Live Factory": ("Live Factory", "Run a complete Short from topic discovery through final upload review."),
        "Channel Statistics": ("Channel Statistics", "See the performance history currently recorded by the factory."),
        "Run Offline Diagnostics": ("Offline Diagnostics", "Run code and runtime checks without using production provider calls."),
        "Demo Factory": ("Demo Factory", "Exercise individual factory sections with safe, controlled test inputs."),
    }
    title, subtitle = titles[action_mode]
    logo_path = ""
    brand_dir = getattr(ultimate_bot, "BRAND_ASSETS_DIR", None)
    if brand_dir:
        for candidate in ("logo.png", "channels4_profile.jpg"):
            candidate_path = os.path.join(brand_dir, candidate)
            if os.path.isfile(candidate_path):
                logo_path = candidate_path
                break

    if logo_path:
        left, right = st.columns([1, 7])
        with left:
            st.image(logo_path, width=86)
        with right:
            st.markdown(
                f"""
<div class="brand-card">
  <div class="brand-title">Viral Shorts Factory</div>
  <div class="brand-sub">{title} · {subtitle}</div>
</div>
""",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            f"""
<div class="brand-card">
  <div class="brand-title">🎬 Viral Shorts Factory</div>
  <div class="brand-sub">{title} · {subtitle}</div>
</div>
""",
            unsafe_allow_html=True,
        )


def render_sidebar_controls() -> Dict[str, Any]:
    st.sidebar.markdown("## Factory setup")
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

    mode_labels = ["Deep Dive", "Top Five", "Cricket", "AI"]
    current_mode = st.session_state.get("editorial_mode", mode_labels[0])
    st.sidebar.selectbox(
        "Editorial mode",
        mode_labels,
        index=mode_labels.index(current_mode),
        key="editorial_mode",
        help="Deep Dive and Top Five use curated topic menus. Cricket keeps its dedicated cricket intake. AI ranks current stories against channel history.",
    )

    if st.session_state.editorial_mode == "Cricket":
        st.sidebar.selectbox("Cricket category", list(CRICKET_CATEGORIES.keys()), key="cricket_category")
        st.sidebar.text_input(
            "Specific topic (optional)",
            placeholder="e.g. BCCI to suspend Impact Player rule",
            key="requested_topic",
        )
    elif st.session_state.editorial_mode == "AI":
        st.sidebar.info("AI mode uses current news, channel history, genre fit, vault topics and trend signals to produce a Top 10.")
    else:
        options = category_options("top5" if st.session_state.editorial_mode == "Top Five" else "regular", st.session_state.editorial_mode)
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
    if sidebar_snapshot.get("thread_alive"):
        st.sidebar.success("Factory run active", icon="⚙️")
    elif sidebar_snapshot.get("completed"):
        st.sidebar.success("Latest run complete", icon="✅")
    elif sidebar_snapshot.get("stage") == "error":
        st.sidebar.error("Latest run stopped", icon="⚠️")
    else:
        st.sidebar.info("Ready for a new run")

    if st.sidebar.button(
        "Reset current run",
        use_container_width=True,
        disabled=bool(sidebar_snapshot.get("thread_alive")),
    ):
        reset_run()
        st.rerun()
    if st.session_state.workflow_controller.snapshot().get("thread_alive"):
        st.sidebar.caption("A live run is active. Use the visual review controls to stop or continue it.")

    return build_config()


def render_stage_progress(snapshot: Dict[str, Any]) -> None:
    stages = [
        ("Discovery", "discovery", 10, 14),
        ("Research", "research", 15, 23),
        ("Script", "script", 24, 40),
        ("Voiceover", "audio", 41, 54),
        ("Visuals", "visuals", 55, 75),
        ("Visual Review", "visual_approval", 76, 76),
        ("Final Render", "render", 77, 95),
        ("Final QC", "qc", 96, 100),
    ]
    current = str(snapshot.get("stage") or "idle")
    percent = int(snapshot.get("percent", 0) or 0)

    st.markdown("<div class='section-kicker'>Production pipeline</div><h3 style='margin-top:0'>Factory progress</h3>", unsafe_allow_html=True)
    for label, key, _lo, hi in stages:
        if current == "error":
            value = 0.0
            icon = "⚠️"
        elif percent >= hi:
            value = 1.0
            icon = "✅"
        elif current == key:
            value = 0.04 if hi <= _lo else max(0.02, min(1.0, (percent - _lo) / max(1, hi - _lo)))
            icon = "⚙️"
        else:
            value = 0.0
            icon = "○"
        st.markdown(f"**{icon} {label}** · {value * 100:.0f}%")
        st.progress(value)

    st.progress(max(0.0, min(1.0, percent / 100)), text=f"{percent}% complete")
    message = str(snapshot.get("message") or "").strip()
    if message:
        st.info(message, icon="ℹ️")


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
    st.markdown("### Script")
    st.caption("Written automatically from the selected story. No script approval step is required.")
    st.text_area("Generated narration", value=text, height=320, disabled=True, key="dashboard_script_preview")


def _visual_items(snapshot: Dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for index, package in enumerate(snapshot.get("visual_packages") or [], 1):
        if not package:
            continue
        layer = package[0] if isinstance(package, list) else package
        if not isinstance(layer, dict):
            continue
        path = str(layer.get("image") or "").strip()
        if path and os.path.isfile(path):
            items.append(
                {
                    "index": index,
                    "path": path,
                    "source": str(layer.get("source_type") or "visual"),
                    "visual_type": str(layer.get("visual_type") or "visual"),
                    "verified": bool(layer.get("visual_verified", False)),
                    "manual_query": str(layer.get("manual_visual_query") or "").strip(),
                    "rescue_reason": str(layer.get("visual_rescue_reason") or "").strip(),
                }
            )
    return items


def render_visual_review(controller: DashboardWorkflowController, snapshot: Dict[str, Any]) -> None:
    items = _visual_items(snapshot)
    if not items:
        return

    st.markdown("<div class='section-kicker'>Approval gate</div><h2 style='margin-top:0'>Visual review</h2>", unsafe_allow_html=True)
    st.caption(
        f"{len(items)} visuals are ready. Review every image below. Rendering will not continue until you approve them."
    )

    columns = st.columns(3, gap="medium")
    for offset, item in enumerate(items):
        with columns[offset % 3]:
            st.image(item["path"], use_container_width=True)
            status = "Verified" if item["verified"] else "Needs attention"
            st.markdown(
                f"**Visual {item['index']}** · {item['visual_type']}  \\n"
                f"<span class='small-muted'>{item['source']} · {status}</span>",
                unsafe_allow_html=True,
            )

    approve_col, reject_col = st.columns(2)
    with approve_col:
        if st.button(
            "✅ Approve visuals & continue",
            type="primary",
            use_container_width=True,
            key="approve_visuals",
        ):
            controller.approve_visuals()
            st.rerun()
    with reject_col:
        if st.button(
            "⛔ Reject visuals & stop",
            use_container_width=True,
            key="reject_visuals",
        ):
            controller.reject_visuals()
            st.rerun()


def render_activity_timeline(snapshot: Dict[str, Any]) -> None:
    events = snapshot.get("activity_events") or []
    if not events:
        return
    st.markdown("### Live activity")
    st.caption("Plain-language progress from the actual factory stages.")
    for index, event in enumerate(events):
        icon = "⚙️" if index == len(events) - 1 and snapshot.get("thread_alive") else "✅"
        st.markdown(
            f"<div class='panel' style='padding:12px 16px;margin-bottom:8px'>"
            f"<b>{icon} {event.get('stage', 'Factory')}</b> "
            f"<span class='small-muted'>{event.get('time', '')}</span><br>"
            f"<span>{event.get('message', '')}</span></div>",
            unsafe_allow_html=True,
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
    st.markdown("### Story & research")
    if headline:
        st.markdown(f"**Headline:** {headline}")
    if source:
        st.markdown(f"**Source:** {source}")
    if url.startswith(("http://", "https://")):
        st.link_button("Open source article", url, use_container_width=True)


def render_audio_preview(snapshot: Dict[str, Any]) -> None:
    paths = [str(path).strip() for path in (snapshot.get("audio_paths") or []) if str(path or "").strip()]
    existing = [path for path in paths if os.path.isfile(path)]
    if not existing:
        return
    st.markdown("### Voiceover")
    st.caption(f"{len(existing)} narration track(s) generated with word-level timing.")
    for index, path in enumerate(existing, 1):
        st.audio(path, format="audio/mpeg")
        st.caption(f"Scene {index}")


def render_visual_details(snapshot: Dict[str, Any]) -> None:
    items = _visual_items(snapshot)
    if not items:
        return
    with st.expander("Visual sourcing details", expanded=False):
        for item in items:
            details = [f"Visual {item['index']}: {item['visual_type']} · {item['source']}"]
            if item.get("manual_query"):
                details.append(f"Manual query: {item['manual_query']}")
            if item.get("rescue_reason"):
                details.append(f"Rescue: {item['rescue_reason']}")
            st.markdown(" — ".join(details))


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

    st.markdown("### Live factory console")
    st.caption("Live output from the factory worker, presented here without replacing the underlying PowerShell console.")
    if operation_percent is not None:
        st.markdown(f"**{operation_label}** · {operation_percent}%")
        st.progress(max(0.0, min(1.0, operation_percent / 100)))
    with st.container(border=True):
        st.code("\n".join(lines[-80:]), language="text")


def render_logs(snapshot: Dict[str, Any]) -> None:
    logs = snapshot.get("dashboard_logs") or []
    if not logs:
        return
    with st.expander("Technical activity summary", expanded=False):
        for index, message in enumerate(logs):
            prefix = "Latest" if index == len(logs) - 1 else "Done"
            st.markdown(f"**{prefix}:** {message}")


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

    st.markdown("---")
    st.markdown("<div class='section-kicker'>Release gate</div><h2 style='margin-top:0'>Final QC & upload</h2>", unsafe_allow_html=True)

    qc_checks = [
        ("Rendered video", bool(video_path)),
        ("Script generated", bool(snapshot.get("script_data"))),
        ("Visual package", bool(snapshot.get("visual_packages"))),
        ("Visual approval", bool(snapshot.get("visual_review_approved"))),
    ]
    qc_cols = st.columns(len(qc_checks))
    for col, (label, ok) in zip(qc_cols, qc_checks):
        col.metric(label, "PASS" if ok else "CHECK")

    st.markdown("### Final video")
    if video_path and os.path.isfile(video_path):
        st.success("The Short is rendered, branded and ready for your review.", icon="✅")
        st.video(video_path)
    else:
        st.error("The dashboard has a final video path, but the file is not accessible from this dashboard process.")
        st.code(video_path or "No final video path recorded.", language="text")
        return

    st.markdown("### 1 · Review and approve metadata")
    st.caption("Edit the title, description and creator comment. Upload controls stay locked until you explicitly approve these fields.")

    editing = not bool(st.session_state.get("metadata_approved"))
    title = st.text_input(
        "YouTube title",
        max_chars=100,
        key="final_title",
        disabled=not editing,
    )
    description = st.text_area(
        "YouTube description",
        height=150,
        key="final_description",
        disabled=not editing,
    )
    comment = st.text_area(
        "Creator comment",
        height=110,
        key="final_comment",
        disabled=not editing,
    )

    if editing:
        approve_col, info_col = st.columns([1, 2])
        with approve_col:
            if st.button(
                "✅ Approve title, description & comment",
                type="primary",
                use_container_width=True,
                key="approve_metadata",
            ):
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
        with info_col:
            st.info("Nothing will be uploaded until the metadata approval above succeeds.")
        return

    st.success("Metadata approved. You can now choose how the Short is published.", icon="✅")
    if st.button("✏️ Edit metadata", use_container_width=True, key="edit_metadata"):
        st.session_state["metadata_approved"] = False
        st.rerun()

    st.markdown("### 2 · Choose upload visibility")
    st.info("Private keeps the Short hidden on YouTube. Public publishes it immediately after the final confirmation.")

    public_col, private_col = st.columns(2)
    with public_col:
        if st.button("🌐 Upload Publicly", type="primary", use_container_width=True, key="upload_public"):
            st.session_state["confirm_public_upload"] = True
            st.rerun()
    with private_col:
        if st.button("🔒 Upload Privately", use_container_width=True, key="upload_private"):
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
        st.warning("You are about to publish this video publicly. It will become visible on YouTube immediately. Continue?")
        confirm_col, cancel_col = st.columns(2)
        with confirm_col:
            if st.button("✅ Yes, upload publicly", type="primary", use_container_width=True, key="confirm_upload_public"):
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
            if st.button("← Cancel", use_container_width=True, key="cancel_upload_public"):
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
        render_script(snapshot)
        render_audio_preview(snapshot)

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
            "Some live provider keys are not configured. Discovery/production may stop when that provider is required."
        )

    st.markdown("<div class='section-kicker'>Step 01 · Discovery</div><h2 style='margin-top:0'>Choose a story</h2>", unsafe_allow_html=True)
    st.caption(
        "The factory ranks up to 20 fresh stories for this section. Repeats from the previous 48 hours are removed before ranking."
    )

    if not st.session_state.candidates:
        if st.button("🚀 Find today's ranked topics", type="primary", use_container_width=True):
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
                            max_candidates=10,
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
                st.session_state.candidates = candidates
                st.session_state.web_config = config
                st.session_state.production_started = False
                st.session_state.final_qc = False
                st.session_state.upload_result = ""
                st.session_state.candidate_page = 0
                st.session_state.discovery_headline_selection = None
                st.success(
                    f"Found {len(candidates)} ranked headlines. Choose one below."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Topic discovery failed: {type(exc).__name__}: {exc}")
        return

    if st.session_state.production_started:
        render_live_monitor(controller)
        return

    pending_candidate = st.session_state.get("pending_candidate")
    if pending_candidate:
        st.markdown("### Visual search queries (optional)")
        st.caption(
            "Leave this blank to use the current Full AI visual flow. "
            "If you enter queries, separate them with semicolons (;). "
            "The factory will intelligently assign them to the most relevant slides."
        )
        st.text_input(
            "Search queries",
            placeholder="e.g. India Afghanistan cricket match; Shubman Gill batting; New Delhi cricket stadium",
            key="visual_search_queries",
            label_visibility="collapsed",
        )
        st.markdown(
            f"<div class='panel'><div class='small-muted'>SELECTED HEADLINE</div>"
            f"<b>{pending_candidate.get('title', '')}</b></div>",
            unsafe_allow_html=True,
        )
        start_col, cancel_col = st.columns(2)
        with start_col:
            if st.button(
                "🚀 Start production",
                type="primary",
                use_container_width=True,
                key="start_selected_topic",
            ):
                config = dict(st.session_state.web_config)
                config["visual_search_queries"] = str(
                    st.session_state.get("visual_search_queries", "") or ""
                ).strip()
                if config.get("editorial_mode") == "AI":
                    config["category"] = str(pending_candidate.get("recommended_category") or "national_global_affairs")
                    config["format_mode"] = str(pending_candidate.get("recommended_format") or "regular")
                st.session_state.production_started = True
                st.session_state.final_qc = False
                st.session_state.upload_result = ""
                controller.start_production(config, dict(pending_candidate))
                st.rerun()
        with cancel_col:
            if st.button(
                "← Choose another headline",
                use_container_width=True,
                key="cancel_selected_topic",
            ):
                st.session_state.pending_candidate = None
                st.session_state.visual_search_queries = ""
                st.rerun()
        return

    candidates = st.session_state.candidates
    total = min(len(candidates), MAX_DASHBOARD_DISCOVERY_HEADLINES)

    st.markdown(
        f"<div class='panel'><b>Ranked headlines · {total} available</b>"
        f"<span class='small-muted' style='float:right'>Select one headline to continue</span></div>",
        unsafe_allow_html=True,
    )

    labels = []
    candidate_by_label: dict[str, dict[str, Any]] = {}
    for index, candidate in enumerate(candidates[:total], 1):
        title = str(candidate.get("title") or "Untitled story").strip()
        label = f"{index:02d}. {title}"
        labels.append(label)
        candidate_by_label[label] = candidate

    selected_label = st.radio(
        "Ranked headlines",
        labels,
        key="discovery_headline_selection",
        label_visibility="collapsed",
    )
    selected_candidate = candidate_by_label.get(selected_label) if selected_label else None

    if selected_candidate:
        source = str(selected_candidate.get("source_label") or "News source").strip()
        article_count = int(selected_candidate.get("event_article_count") or 1)
        source_count = int(selected_candidate.get("event_source_count") or 0)
        support = (
            f"{article_count} article{'s' if article_count != 1 else ''}"
            + (f" · {source_count} publisher{'s' if source_count != 1 else ''}" if source_count else "")
        )
        st.caption(f"Source: {source} · {support}")
        if selected_candidate.get("story_url"):
            st.link_button("Open source article", str(selected_candidate["story_url"]))

        if st.button(
            "Use selected headline →",
            type="primary",
            use_container_width=True,
            key="use_selected_headline",
        ):
            st.session_state.pending_candidate = dict(selected_candidate)
            st.session_state.visual_search_queries = ""
            st.rerun()

    if not controller.snapshot().get("thread_alive"):
        st.info("Select a headline above, then continue to the optional image-search query step.")


def render_channel_statistics() -> None:
    st.markdown("<div class='section-kicker'>Analytics</div><h2 style='margin-top:0'>Channel performance</h2>", unsafe_allow_html=True)
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

    ctr_col, live_col = st.columns(2)
    ctr_col.metric("Average title CTR", f"{stats['avg_ctr']:.2f}%" if stats["avg_ctr"] is not None else "—")
    with live_col:
        if st.button("↻ Refresh live YouTube totals", use_container_width=True, key="refresh_live_channel_stats"):
            st.session_state.live_channel_stats = collect_live_channel_statistics(ultimate_bot)

    live = st.session_state.get("live_channel_stats") or {}
    if live.get("error"):
        st.warning(f"Live YouTube totals could not be loaded: {live['error']}")
    elif live:
        st.markdown("### Live YouTube channel totals")
        live_cols = st.columns(4)
        live_cols[0].metric("Channel", live.get("channel_title", "Connected channel"))
        live_cols[1].metric("Subscribers", "Hidden" if live.get("hidden_subscriber_count") else f"{live.get('subscriber_count', 0):,}")
        live_cols[2].metric("Videos", f"{live.get('video_count', 0):,}")
        live_cols[3].metric("All-time views", f"{live.get('view_count', 0):,}")
    else:
        st.caption(
            "Recorded factory metrics are shown above. Use “Refresh live YouTube totals” "
            "to query the connected channel account."
        )

    st.markdown("### By format")
    if stats["by_format"]:
        st.dataframe(stats["by_format"], use_container_width=True, hide_index=True)

    st.markdown("### By language")
    if stats["by_language"]:
        st.dataframe(stats["by_language"], use_container_width=True, hide_index=True)

    st.markdown("### Recent factory history")
    if stats["recent"]:
        st.dataframe(stats["recent"], use_container_width=True, hide_index=True)
    else:
        st.info("No recorded factory runs yet.")


def render_offline_page() -> None:
    st.markdown("<div class='section-kicker'>Engineering</div><h2 style='margin-top:0'>Offline diagnostics</h2>", unsafe_allow_html=True)
    st.caption("These checks are safe to run while coding. They make zero provider/API calls.")

    if st.button("🧪 Run offline diagnostics", type="primary", use_container_width=True):
        with st.spinner("Running offline factory checks..."):
            st.session_state.offline_diagnostics = run_offline_diagnostics()
            st.session_state.show_offline_diagnostics = True

    report = st.session_state.get("offline_diagnostics") or {}
    if not report:
        return

    if report.get("all_passed"):
        st.success(f"All checks passed: {report.get('passed', 0)}/{report.get('total', 0)}")
    else:
        st.error(
            f"Diagnostics found {report.get('failed', 0)} issue(s) out of {report.get('total', 0)}."
        )

    for item in report.get("results", []):
        icon = "✅" if item.get("status") == "PASS" else "❌"
        st.markdown(
            f"<div class='panel'><b>{icon} {item.get('name', '')}</b><br>"
            f"<span class='small-muted'>{item.get('detail', '')}</span></div>",
            unsafe_allow_html=True,
        )



def render_factory_function_coverage() -> None:
    """Show a complete, read-only map of ultimate_bot callables."""
    report = factory_function_coverage()
    st.markdown("### Factory function coverage")
    st.caption(
        "Every top-level function in ultimate_bot.py is explicitly classified so we can "
        "distinguish dashboard features from deliberate internal helpers."
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
        with st.expander(f"{label} ({len(names)})", expanded=(label != "Internal")):
            st.code("\\n".join(names), language="text") if names else st.caption("None")

def render_demo_page() -> None:
    st.markdown("<div class='section-kicker'>Engineering lab</div><h2 style='margin-top:0'>Demo Factory</h2><h4>Component-by-component factory tests</h4>", unsafe_allow_html=True)
    st.caption(
        "Demo mode never performs a production upload and does not need provider calls. "
        "It exercises existing factory contracts with controlled test inputs."
    )

    sections = [
        ("imports", "Imports"),
        ("environment", "Environment"),
        ("database", "Database"),
        ("visual_strategy", "Visual strategy & identity"),
        ("scene_branding", "Scene overlay"),
        ("script_audio", "Script cleaning & audio timing"),
        ("runtime_bindings", "Runtime bindings"),
        ("provider_boundary", "Raw provider boundary"),
        ("premium_renderers", "Subtitles, Top-5 card & glass logo"),
        ("manual_visual_queries", "Manual visual query routing"),
        ("dashboard_architecture", "Dashboard architecture"),
        ("factory_function_coverage", "Factory function coverage"),
    ]

    if st.button("▶ Run all demo checks", type="primary", use_container_width=True):
        results = {}
        with st.spinner("Running all demo sections..."):
            for key, _label in sections:
                results[key] = run_demo_section(key)
        st.session_state.last_demo_results = results

    columns = st.columns(2, gap="medium")
    for index, (key, label) in enumerate(sections):
        with columns[index % 2]:
            st.markdown(f"<div class='panel'><div class='qc-title'>{label}</div></div>", unsafe_allow_html=True)
            if st.button(f"Test {label}", key=f"demo_{key}", use_container_width=True):
                result = run_demo_section(key)
                st.session_state.last_demo_results[key] = result

            result = (st.session_state.get("last_demo_results") or {}).get(key)
            if result:
                if result.get("status") == "PASS":
                    st.success(result.get("detail", "Passed"))
                else:
                    st.error(result.get("detail", "Failed"))
                artifacts = result.get("artifacts") or {}
                for artifact_name, artifact_path in artifacts.items():
                    if artifact_path and os.path.isfile(artifact_path):
                        st.caption(artifact_name.replace("_", " ").title())
                        st.image(artifact_path, use_container_width=True)

    st.markdown("---")
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

    action_mode = st.radio(
        "Action plan",
        ["Live Factory", "Channel Statistics", "Run Offline Diagnostics", "Demo Factory"],
        horizontal=True,
        key="action_mode",
    )
    render_header(action_mode)

    if action_mode == "Live Factory":
        config = render_sidebar_controls()
        render_live_factory(config, controller)
    elif action_mode == "Channel Statistics":
        st.sidebar.caption("Channel configuration is not needed for statistics.")
        render_channel_statistics()
    elif action_mode == "Run Offline Diagnostics":
        st.sidebar.caption("No API calls are made from this screen.")
        render_offline_page()
    else:
        st.sidebar.caption("Demo runs are local and do not publish videos.")
        render_demo_page()

    st.divider()
    st.caption(
        "Viral Shorts Factory · dashboard controls production, visual approval and upload visibility; "
        "the underlying factory generation logic remains the production source of truth."
    )


main()
