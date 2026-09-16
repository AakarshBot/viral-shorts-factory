from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

import ultimate_bot
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
from youtube_comment_runtime import _build_clean_metadata, build_pinned_comment


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
        patch_provider_adapters(ultimate_bot)
        ultimate_bot._dashboard_runtime_initialized = True
    else:
        harden_editorial_defaults(ultimate_bot)
        install_visual_qa_bridge(visual_runtime)
        patch_provider_adapters(ultimate_bot)
    bind_dashboard_patches(ultimate_bot)


_init_runtime()


if "qc_mode" not in st.session_state:
    st.session_state.qc_mode = "Manual Run"
if "qc_stage" not in st.session_state:
    st.session_state.qc_stage = "setup"
if "qc_config" not in st.session_state:
    st.session_state.qc_config = {}
if "qc_candidates" not in st.session_state:
    st.session_state.qc_candidates = []
if "qc_selected_story" not in st.session_state:
    st.session_state.qc_selected_story = None
if "qc_script" not in st.session_state:
    st.session_state.qc_script = None
if "qc_visuals" not in st.session_state:
    st.session_state.qc_visuals = []
if "qc_metadata_options" not in st.session_state:
    st.session_state.qc_metadata_options = {}
if "qc_approvals" not in st.session_state:
    st.session_state.qc_approvals = {}
if "qc_ai_started" not in st.session_state:
    st.session_state.qc_ai_started = False
if "qc_ai_done" not in st.session_state:
    st.session_state.qc_ai_done = False
if "qc_ai_snapshot" not in st.session_state:
    st.session_state.qc_ai_snapshot = {}


st.markdown("# 🛠️ Factory QC Control Room")
st.caption("Choose AI Run or Manual Run. Manual Run exposes each important editorial decision before the pipeline can continue.")


with st.sidebar:
    st.markdown("### Run control")
    st.session_state.qc_mode = st.radio(
        "Run mode",
        ["AI Run", "Manual Run"],
        index=0 if st.session_state.qc_mode == "AI Run" else 1,
        help="AI Run follows the existing self-critique/QC pipeline. Manual Run stops at each editorial approval point.",
    )
    st.markdown("### Code & pipeline")
    code_view = st.selectbox(
        "Inspect",
        ["None", "Pipeline overview", "app.py", "workflow_runtime.py", "ultimate_bot.py", "subtitle_runtime.py"],
    )


