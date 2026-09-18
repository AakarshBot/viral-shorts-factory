"""Cheap, deterministic quality gates for fetched visuals.

This module deliberately does not make API calls. It rejects images that are too
small, badly shaped, or impractical to crop into a 9:16 Short. It also turns an
uncertain semantic QA result into a hard rejection so an unverified image can
never become the fallback winner.

Verified visual caching is intentionally entity-scoped: once an image has been
verified as representing an entity, narration/context must not force another
search for that same entity.
"""
from __future__ import annotations

import functools
import io
import os
from PIL import Image, ImageFilter, ImageStat

MIN_SHORT_SIDE = 720
PREFERRED_SHORT_SIDE = 1080
MAX_SOURCE_ASPECT = 3.2
MIN_SOURCE_ASPECT = 0.32
MAX_CROP_LOSS = 0.72


def inspect_image(img_bytes: bytes) -> dict:
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        short = min(w, h)
        aspect = w / max(1, h)
        scale = max(1080 / max(1, w), 1920 / max(1, h))
        crop_w = 1080 / scale
        crop_h = 1920 / scale
        kept_area = min(1.0, (crop_w * crop_h) / max(1.0, w * h))
        crop_loss = 1.0 - kept_area
        gray = img.resize((min(256, w), min(256, h))).convert("L")
        edge = gray.filter(ImageFilter.FIND_EDGES)
        sharpness = float(ImageStat.Stat(edge).var)
        return {"width": w, "height": h, "short_side": short, "long_side": max(w, h), "aspect": aspect, "crop_loss": crop_loss, "sharpness": sharpness, "valid": True}
    except Exception:
        return {"valid": False}


def quality_gate(img_bytes: bytes) -> tuple[bool, str, float]:
    info = inspect_image(img_bytes)
    if not info.get("valid"):
        return False, "invalid-image", 0.0
    short = info["short_side"]
    aspect = info["aspect"]
    crop_loss = info["crop_loss"]
    if short < MIN_SHORT_SIDE:
        return False, f"resolution-too-low:{info['width']}x{info['height']}", 0.0
    if not (MIN_SOURCE_ASPECT <= aspect <= MAX_SOURCE_ASPECT):
        return False, f"extreme-aspect:{aspect:.2f}", 0.0
    if crop_loss > MAX_CROP_LOSS:
        return False, f"bad-9x16-crop:{crop_loss:.0%}-loss", 0.0
    score = min(35.0, 35.0 * short / PREFERRED_SHORT_SIDE)
    score += max(0.0, 35.0 * (1.0 - crop_loss / MAX_CROP_LOSS))
    score += min(20.0, info["sharpness"] / 18.0)
    score += 10.0 if short >= PREFERRED_SHORT_SIDE else 0.0
    return True, "quality-ok", round(min(100.0, score), 1)


def _estimate_crop_focus(img: Image.Image) -> tuple[float, float, float]:
    """Find a cheap visual focal point using only PIL edge information."""
    try:
        gray = img.convert("L")
        max_side = 256
        scale = min(1.0, max_side / max(gray.width, gray.height))
        probe = gray.resize(
            (
                max(32, int(round(gray.width * scale))),
                max(32, int(round(gray.height * scale))),
            ),
            Image.Resampling.BILINEAR,
        )
        edges = probe.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=1.2))
        pixels = list(edges.getdata())
        if not pixels:
            return 0.5, 0.5, 0.0

        ranked = sorted(pixels)
        threshold = ranked[int(len(ranked) * 0.72)]
        weights = [float(value) if value >= threshold else 0.0 for value in pixels]
        total = sum(weights)
        if total <= 1.0:
            return 0.5, 0.5, 0.0

        width, height = edges.size
        weighted_x = 0.0
        weighted_y = 0.0
        index = 0
        for y in range(height):
            for x in range(width):
                weight = weights[index]
                index += 1
                weighted_x += x * weight
                weighted_y += y * weight

        return (
            weighted_x / total / max(1, width - 1),
            weighted_y / total / max(1, height - 1),
            min(1.0, total / max(1.0, width * height * 35.0)),
        )
    except Exception:
        return 0.5, 0.5, 0.0


