from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

from workflow_runtime import CRICKET_CATEGORIES, FORMAT_OPTIONS, WorkflowController, discover_three_candidates
from youtube_comment_runtime import _build_clean_metadata, build_pinned_comment


PIPELINE_STAGES = [
    ("setup", "Setup"),
    ("discovery", "Discovery"),
    ("story", "Story"),
    ("script", "Script"),
    ("visuals", "Visuals"),
    ("metadata", "Metadata"),
    ("audio", "Audio"),
    ("render", "Render"),
    ("upload", "Upload"),
]


def _category_options(bot, format_mode: str) -> Dict[str, str]:
    output: Dict[str, str] = {}
    for key, cfg in bot.CONTENT_CATEGORIES.items():
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


def _script_text(script: Any) -> str:
    if not isinstance(script, dict):
        return str(script or "")
    scenes = script.get("script")
    if isinstance(scenes, list):
        lines = [str(scene.get("voiceover", "")).strip() for scene in scenes if isinstance(scene, dict)]
        return "\n\n".join(line for line in lines if line)
    return str(script.get("narration") or script.get("voiceover") or script.get("text") or "")


def _metadata_variants(bot, script: Dict[str, Any], category_key: str) -> Dict[str, List[str]]:
    genre_cfg = bot.CONTENT_CATEGORIES.get(category_key, bot.CONTENT_CATEGORIES.get("national_global_affairs", {}))
    title, description, tags = _build_clean_metadata(script, genre_cfg, "")
    title = str(title or "").strip()
    description = str(description or "").strip()
    comment = build_pinned_comment(script, title, genre_cfg.get("label", ""))
    titles = list(dict.fromkeys([
        title,
        title.rstrip(".!?") + " — What happened and why it matters",
        "What You Need to Know: " + title,
    ]))
    descriptions = list(dict.fromkeys([
        description,
        description[:680].rsplit(" ", 1)[0] + "…" if len(description) > 680 else description,
        (description + "\n\nFollow for the next verified update.").strip(),
    ]))
    comments = list(dict.fromkeys([
        comment,
        "📌 " + comment if not comment.startswith("📌") else comment,
        comment + "\n\nWhat part of this story should we follow next?",
    ]))
    return {"title": titles[:3], "description": descriptions[:3], "pinned_comment": comments[:3], "tags": tags if isinstance(tags, list) else []}


def _code_view(bot, selection: str) -> None:
    if selection == "None":
        return
    if selection == "Pipeline overview":
        st.code(
            "Discovery → Story approval → Script → Visual approval → Metadata approval → Audio → Render → Upload visibility\n\n"
            "Manual Run: each arrow is an explicit human gate except Script.\n"
            "AI Run: existing WorkflowController runs the automated self-critique/QC production path, then returns the result to the dashboard for human final approval before upload.\n"
            "Upload is never automatic. Visibility is selected at the final step.",
            language="text",
        )
        return
    mapping = {
        "app.py": "app.py",
        "workflow_runtime.py": "workflow_runtime.py",
        "ultimate_bot.py": "ultimate_bot.py",
        "visual_runtime.py": "visual_runtime.py",
        "script_runtime.py": "script_runtime.py",
    }
    filename = mapping.get(selection)
    if not filename:
        return
    path = Path(bot.BASE_DIR) / filename
    if path.is_file():
        st.code(path.read_text(encoding="utf-8", errors="replace"), language="python")
    else:
        st.warning(f"Code file not found: {path}")


