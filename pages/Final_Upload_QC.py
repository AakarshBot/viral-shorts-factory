from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

import ultimate_bot

st.set_page_config(page_title="Final Upload QC", page_icon="🔐", layout="wide")

st.markdown("# 🔐 Final Upload QC")
st.caption("This is the final gate before YouTube upload. Nothing is uploaded from this page.")

base = Path(ultimate_bot.BASE_DIR)
output_dir = base / "output"

# Find the most recently rendered video so the final decision is attached to a
# concrete artifact rather than being an abstract upload setting.
video_candidates = []
if output_dir.exists():
    for path in output_dir.iterdir():
        if path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
            try:
                video_candidates.append((path.stat().st_mtime, path))
            except OSError:
                pass
video_candidates.sort(reverse=True)

latest_video = video_candidates[0][1] if video_candidates else None

with st.container(border=True):
    st.markdown("### Final video")
    if latest_video:
        st.success(f"Ready artifact: `{latest_video}`")
        st.caption(f"Modified: {datetime.fromtimestamp(latest_video.stat().st_mtime).isoformat(sep=' ', timespec='seconds')}")
    else:
        st.warning("No rendered video was found in the output folder yet.")

st.markdown("### Last approval")
visibility = st.radio(
    "When the video is uploaded, what visibility should YouTube use?",
    ["Private", "Public"],
    index=0,
    key="final_upload_visibility",
    horizontal=True,
)

st.warning(
    "Choose **Private** for a safe review upload. Choose **Public** only when you explicitly want the video published."
)

confirm = st.checkbox(
    f"I approve this video being uploaded as **{visibility}**.",
    key="final_upload_visibility_confirm",
)

if confirm and st.button("✅ Approve final upload visibility", type="primary", use_container_width=True):
    manifest = {
        "approved": True,
        "visibility": visibility.lower(),
        "video_path": str(latest_video) if latest_video else "",
        "approved_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_path = output_dir / "final_upload_qc.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    st.session_state["final_upload_visibility_approved"] = visibility.lower()
    st.success(f"Final upload visibility approved: **{visibility}**")
    st.caption(f"QC decision saved to `{manifest_path}`. Upload remains separately gated by the factory.")

st.markdown("---")
st.caption("This is intentionally the last QC decision: Story → Script → Visuals → Metadata → Audio/Render → **Upload visibility**.")
