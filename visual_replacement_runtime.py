"""Per-scene visual replacement bridge for the newsroom dashboard."""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List


def _image_hash(bot, path: str) -> str:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        getter = getattr(bot, "get_image_hash", None)
        if callable(getter):
            return str(getter(data))
        return hashlib.sha256(data).hexdigest()
    except Exception:
        return ""


def _scene_package_paths(package: Any) -> List[str]:
    found: List[str] = []
    if isinstance(package, str):
        if os.path.isfile(package):
            found.append(package)
    elif isinstance(package, dict):
        value = package.get("image") or package.get("image_path") or package.get("path")
        if isinstance(value, str) and os.path.isfile(value):
            found.append(value)
        elif isinstance(value, dict):
            found.extend(_scene_package_paths(value))
    elif isinstance(package, (list, tuple)):
        for item in package:
            found.extend(_scene_package_paths(item))
    return found


def _render_replacement_scene(bot, bg_img, seg: Dict[str, Any], script_data: Dict[str, Any], index: int, format_mode: str, font_choice: Any):
    from PIL import Image, ImageDraw
    from visual_runtime import _render_image_slide

    width, height = 1080, 1920
    bg_img = bg_img.resize((width, height), Image.Resampling.LANCZOS).convert("RGBA")
    video_title = script_data.get("title", "") or (script_data.get("titles") or [""])[0]
    if format_mode == "top5" and index == 0:
        return _render_image_slide(bot, bg_img, video_title or seg.get("voiceover", "Top 5"), "TODAY'S TOP 5", font_choice)
    if format_mode == "top5":
        clean = re.sub(r"(number\s*\d+|story\s*#?\d+|#\d+)", "", str(seg.get("voiceover", "")), flags=re.IGNORECASE).strip()
        return bot.render_top5_card(bg_img, max(1, 6 - index), 5, clean or seg.get("voiceover", ""), font_choice=font_choice)
    if index == 0:
        return bot.render_hook_card(bg_img, seg.get("voiceover", ""), font_choice=font_choice)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, 0, width, 40], fill=bot.PALETTE["accent_primary"] + (200,))
    draw.rectangle([0, height - 40, width, height], fill=bot.PALETTE["accent_secondary"] + (200,))
    return Image.alpha_composite(bg_img, overlay)


def replace_rejected_scenes(bot, script_data: Dict[str, Any], existing_packages: Any, decisions: Dict[str, str], language_cfg: Dict[str, Any], format_mode: str) -> Any:
    """Keep approved scene packages and source/render only scenes marked Reject."""
    from visual_runtime import _relevant_asset

    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not isinstance(scenes, list) or not isinstance(existing_packages, (list, tuple)):
        return None

    rejected_indexes: List[int] = []
    merged = list(existing_packages)
    used_hashes: set[str] = set()
    used_urls: set[str] = set()

    for index, package in enumerate(merged):
        paths = _scene_package_paths(package)
        if any(decisions.get(path) == "Reject" for path in paths):
            rejected_indexes.append(index)
            continue
        for path in paths:
            image_hash = _image_hash(bot, path)
            if image_hash:
                used_hashes.add(image_hash)

    if not rejected_indexes:
        return None

    font_choice = language_cfg.get("font") if isinstance(language_cfg, dict) else None
    video_title = script_data.get("title", "") or (script_data.get("titles") or [""])[0]
    for index in rejected_indexes:
        if index >= len(scenes):
            raise RuntimeError(f"Cannot replace visual scene {index + 1}: script has only {len(scenes)} scene(s).")
        seg = scenes[index]
        category = str(seg.get("sport_or_topic_category", "")).lower()
        bg_img, used_ai, source_type = _relevant_asset(bot, seg, category, used_urls, used_hashes, video_title)
        image_path = os.path.join(bot.ASSETS_DIR, f"scene_{index + 1}_img.jpg")
        rendered = _render_replacement_scene(bot, bg_img, seg, script_data, index, format_mode, font_choice)
        rendered.convert("RGB").save(image_path, "JPEG", quality=95)
        merged[index] = [{
            "image": image_path,
            "text": "" if (format_mode == "top5" or index == 0) else seg.get("voiceover", ""),
            "ai_generated": used_ai,
            "source_type": source_type,
        }]
        print(f"   [Visual Replacement] Scene {index + 1} replaced only | source={source_type} | ai={used_ai}", flush=True)

    return merged


def install_visual_replacement_bridge(bot, dashboard_module) -> None:
    """Patch the dashboard re-source action so rejection only replaces rejected scenes."""
    if getattr(bot, "_visual_replacement_bridge_installed", False):
        return

    original_render_manual = getattr(dashboard_module, "_render_manual_run", None)
    original_process = getattr(bot, "process_visuals_async", None)
    if not callable(original_render_manual) or not callable(original_process):
        return

    def wrapped_render_manual(runtime_bot, stages):
        import streamlit as st
        runtime_bot._dashboard_visual_decisions = dict(st.session_state.get("nr_visual_decisions", {}))
        runtime_bot._dashboard_existing_visuals = st.session_state.get("nr_visuals", [])
        original_render_manual(runtime_bot, stages)

    async def wrapped_process(script_data, language_cfg, format_mode="regular"):
        decisions = getattr(bot, "_dashboard_visual_decisions", {}) or {}
        existing = getattr(bot, "_dashboard_existing_visuals", [])
        if decisions and existing and any(value == "Reject" for value in decisions.values()):
            replacement = replace_rejected_scenes(bot, script_data, existing, decisions, language_cfg, format_mode)
            if replacement is not None:
                return replacement
        return await original_process(script_data, language_cfg, format_mode)

    dashboard_module._render_manual_run = wrapped_render_manual
    wrapped_process._visual_replacement_wrapped = True
    bot.process_visuals_async = wrapped_process
    bot._visual_replacement_bridge_installed = True