st.markdown("### Workflow checkpoints")
stages = [
    ("setup", "Setup"),
    ("discovery", "Discovery"),
    ("story", "Story approval"),
    ("script", "Script"),
    ("visuals", "Visual approval"),
    ("metadata", "Title / description / comment"),
    ("audio", "Audio"),
    ("render", "Render"),
]
current_index = next((i for i, (key, _) in enumerate(stages) if key == st.session_state.qc_stage), 0)
cols = st.columns(len(stages))
for i, (_key, label) in enumerate(stages):
    state = "✅" if i < current_index else "⚙️" if i == current_index else "○"
    cols[i].markdown(f"**{state}**  
{label}")


def _category_options(format_mode: str) -> Dict[str, str]:
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


def _build_config() -> Dict[str, Any]:
    language_options = {cfg["label"]: key for key, cfg in ultimate_bot.LANGUAGES.items()}
    language_label = st.session_state.get("qc_language", "English")
    format_label = st.session_state.get("qc_format", "Deep Dive")
    language_key = language_options.get(language_label, "english")
    if format_label == "Cricket":
        return {
            "format_mode": "regular",
            "display_format": "Cricket",
            "category": "sports_stories_of_day",
            "language": language_key,
            "cricket_pipeline": True,
            "cricket_category": st.session_state.get("qc_cricket_category", "AI-assisted top story in cricket"),
            "requested_topic": str(st.session_state.get("qc_requested_topic", "") or "").strip(),
            "language_label": language_label,
        }
    options = _category_options(FORMAT_OPTIONS[format_label])
    category_key = options.get(st.session_state.get("qc_category_label"), next(iter(options.values())))
    return {
        "format_mode": FORMAT_OPTIONS[format_label],
        "display_format": format_label,
        "category": category_key,
        "language": language_key,
        "cricket_pipeline": False,
        "language_label": language_label,
    }


with st.container(border=True):
    st.markdown("### 1. Choose what the factory should work on")
    c1, c2 = st.columns(2)
    with c1:
        st.selectbox("Format", list(FORMAT_OPTIONS.keys()), key="qc_format")
        st.selectbox("Language", [cfg["label"] for cfg in ultimate_bot.LANGUAGES.values()], key="qc_language")
    with c2:
        format_mode = FORMAT_OPTIONS[st.session_state.qc_format]
        if st.session_state.qc_format == "Cricket":
            st.selectbox("Cricket scope", list(CRICKET_CATEGORIES.keys()), key="qc_cricket_category")
            st.text_input("Specific topic (optional)", key="qc_requested_topic", placeholder="e.g. BCCI to suspend Impact Player rule")
        else:
            options = _category_options(format_mode)
            labels = list(options.keys())
            if st.session_state.get("qc_category_label") not in labels:
                st.session_state.qc_category_label = labels[0]
            st.selectbox("Topic category", labels, key="qc_category_label")

    st.session_state.qc_config = _build_config()
    if st.button("🔎 Discover candidates", type="primary", use_container_width=True):
        try:
            conn = ultimate_bot.sqlite3.connect(ultimate_bot.DB_PATH)
            try:
                st.session_state.qc_candidates = discover_three_candidates(
                    ultimate_bot,
                    st.session_state.qc_config,
                    conn,
                )
            finally:
                conn.close()
            st.session_state.qc_selected_story = None
            st.session_state.qc_script = None
            st.session_state.qc_visuals = []
            st.session_state.qc_metadata_options = {}
            st.session_state.qc_approvals = {}
            st.session_state.qc_ai_started = False
            st.session_state.qc_ai_done = False
            st.session_state.qc_ai_snapshot = {}
            st.session_state.qc_stage = "discovery"
        except Exception as exc:
            st.error(f"Discovery failed: {type(exc).__name__}: {exc}")


if st.session_state.qc_candidates:
    st.markdown("### 2. Story choices")
    st.caption("Nothing after this point is started until you select a story. Discovery itself does not run script, TTS, image, render, or upload work.")
    candidate_labels = [
        f"#{item.get('discovery_rank', i + 1)} — {item.get('title', 'Untitled')}"
        for i, item in enumerate(st.session_state.qc_candidates)
    ]
    chosen_label = st.selectbox("Candidate", candidate_labels, key="qc_candidate_choice")
    idx = candidate_labels.index(chosen_label)
    chosen = st.session_state.qc_candidates[idx]
    st.info(
        f"**Source:** {chosen.get('source_label', 'Unknown')}  \n"
        f"**Reason:** {chosen.get('discovery_reason', 'No reason recorded.')}"
    )
    if st.button("✅ Approve story and continue", use_container_width=True):
        st.session_state.qc_selected_story = dict(chosen)
        st.session_state.qc_stage = "story"


if st.session_state.qc_selected_story:
    story = st.session_state.qc_selected_story
    with st.container(border=True):
        st.markdown("### Selected story")
        st.markdown(f"**{story.get('title', '')}**")
        if story.get("story_url"):
            st.caption(story["story_url"])


# -------------------------
# AI RUN
# -------------------------
if st.session_state.qc_selected_story and st.session_state.qc_mode == "AI Run":
    with st.container(border=True):
        st.markdown("### 🤖 AI Run")
        st.caption("Uses the existing production path, including its self-critique, grounding gates, visual QC and final QC. The automatic YouTube upload remains blocked by the existing safety gate.")
        if not st.session_state.qc_ai_started and st.button("▶️ Start AI Run", type="primary", use_container_width=True):
            try:
                controller = WorkflowController(ultimate_bot)
                controller.start_production(st.session_state.qc_config, st.session_state.qc_selected_story)
                st.session_state.qc_ai_controller = controller
                st.session_state.qc_ai_started = True
                st.session_state.qc_stage = "script"
            except Exception as exc:
                st.error(f"AI run could not start: {type(exc).__name__}: {exc}")

        controller = st.session_state.get("qc_ai_controller")
        if st.session_state.qc_ai_started and controller:
            progress = st.empty()
            status = st.empty()
            while True:
                snap = controller.snapshot()
                st.session_state.qc_ai_snapshot = snap
                with progress.container():
                    st.progress(int(snap.get("percent", 0)) / 100.0, text=f"{snap.get('stage', 'idle').title()} — {snap.get('message', '')}")
                with status.container():
                    script = snap.get("script_data") or {}
                    metadata = snap.get("final_metadata") or {}
                    if script:
                        st.markdown("#### Script selected by AI")
                        narration = script.get("narration") or script.get("voiceover") or script.get("text")
                        if narration:
                            st.text_area("Narration", str(narration), height=260, disabled=True, key="ai_script_preview")
                    if metadata:
                        st.markdown("#### Metadata selected by AI")
                        st.json(metadata)
                    if snap.get("video_path"):
                        st.success(f"Rendered video: `{snap['video_path']}`")
                    if snap.get("error"):
                        st.error(snap["error"])
                if not snap.get("thread_alive"):
                    st.session_state.qc_ai_done = True
                    if snap.get("completed") or snap.get("video_path"):
                        st.session_state.qc_stage = "render"
                    break
                time.sleep(1)


# -------------------------
# MANUAL RUN
# -------------------------
if st.session_state.qc_selected_story and st.session_state.qc_mode == "Manual Run":
    if st.session_state.qc_script is None:
        st.markdown("### 3. Script")
        if st.button("✍️ Generate and QC script", use_container_width=True):
            config = st.session_state.qc_config
            lang_cfg = ultimate_bot.LANGUAGES[config["language"]]
            genre_key = config["category"]
            try:
                conn = ultimate_bot.sqlite3.connect(ultimate_bot.DB_PATH)
                try:
                    script = ultimate_bot.write_script(story, lang_cfg, genre_key, conn, config["format_mode"])
                finally:
                    conn.close()
                if not script:
                    raise RuntimeError("Script generation returned no usable script.")
                st.session_state.qc_script = script
                st.session_state.qc_stage = "script"
            except Exception as exc:
                st.error(f"Script generation failed: {type(exc).__name__}: {exc}")

    if st.session_state.qc_script:
        with st.container(border=True):
            st.markdown("### Chosen script — informational")
            st.caption("The script is shown for review, but this control room does not require a separate script approval click.")
            script = st.session_state.qc_script
            if isinstance(script, dict):
                narration = script.get("narration") or script.get("voiceover") or script.get("text")
                if narration:
                    st.text_area("Narration", str(narration), height=320, disabled=True)
                scenes = script.get("script")
                if isinstance(scenes, list):
                    st.json(scenes)
                if script.get("self_critique"):
                    st.caption("Self-critique")
                    st.write(script.get("self_critique"))
            else:
                st.text_area("Script", str(script), height=320, disabled=True)
            st.markdown("### Script actions")
            st.caption("Continue only when you are satisfied with the script. Script is the one item that does not require an approval state in this workflow.")
            if st.button("➡️ Continue to visuals", use_container_width=True):
                st.session_state.qc_stage = "visuals"

    def _collect_paths(value: Any) -> List[str]:
        found: List[str] = []
        if isinstance(value, str):
            lower = value.lower()
            if os.path.isfile(value) and lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
                found.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                found.extend(_collect_paths(item))
        elif isinstance(value, (list, tuple)):
            for item in value:
                found.extend(_collect_paths(item))
        return list(dict.fromkeys(found))

    if st.session_state.qc_script and st.session_state.qc_stage in {"visuals", "metadata", "audio", "render"}:
        with st.container(border=True):
            st.markdown("### 4. Visuals")
            if not st.session_state.qc_visuals:
                if st.button("🖼️ Source and show selected visuals", use_container_width=True):
                    try:
                        lang_cfg = ultimate_bot.LANGUAGES[st.session_state.qc_config["language"]]
                        result = ultimate_bot.process_visuals_async(
                            st.session_state.qc_script,
                            lang_cfg,
                            st.session_state.qc_config["format_mode"],
                        )
                        if inspect.isawaitable(result):
                            result = asyncio.run(result)
                        st.session_state.qc_visuals = result or []
                        st.session_state.qc_stage = "visuals"
                    except Exception as exc:
                        st.error(f"Visual sourcing failed: {type(exc).__name__}: {exc}")
            paths = _collect_paths(st.session_state.qc_visuals)
            if paths:
                cols = st.columns(min(3, len(paths)))
                for i, path in enumerate(paths):
                    with cols[i % len(cols)]:
                        st.image(path, caption=os.path.basename(path), use_container_width=True)
                st.caption("These are the image assets actually returned by the visual pipeline.")
            elif st.session_state.qc_visuals:
                st.code(json.dumps(st.session_state.qc_visuals, indent=2, default=str), language="json")
            if st.session_state.qc_visuals:
                choice = st.radio(
                    "Visual approval",
                    ["Approve visual package", "Reject visual package and source again"],
                    key="qc_visual_approval",
                )
                if choice == "Approve visual package" and st.button("✅ Approve visuals", use_container_width=True):
                    st.session_state.qc_approvals["visuals"] = True
                    st.session_state.qc_stage = "metadata"
                elif choice.startswith("Reject") and st.button("🔄 Clear and re-source visuals", use_container_width=True):
                    st.session_state.qc_visuals = []

    def _metadata_variants(script: Dict[str, Any], genre_cfg: Dict[str, Any], trend_keyword: str) -> Dict[str, List[str]]:
        title, description, tags = _build_clean_metadata(script, genre_cfg, trend_keyword)
        title = str(title or "").strip()
        description = str(description or "").strip()
        comment = build_pinned_comment(script, title, genre_cfg.get("label", ""))
        titles = [
            title,
            title.rstrip(".!?") + " — What happened and why it matters",
            "What You Need to Know: " + title,
        ]
        descriptions = [
            description,
            (description[:650].rsplit(" ", 1)[0] + "…") if len(description) > 680 else description,
            (description + "\n\nFollow for the next verified update.").strip(),
        ]
        comments = [
            comment,
            "📌 " + comment if not comment.startswith("📌") else comment,
            comment + "\n\nWhat part of this story should we follow next?",
        ]
        return {
            "title": list(dict.fromkeys(titles))[:3],
            "description": list(dict.fromkeys(descriptions))[:3],
            "pinned_comment": list(dict.fromkeys(comments))[:3],
            "tags": tags if isinstance(tags, list) else [],
        }

    if st.session_state.qc_script and st.session_state.qc_stage == "metadata" and st.session_state.qc_visuals:
        with st.container(border=True):
            st.markdown("### 5. Metadata approval")
            genre_cfg = ultimate_bot.CONTENT_CATEGORIES.get(
                st.session_state.qc_config["category"],
                ultimate_bot.CONTENT_CATEGORIES.get("national_global_affairs", {}),
            )
            if not st.session_state.qc_metadata_options:
                st.session_state.qc_metadata_options = _metadata_variants(st.session_state.qc_script, genre_cfg, "")
            selections = {}
            for field, label in (("title", "Title"), ("description", "Description"), ("pinned_comment", "Pinned comment")):
                options = st.session_state.qc_metadata_options.get(field, [])
                selections[field] = st.radio(label, options or ["No option generated"], key=f"qc_{field}_pick")
                st.session_state.qc_approvals[field] = False
            st.session_state.qc_approved_metadata = selections
            st.markdown("**Nothing is accepted automatically.** Select an option for every field, then explicitly approve each one.")
            a1, a2, a3 = st.columns(3)
            for col, field in zip((a1, a2, a3), ("title", "description", "pinned_comment")):
                with col:
                    if st.button(f"✅ Approve {field.replace('_', ' ')}", key=f"approve_{field}"):
                        st.session_state.qc_approvals[field] = True
            if all(st.session_state.qc_approvals.get(x) for x in ("title", "description", "pinned_comment")):
                st.success("All metadata fields approved. The next stage is audio.")
                if st.button("➡️ Continue to audio", use_container_width=True):
                    st.session_state.qc_stage = "audio"


with st.expander("Code / pipeline explanation", expanded=(code_view != "None")):
    if code_view == "Pipeline overview":
        st.markdown(
            "1. **Discovery** finds and filters candidates.\n"
            "2. **Story approval** locks the exact story used for production.\n"
            "3. **Script** writes the narration and runs the existing script safeguards/QC.\n"
            "4. **Visuals** sources and verifies the content-first visual package.\n"
            "5. **Metadata** gives you explicit title, description and pinned-comment choices.\n"
            "6. **Audio** generates narration/timings.\n"
            "7. **Render** assembles the approved assets with the already-approved branding.\n"
            "8. **Upload remains separately gated."
        )
    elif code_view != "None":
        base = Path(ultimate_bot.BASE_DIR)
        path = base / code_view
        if path.is_file():
            st.code(path.read_text(encoding="utf-8", errors="replace"), language="python")
        else:
            st.warning(f"Local file not found: {path}")


st.caption("The approved subtitle/logo styling is not modified by this QC page. This page controls workflow decisions only.")