def _init_state() -> None:
    defaults = {
        "nr_mode": "Manual Run",
        "nr_stage": "setup",
        "nr_config": {},
        "nr_candidates": [],
        "nr_story": None,
        "nr_script": None,
        "nr_visuals": [],
        "nr_metadata": {},
        "nr_approvals": {},
        "nr_audio": None,
        "nr_rendered": "",
        "nr_ai_controller": None,
        "nr_ai_started": False,
        "nr_ai_snapshot": {},
        "nr_visibility": "Private",
        "nr_upload_result": "",
        "nr_approved_metadata": {},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _reset_run() -> None:
    keep_mode = st.session_state.get("nr_mode", "Manual Run")
    for key in (
        "nr_config", "nr_candidates", "nr_story", "nr_script", "nr_visuals", "nr_metadata",
        "nr_approvals", "nr_audio", "nr_rendered", "nr_ai_controller", "nr_ai_started",
        "nr_ai_snapshot", "nr_upload_result", "nr_approved_metadata",
    ):
        st.session_state[key] = {} if key in {"nr_config", "nr_metadata", "nr_approvals", "nr_ai_snapshot", "nr_approved_metadata"} else [] if key in {"nr_candidates", "nr_visuals"} else None if key in {"nr_story", "nr_script", "nr_audio", "nr_ai_controller"} else ""
    st.session_state.nr_mode = keep_mode
    st.session_state.nr_stage = "setup"


def _manual_stage_state(stage: str) -> tuple[str, float, str]:
    approvals = st.session_state.get("nr_approvals", {})
    story = bool(st.session_state.get("nr_story"))
    script = bool(st.session_state.get("nr_script"))
    visuals = bool(st.session_state.get("nr_visuals"))
    metadata = bool(st.session_state.get("nr_metadata"))
    audio = st.session_state.get("nr_audio") is not None
    rendered = bool(st.session_state.get("nr_rendered"))
    uploaded = bool(st.session_state.get("nr_upload_result"))

    if stage == "setup":
        return ("complete", 1.0, "Configuration ready") if st.session_state.get("nr_config") else ("active", 0.0, "Choose format, language and category")
    if stage == "discovery":
        return ("complete", 1.0, "3 stories available") if st.session_state.get("nr_candidates") else ("locked", 0.0, "Waiting for discovery")
    if stage == "story":
        return ("complete", 1.0, "Story approved") if story else ("locked", 0.0, "Choose and approve a story")
    if stage == "script":
        if script:
            return "complete", 1.0, "Script ready"
        return ("active", 0.0, "Generate the script") if story else ("locked", 0.0, "Waiting for story approval")
    if stage == "visuals":
        if approvals.get("visuals"):
            return "complete", 1.0, "Visuals approved"
        if visuals:
            return "waiting", 1.0, "Waiting for your visual approval"
        return ("active", 0.0, "Source and verify visuals") if script else ("locked", 0.0, "Waiting for script")
    if stage == "metadata":
        meta_fields = ("title", "description", "pinned_comment")
        approved = sum(1 for field in meta_fields if approvals.get(field))
        if approved == 3:
            return "complete", 1.0, "All metadata approved"
        if metadata:
            return "waiting", approved / 3.0, f"{approved}/3 metadata approvals"
        return ("active", 0.0, "Generate and choose metadata") if approvals.get("visuals") else ("locked", 0.0, "Waiting for visual approval")
    if stage == "audio":
        if audio:
            return "complete", 1.0, "Audio ready"
        return ("active", 0.0, "Generate narration and timings") if all(approvals.get(f) for f in ("visuals", "title", "description", "pinned_comment")) else ("locked", 0.0, "Waiting for metadata approval")
    if stage == "render":
        if rendered:
            return "complete", 1.0, "Final video rendered"
        return ("active", 0.0, "Render the final video") if audio else ("locked", 0.0, "Waiting for audio")
    if stage == "upload":
        if uploaded:
            return "complete", 1.0, "Uploaded"
        return ("active", 0.0, "Waiting for final visibility and upload") if rendered else ("locked", 0.0, "Waiting for rendered video")
    return "locked", 0.0, ""


def _ai_stage_state(stage: str, snap: Dict[str, Any]) -> tuple[str, float, str]:
    current = str(snap.get("stage") or "idle")
    percent = max(0, min(100, int(snap.get("percent", 0) or 0)))
    completed = bool(snap.get("completed"))
    stage_ranges = {
        "setup": (0, 15),
        "discovery": (15, 23),
        "story": (23, 27),
        "script": (27, 40),
        "visuals": (55, 76),
        "audio": (41, 54),
        "render": (77, 95),
        "upload": (96, 100),
    }
    metadata_done = bool(snap.get("final_metadata"))
    if stage == "metadata":
        if completed or metadata_done:
            return "complete", 1.0, "Metadata generated and QC checked"
        if current in {"metadata", "qc"}:
            return "active", 0.7, str(snap.get("message") or "Checking metadata")
        return ("complete", 1.0, "Passed in automated QC") if percent > 76 else ("locked", 0.0, "Waiting for automated production")
    if stage == "story":
        return ("complete", 1.0, "Story selected") if snap.get("selected_story") else ("locked", 0.0, "Waiting for story selection")
    if completed and stage != "upload":
        return "complete", 1.0, "Complete"
    if stage == current:
        lo, hi = stage_ranges.get(stage, (0, 100))
        local = 0.0 if hi <= lo else max(0.0, min(1.0, (percent - lo) / (hi - lo)))
        return "active", local, str(snap.get("message") or "Working")
    if current == "upload" and stage in {"script", "visuals", "audio", "render", "metadata"}:
        return "complete", 1.0, "Complete"
    order = {key: i for i, (key, _) in enumerate(PIPELINE_STAGES)}
    if order.get(stage, 0) < order.get(current, 0):
        return "complete", 1.0, "Complete"
    return "locked", 0.0, "Waiting"


def _render_pipeline(bot) -> None:
    mode = st.session_state.get("nr_mode", "Manual Run")
    snap = st.session_state.get("nr_ai_snapshot", {}) if mode == "AI Run" else {}
    if mode == "Manual Run":
        states = {stage: _manual_stage_state(stage) for stage, _ in PIPELINE_STAGES}
    else:
        states = {stage: _ai_stage_state(stage, snap) for stage, _ in PIPELINE_STAGES}

    complete_count = sum(1 for status, _, _ in states.values() if status == "complete")
    active = next(((stage, label, states[stage]) for stage, label in PIPELINE_STAGES if states[stage][0] in {"active", "waiting"}), None)
    overall = complete_count / len(PIPELINE_STAGES)
    if active:
        overall = min(1.0, overall + (active[2][1] / len(PIPELINE_STAGES)))

    current_label = active[1] if active else "Ready"
    st.progress(overall, text=f"Pipeline: {complete_count}/{len(PIPELINE_STAGES)} stages complete · {current_label}")

    for stage, label in PIPELINE_STAGES:
        status, value, detail = states[stage]
        if status == "complete":
            icon, state_text = "✅", "Complete"
        elif status == "active":
            icon, state_text = "🔄", "Working"
        elif status == "waiting":
            icon, state_text = "⏸️", "Waiting for approval"
        else:
            icon, state_text = "○", "Locked"
        left, right = st.columns([1.1, 4.9])
        left.markdown(f"**{icon} {label}**")
        with right:
            st.caption(f"{state_text} · {detail}")
            if status == "active":
                st.progress(value, text=f"{label}: {int(value * 100)}%")
            elif status == "waiting":
                st.progress(value, text=f"{label}: approval gate")


def render_dashboard(bot) -> None:
    _init_state()

    st.markdown("# 🎬 Viral Shorts Factory")
    st.caption("Newsroom control room: the factory does the work, but you remain the final editor.")

    with st.sidebar:
        st.markdown("### Run control")
        new_mode = st.radio(
            "Run mode",
            ["AI Run", "Manual Run"],
            index=0 if st.session_state.nr_mode == "AI Run" else 1,
            help="AI Run uses the existing automated self-critique/QC production path. Manual Run exposes each decision point.",
        )
        if new_mode != st.session_state.nr_mode and not st.session_state.nr_story:
            st.session_state.nr_mode = new_mode
        elif new_mode != st.session_state.nr_mode and st.session_state.nr_story:
            st.warning("Finish or reset the current run before switching modes.")

        st.markdown("### Inspect")
        code_view = st.selectbox(
            "Code / pipeline",
            ["None", "Pipeline overview", "app.py", "workflow_runtime.py", "ultimate_bot.py", "visual_runtime.py", "script_runtime.py"],
        )
        if st.button("↻ Reset current run", use_container_width=True):
            _reset_run()
            st.rerun()

    _render_pipeline(bot)

    if code_view != "None":
        with st.expander("Code / pipeline", expanded=True):
            _code_view(bot, code_view)

    if st.session_state.nr_mode == "AI Run":
        _render_ai_run(bot, PIPELINE_STAGES)
    else:
        _render_manual_run(bot, PIPELINE_STAGES)


def _render_setup_and_discovery(bot) -> None:
    with st.container(border=True):
        st.markdown("### 1. Choose the topic pool")
        c1, c2 = st.columns(2)
        with c1:
            format_label = st.selectbox("Format", list(FORMAT_OPTIONS.keys()), key="nr_format")
            language_label = st.selectbox("Language", [cfg["label"] for cfg in bot.LANGUAGES.values()], key="nr_language")
        with c2:
            format_mode = FORMAT_OPTIONS[format_label]
            if format_label == "Cricket":
                st.selectbox("Cricket scope", list(CRICKET_CATEGORIES.keys()), key="nr_cricket_scope")
                st.text_input("Specific topic (optional)", key="nr_topic", placeholder="e.g. BCCI impact player rule")
            else:
                options = _category_options(bot, format_mode)
                st.selectbox("Category", list(options.keys()), key="nr_category_label")

        lang_key = next((key for key, cfg in bot.LANGUAGES.items() if cfg["label"] == language_label), "english")
        if format_label == "Cricket":
            category_key = "sports_stories_of_day"
            config = {
                "format_mode": "regular", "display_format": "Cricket", "category": category_key,
                "language": lang_key, "cricket_pipeline": True,
                "cricket_category": st.session_state.nr_cricket_scope,
                "requested_topic": str(st.session_state.get("nr_topic", "") or "").strip(),
                "language_label": language_label,
            }
        else:
            options = _category_options(bot, format_mode)
            category_key = options[st.session_state.nr_category_label]
            config = {
                "format_mode": format_mode, "display_format": format_label,
                "category": category_key, "language": lang_key,
                "cricket_pipeline": False, "language_label": language_label,
            }
        st.session_state.nr_config = config
        if st.button("🔎 Discover 3 stories", type="primary", use_container_width=True):
            import sqlite3
            conn = sqlite3.connect(bot.DB_PATH)
            try:
                st.session_state.nr_candidates = discover_three_candidates(bot, config, conn)
            finally:
                conn.close()
            st.session_state.nr_story = None
            st.session_state.nr_script = None
            st.session_state.nr_visuals = []
            st.session_state.nr_metadata = {}
            st.session_state.nr_approvals = {}
            st.session_state.nr_audio = None
            st.session_state.nr_rendered = ""
            st.session_state.nr_approved_metadata = {}
            st.session_state.nr_upload_result = ""
            st.session_state.nr_stage = "discovery"

    if st.session_state.nr_candidates:
        st.markdown("### 2. Story choices")
        labels = [f"#{x.get('discovery_rank', i + 1)} — {x.get('title', 'Untitled')}" for i, x in enumerate(st.session_state.nr_candidates)]
        picked = st.selectbox("Choose the story", labels, key="nr_candidate")
        item = st.session_state.nr_candidates[labels.index(picked)]
        st.info(f"**Source:** {item.get('source_label', 'News source')}\n\n{item.get('discovery_reason', '')}")
        if item.get("story_url"):
            st.link_button("Open source article", item["story_url"])
        if st.button("✅ Approve this story", type="primary", use_container_width=True):
            st.session_state.nr_story = dict(item)
            st.session_state.nr_stage = "script"


def _render_ai_run(bot, stages) -> None:
    _render_setup_and_discovery(bot)
    if not st.session_state.nr_story:
        return
    with st.container(border=True):
        st.markdown("### 3. AI Run")
        st.caption("AI selects the script, visuals and metadata through the existing production/self-critique/QC path. The result is returned here for your final approval before upload.")
        if not st.session_state.nr_ai_started and st.button("▶️ Start AI production", type="primary", use_container_width=True):
            controller = WorkflowController(bot)
            controller.start_production(st.session_state.nr_config, st.session_state.nr_story)
            st.session_state.nr_ai_controller = controller
            st.session_state.nr_ai_started = True
            st.session_state.nr_stage = "script"
            st.rerun()

    controller = st.session_state.nr_ai_controller
    if not controller:
        return
    snap = controller.snapshot()
    st.session_state.nr_ai_snapshot = snap
    if snap.get("thread_alive"):
        st.info(f"**{snap.get('stage', 'working').title()}** — {snap.get('message', '')}")
        return

    if snap.get("error"):
        st.error(snap["error"])
        return
    if not snap.get("completed"):
        return

    st.session_state.nr_stage = "upload"
    script = snap.get("script_data") or {}
    metadata = snap.get("final_metadata") or {}
    st.markdown("### Script selected by AI")
    st.text_area("Narration", _script_text(script), height=300, disabled=True, key="nr_ai_script")
    if metadata:
        st.markdown("### Metadata selected by AI")
        st.write("**Title**")
        st.write(metadata.get("title", ""))
        st.write("**Description**")
        st.text_area("AI description", metadata.get("description", ""), height=150, disabled=True, key="nr_ai_desc")
        st.write("**Pinned comment**")
        st.write(metadata.get("pinned_comment", ""))

    st.markdown("### Final approval")
    st.caption("The script is informational. Approve the AI-selected title, description and pinned comment before upload.")
    checks = {}
    for field, label in (("title", "title"), ("description", "description"), ("pinned_comment", "pinned comment")):
        checks[field] = st.checkbox(f"Approve AI {label}", key=f"nr_ai_approve_{field}")
    visibility = st.selectbox("Final upload visibility", ["Private", "Public"], key="nr_visibility")
    if all(checks.values()) and st.button("⬆️ Upload approved AI result", type="primary", use_container_width=True):
        try:
            genre_cfg = bot.CONTENT_CATEGORIES.get(st.session_state.nr_config.get("category", "national_global_affairs"), bot.CONTENT_CATEGORIES["national_global_affairs"])
            uploader = WorkflowController(bot)
            uploader._real_uploader = getattr(bot, "upload_to_youtube", None)
            result = uploader.upload_manual(
                snap.get("video_path") or os.path.join(bot.ASSETS_DIR, "final_video_output.mp4"),
                script,
                str(metadata.get("title", "")),
                str(metadata.get("description", "")),
                str(metadata.get("pinned_comment", "")),
                "public" if visibility == "Public" else "private",
                genre_cfg,
            )
            st.session_state.nr_upload_result = result
            st.success(f"Uploaded successfully. Video ID: {result}")
        except Exception as exc:
            st.error(f"Upload failed: {type(exc).__name__}: {exc}")


def _render_manual_run(bot, stages) -> None:
    _render_setup_and_discovery(bot)
    story = st.session_state.nr_story
    if not story:
        return

    if st.session_state.nr_script is None:
        st.markdown("### 3. Script")
        st.caption("Script is shown for review. It does not require an approval click.")
        if st.button("✍️ Generate script", type="primary", use_container_width=True):
            import sqlite3
            config = st.session_state.nr_config
            conn = sqlite3.connect(bot.DB_PATH)
            try:
                st.session_state.nr_script = bot.write_script(story, bot.LANGUAGES[config["language"]], config["category"], conn, config["format_mode"])
            finally:
                conn.close()
            st.session_state.nr_stage = "script"
    else:
        script = st.session_state.nr_script
        with st.container(border=True):
            st.markdown("### Script")
            st.text_area("Narration", _script_text(script), height=320, disabled=True, key="nr_script_preview")
            if st.button("➡️ Continue to visuals", use_container_width=True):
                st.session_state.nr_stage = "visuals"

    script = st.session_state.nr_script
    if not script:
        return

    if st.session_state.nr_stage in {"visuals", "metadata", "audio", "render", "upload"}:
        with st.container(border=True):
            st.markdown("### 4. Visual approval")
            if not st.session_state.nr_visuals:
                if st.button("🖼️ Source visuals", use_container_width=True):
                    lang_cfg = bot.LANGUAGES[st.session_state.nr_config["language"]]
                    result = bot.process_visuals_async(script, lang_cfg, st.session_state.nr_config["format_mode"])
                    if inspect.isawaitable(result):
                        result = asyncio.run(result)
                    st.session_state.nr_visuals = result or []
            paths = _collect_paths(st.session_state.nr_visuals)
            if paths:
                cols = st.columns(min(3, len(paths)))
                for i, path in enumerate(paths):
                    with cols[i % len(cols)]:
                        st.image(path, caption=os.path.basename(path), use_container_width=True)
            if st.session_state.nr_visuals:
                approved = st.checkbox("✅ I approve these visuals", key="nr_visuals_approved")
                if approved:
                    st.session_state.nr_approvals["visuals"] = True
                    st.session_state.nr_stage = "metadata"

    if st.session_state.nr_approvals.get("visuals") and st.session_state.nr_stage in {"metadata", "audio", "render", "upload"}:
        with st.container(border=True):
            st.markdown("### 5. Metadata choices")
            if not st.session_state.nr_metadata:
                st.session_state.nr_metadata = _metadata_variants(bot, script, st.session_state.nr_config["category"])
            selections = {}
            for field, label in (("title", "Title"), ("description", "Description"), ("pinned_comment", "Pinned comment")):
                options = st.session_state.nr_metadata.get(field, [])
                selections[field] = st.radio(f"Choose {label.lower()}", options or ["No option generated"], key=f"nr_pick_{field}")
            st.session_state.nr_approved_metadata = selections
            a1, a2, a3 = st.columns(3)
            for col, field in zip((a1, a2, a3), ("title", "description", "pinned_comment")):
                with col:
                    if st.button(f"✅ Approve {field.replace('_', ' ')}", key=f"nr_approve_{field}"):
                        st.session_state.nr_approvals[field] = True
            if all(st.session_state.nr_approvals.get(f) for f in ("title", "description", "pinned_comment")):
                st.session_state.nr_stage = "audio"
                st.success("Metadata approved. Audio is unlocked.")

    if all(st.session_state.nr_approvals.get(f) for f in ("visuals", "title", "description", "pinned_comment")) and st.session_state.nr_stage in {"audio", "render", "upload"}:
        with st.container(border=True):
            st.markdown("### 6. Audio")
            if st.session_state.nr_audio is None:
                st.caption("Audio is generated only after visual and metadata approval.")
                if st.button("🎙️ Generate audio", type="primary", use_container_width=True):
                    result = bot.generate_voiceover_and_timestamps(script, bot.LANGUAGES[st.session_state.nr_config["language"]])
                    if inspect.isawaitable(result):
                        result = asyncio.run(result)
                    st.session_state.nr_audio = result
                    st.session_state.nr_stage = "render"
            else:
                audio_paths, _timings = st.session_state.nr_audio
                st.success(f"Audio generated: {len(audio_paths or [])} scene track(s).")

    if st.session_state.nr_audio is not None and st.session_state.nr_stage in {"render", "upload"}:
        with st.container(border=True):
            st.markdown("### 7. Render")
            if not st.session_state.nr_rendered:
                if st.button("🎬 Render final video", type="primary", use_container_width=True):
                    audio_paths, word_timings = st.session_state.nr_audio
                    result = bot.compile_video(
                        st.session_state.nr_visuals,
                        audio_paths,
                        word_timings,
                        bot.LANGUAGES[st.session_state.nr_config["language"]],
                        st.session_state.nr_config["format_mode"],
                    )
                    st.session_state.nr_rendered = str(result or "")
                    st.session_state.nr_stage = "upload"
            elif os.path.isfile(st.session_state.nr_rendered):
                st.video(st.session_state.nr_rendered)
                st.success("Final render complete. Upload is now the only remaining action.")

    if st.session_state.nr_rendered and os.path.isfile(st.session_state.nr_rendered):
        with st.container(border=True):
            st.markdown("### 8. Final upload")
            st.warning("Last decision: choose whether YouTube should receive this video as Private or Public.")
            visibility = st.selectbox("Upload visibility", ["Private", "Public"], key="nr_visibility")
            st.write("**Approved title:**", st.session_state.nr_approved_metadata.get("title", ""))
            st.write("**Approved description:**", st.session_state.nr_approved_metadata.get("description", ""))
            st.write("**Approved pinned comment:**", st.session_state.nr_approved_metadata.get("pinned_comment", ""))
            if st.button("⬆️ Upload to YouTube", type="primary", use_container_width=True):
                try:
                    genre_cfg = bot.CONTENT_CATEGORIES.get(st.session_state.nr_config.get("category", "national_global_affairs"), bot.CONTENT_CATEGORIES["national_global_affairs"])
                    uploader = WorkflowController(bot)
                    uploader._real_uploader = getattr(bot, "upload_to_youtube", None)
                    result = uploader.upload_manual(
                        st.session_state.nr_rendered,
                        script,
                        st.session_state.nr_approved_metadata["title"],
                        st.session_state.nr_approved_metadata["description"],
                        st.session_state.nr_approved_metadata["pinned_comment"],
                        "public" if visibility == "Public" else "private",
                        genre_cfg,
                    )
                    st.session_state.nr_upload_result = result
                    st.success(f"Uploaded successfully. Video ID: {result}")
                except Exception as exc:
                    st.error(f"Upload failed: {type(exc).__name__}: {exc}")
