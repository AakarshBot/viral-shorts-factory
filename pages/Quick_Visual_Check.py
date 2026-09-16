from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from PIL import Image, ImageDraw, ImageFilter

from subtitle_runtime import create_glossy_logo_watermark, generate_readable_karaoke_clip


st.set_page_config(page_title="Quick Visual Check", page_icon="🧪", layout="wide")

BASE_DIR = Path(__file__).resolve().parents[1]
BRAND_DIR = BASE_DIR / "brand_assets"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def logo_path() -> Path | None:
    for name in ("logo.png", "channels4_profile.jpg"):
        candidate = BRAND_DIR / name
        if candidate.exists():
            return candidate
    return None


def build_preview(subtitle_text: str, active_index: int, subtitle_y: float) -> tuple[Image.Image, Path]:
    width, height = 1080, 1920
    image = Image.new("RGB", (width, height), (16, 22, 34))
    draw = ImageDraw.Draw(image)

    # Synthetic background so this page never touches an external provider.
    for y in range(height):
        shade = int(24 + (y / height) * 24)
        draw.line((0, y, width, y), fill=(shade, shade + 5, shade + 16))

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((70, 180, 1010, 1060), fill=(0, 191, 255, 32))
    glow_draw.ellipse((120, 820, 960, 1810), fill=(255, 140, 0, 22))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    image = Image.alpha_composite(image.convert("RGBA"), glow)

    # Refined frame-edge treatment matching the production visual runtime.
    band = 40
    edge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    edge_draw = ImageDraw.Draw(edge)
    edge_draw.rectangle((0, 0, width, band - 1), fill=(8, 14, 24, 54))
    edge_draw.rectangle((0, height - band, width, height - 1), fill=(8, 14, 24, 62))
    edge_draw.rectangle((0, band - 2, width, band), fill=(255, 194, 78, 175))
    edge_draw.rectangle((0, height - band - 1, width, height - band + 1), fill=(64, 196, 255, 150))
    image = Image.alpha_composite(image, edge)

    # Production logo badge.
    badge_source = logo_path()
    if badge_source:
        badge = create_glossy_logo_watermark(str(badge_source), size=110)
        if badge:
            image.alpha_composite(badge, (width - 150, 60))

    # Production subtitle card.
    words = subtitle_text.split()
    chunk = [{"word": word} for word in words]
    subtitle_path = OUTPUT_DIR / "quick_visual_subtitle.png"
    generate_readable_karaoke_clip(
        chunk,
        max(0, min(active_index, len(chunk) - 1)) if chunk else -1,
        "arialbd.ttf",
        width,
        str(subtitle_path),
    )
    subtitle = Image.open(subtitle_path).convert("RGBA")
    x = (width - subtitle.width) // 2
    y = int(max(0.25, min(0.75, subtitle_y)) * height - subtitle.height / 2)
    image.alpha_composite(subtitle, (x, y))

    preview_path = OUTPUT_DIR / "quick_visual_check.png"
    image.convert("RGB").save(preview_path, "PNG")
    return image.convert("RGB"), preview_path


st.title("🧪 Quick Visual Check")
st.caption("Local-only preview. This page does not call AI, news, image providers, TTS, YouTube, or analytics.")

left, right = st.columns([0.34, 0.66], gap="large")
with left:
    subtitle = st.text_area(
        "Test subtitle",
        value="This is exactly how the spoken caption will appear on screen.",
        height=120,
    )
    words = subtitle.split()
    active_index = st.number_input(
        "Highlighted word index",
        min_value=0,
        max_value=max(0, len(words) - 1),
        value=min(4, max(0, len(words) - 1)),
        step=1,
        help="Zero-based word index used to simulate the karaoke highlight.",
    )
    subtitle_position = st.slider(
        "Subtitle vertical position",
        min_value=0.38,
        max_value=0.72,
        value=0.60,
        step=0.01,
        help="Preview only. Production uses the scene-safe positioning already defined by the renderer.",
    )

    if st.button("Render local preview", type="primary", use_container_width=True):
        preview, path = build_preview(subtitle, int(active_index), float(subtitle_position))
        st.session_state.quick_preview = preview
        st.session_state.quick_preview_path = str(path)

    st.info(
        "This is the safe styling check: it exercises the actual logo and subtitle renderers, "
        "but uses a synthetic background instead of consuming a single provider/API call."
    )

with right:
    preview = st.session_state.get("quick_preview")
    if preview is None:
        preview, path = build_preview(subtitle, int(active_index), float(subtitle_position))
        st.session_state.quick_preview = preview
        st.session_state.quick_preview_path = str(path)
    st.image(preview, caption="Current production branding preview", use_container_width=True)
    st.caption(f"Saved locally to: `{st.session_state.get('quick_preview_path', '')}`")

st.divider()
st.markdown("### What this checks")
checks = [
    "Frame overlay / edge treatment",
    "Channel logo badge",
    "Subtitle card width and height",
    "1–2 line wrapping and active-word highlight",
    "Overall vertical composition in a Shorts frame",
]
for item in checks:
    st.write(f"✅ {item}")