def cover_crop(img: Image.Image, size=(1080, 1920)) -> Image.Image:
    """Scale-to-cover without blindly assuming the subject is centred."""
    target_w, target_h = size
    base = img.convert("RGB")
    source_aspect = base.width / max(1, base.height)
    target_aspect = target_w / max(1, target_h)

    scale = max(target_w / base.width, target_h / base.height)
    new_w = max(target_w, int(round(base.width * scale)))
    new_h = max(target_h, int(round(base.height * scale)))
    resized = base.resize((new_w, new_h), Image.Resampling.LANCZOS)

    crop_is_heavy = (
        source_aspect / max(target_aspect, 1e-6) > 1.25
        or source_aspect < target_aspect / 1.25
    )
    focal_x, focal_y = 0.5, 0.5
    if crop_is_heavy:
        focal_x, focal_y, confidence = _estimate_crop_focus(base)
        if confidence < 0.05:
            focal_x, focal_y = 0.5, 0.5
        elif abs(focal_x - 0.5) <= 0.04 and abs(focal_y - 0.5) <= 0.04:
            focal_x, focal_y = 0.5, 0.5
        else:
            print(
                f"   [Visual Framing] FOCAL-CROP | focus=({focal_x:.2f},{focal_y:.2f}) "
                f"confidence={confidence:.2f}",
                flush=True,
            )

    max_left = max(0, new_w - target_w)
    max_top = max(0, new_h - target_h)
    desired_left = int(round(focal_x * new_w - target_w / 2.0))
    desired_top = int(round(focal_y * new_h - target_h / 2.0))
    left = max(0, min(max_left, desired_left))
    top = max(0, min(max_top, desired_top))
    return resized.crop((left, top, left + target_w, top + target_h))

def install(visual_runtime_module):
    """Install quality gates plus entity-scoped verified visual caching."""
    if visual_runtime_module is None:
        return False
    original_gate = getattr(visual_runtime_module, "_strict_gate", None)
    original_cache = getattr(visual_runtime_module, "get_cached_asset", None)
    original_save = getattr(visual_runtime_module, "save_to_cache", None)
    if not callable(original_gate):
        return False
    if getattr(visual_runtime_module, "_quality_gate_installed", False):
        return True

    @functools.wraps(original_gate)
    def gated_strict_gate(bot, img_bytes, seg, video_title="", source=""):
        ok, reason, quality_score = quality_gate(img_bytes)
        if not ok:
            print(f"   [Visual Quality] REJECTED | {reason}", flush=True)
            return False, "LOCAL-QUALITY", 0, True
        accepted, tier, semantic_score, hard_reject = original_gate(bot, img_bytes, seg, video_title, source=source)
        if not accepted and not hard_reject:
            print(f"   [Visual Quality] REJECTED | semantic verification uncertain | quality={quality_score} | source={source}", flush=True)
            return False, tier, 0, True
        if accepted:
            combined = round((float(semantic_score) * 0.75) + (quality_score * 0.25), 1)
            print(f"   [Visual Quality] PASS | resolution/crop score={quality_score} | combined={combined}", flush=True)
            return True, tier, combined, False
        return accepted, tier, semantic_score, hard_reject

    visual_runtime_module._strict_gate = gated_strict_gate

    if callable(original_cache):
        @functools.wraps(original_cache)
        def gated_cached_asset(bot, entity, visual_type, context=""):
            # First use the new entity-scoped cache. This is the important path:
            # narration, intent and video title must not create separate caches.
            image, path = original_cache(bot, entity, visual_type, "")
            if image is None and context:
                # Read older context-specific caches for backward compatibility.
                image, path = original_cache(bot, entity, visual_type, context)
            if image is None:
                return None, None
            try:
                buf = io.BytesIO(); image.save(buf, format="JPEG", quality=95)
                ok, reason, _ = quality_gate(buf.getvalue())
                if not ok:
                    print(f"   [Visual Cache] REJECTED stale/low-quality cache | {reason}", flush=True)
                    return None, None
            except Exception:
                return None, None
            print(f"   [Visual Cache] ENTITY HIT | entity='{entity}' type={visual_type} | context ignored", flush=True)
            return image, path
        visual_runtime_module.get_cached_asset = gated_cached_asset

    if callable(original_save):
        @functools.wraps(original_save)
        def entity_scoped_save(bot, img_bytes, entity, visual_type, source_type, context=""):
            # Always save newly verified assets under context="" so every later
            # scene mentioning the same entity can reuse the verification.
            return original_save(bot, img_bytes, entity, visual_type, source_type, "")
        visual_runtime_module.save_to_cache = entity_scoped_save

    visual_runtime_module._quality_gate_installed = True
    visual_runtime_module._visual_quality_gate_version = "2026-09-17-v4-entity-cache"
    return True
