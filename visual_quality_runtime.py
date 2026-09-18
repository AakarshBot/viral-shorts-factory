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
        sharpness = float(ImageStat.Stat(edge).var[0])
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


def _detect_face_focus(img: Image.Image) -> tuple[float, float, float] | None:
    """Return a face-weighted focal point when OpenCV can detect faces."""
    try:
        import cv2
        import numpy as np

        base = img.convert("RGB")
        max_side = 1000
        scale = min(1.0, max_side / max(base.width, base.height))
        probe = base.resize(
            (max(1, int(round(base.width * scale))), max(1, int(round(base.height * scale)))),
            Image.Resampling.LANCZOS,
        )
        gray = cv2.cvtColor(np.asarray(probe), cv2.COLOR_RGB2GRAY)
        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        cascade = cv2.CascadeClassifier(cascade_path)
        if cascade.empty():
            return None
        faces = cascade.detectMultiScale(
            gray,
            scaleFactor=1.08,
            minNeighbors=4,
            minSize=(max(24, int(40 * scale)), max(24, int(40 * scale))),
        )
        if len(faces) == 0:
            return None

        total_weight = 0.0
        cx = cy = 0.0
        for x, y, w, h in faces:
            weight = max(1.0, float(w * h))
            cx += (x + w / 2.0) * weight
            cy += (y + h / 2.0) * weight
            total_weight += weight

        if total_weight <= 0:
            return None
        return (
            cx / total_weight / max(1.0, probe.width),
            cy / total_weight / max(1.0, probe.height),
            min(1.0, total_weight / max(1.0, probe.width * probe.height) * 35.0),
        )
    except Exception:
        return None


def _saliency_focus(img: Image.Image) -> tuple[float, float, float]:
    """Find a cheap edge/saliency focal point without an API or ML model."""
    try:
        base = img.convert("L")
        max_side = 256
        scale = min(1.0, max_side / max(base.width, base.height))
        probe = base.resize(
            (max(32, int(round(base.width * scale))), max(32, int(round(base.height * scale)))),
            Image.Resampling.BILINEAR,
        )
        edges = probe.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(radius=1.2))
        px = list(edges.getdata())
        if not px:
            return 0.5, 0.5, 0.0

        total = float(sum(px))
        if total <= 1.0:
            return 0.5, 0.5, 0.0

        weighted_x = 0.0
        weighted_y = 0.0
        width, height = edges.size
        index = 0
        max_value = max(px) or 1
        for y in range(height):
            for x in range(width):
                value = float(px[index])
                index += 1
                # Mildly suppress isolated extremes so a single bright pixel does
                # not yank the entire crop away from the main composition.
                weight = min(value, max_value * 0.85)
                weighted_x += x * weight
                weighted_y += y * weight

        return (
            weighted_x / max(total, 1.0) / max(1, width - 1),
            weighted_y / max(total, 1.0) / max(1, height - 1),
            min(1.0, total / max(1.0, width * height * 18.0)),
        )
    except Exception:
        return 0.5, 0.5, 0.0


def _fit_with_blurred_background(img: Image.Image, size=(1080, 1920)) -> Image.Image:
    """Preserve the full source when a confident vertical crop is unavailable."""
    target_w, target_h = size
    base = img.convert("RGB")

    background = base.resize((target_w, target_h), Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(radius=24))

    scale = min(target_w / max(1, base.width), target_h / max(1, base.height))
    fit_w = max(1, int(round(base.width * scale)))
    fit_h = max(1, int(round(base.height * scale)))
    foreground = base.resize((fit_w, fit_h), Image.Resampling.LANCZOS)

    left = max(0, (target_w - fit_w) // 2)
    top = max(0, (target_h - fit_h) // 2)
    background.paste(foreground, (left, top))
    return background


def _crop_with_focal_point(img: Image.Image, size=(1080, 1920), focal_x=0.5, focal_y=0.5):
    target_w, target_h = size
    base = img.convert("RGB")
    scale = max(target_w / base.width, target_h / base.height)
    new_w = max(target_w, int(round(base.width * scale)))
    new_h = max(target_h, int(round(base.height * scale)))
    resized = base.resize((new_w, new_h), Image.Resampling.LANCZOS)

    max_left = max(0, new_w - target_w)
    max_top = max(0, new_h - target_h)
    desired_left = int(round(focal_x * new_w - target_w / 2.0))
    desired_top = int(round(focal_y * new_h - target_h / 2.0))
    left = max(0, min(max_left, desired_left))
    top = max(0, min(max_top, desired_top))
    return resized.crop((left, top, left + target_w, top + target_h))


def cover_crop(
    img: Image.Image,
    size=(1080, 1920),
    visual_genre: str = "",
    visual_type: str = "",
) -> Image.Image:
    """Smart 9:16 crop that tries to keep people/subjects inside the frame."""
    target_w, target_h = size
    base = img.convert("RGB")

    # Near-vertical sources need little or no horizontal intervention.
    source_aspect = base.width / max(1, base.height)
    target_aspect = target_w / max(1, target_h)
    person_like = {
        "PERSON_PORTRAIT",
        "PERSON_ACTION",
        "TEAM_ACTION",
        "TEAM_BRANDING",
        "SPORTS_ACTION",
    }
    face_focus = _detect_face_focus(base) if (
        str(visual_genre or "").upper() in person_like
        or str(visual_type or "").upper() == "PERSON"
    ) else None

    if face_focus is not None and face_focus[2] >= 0.01:
        print(
            f"   [Visual Framing] FACE-ANCHORED | genre={visual_genre or visual_type} "
            f"focus=({face_focus[0]:.2f},{face_focus[1]:.2f})",
            flush=True,
        )
        return _crop_with_focal_point(base, size, face_focus[0], face_focus[1])

    # For strongly cropped/wide images, use cheap local saliency instead of
    # assuming the subject is in the exact centre.
    strongly_cropped = (
        source_aspect / max(target_aspect, 1e-6) > 1.25
        or source_aspect < target_aspect / 1.25
    )
    if strongly_cropped:
        saliency_x, saliency_y, confidence = _saliency_focus(base)
        if confidence >= 0.12 and (
            abs(saliency_x - 0.5) > 0.05 or abs(saliency_y - 0.5) > 0.05
        ):
            print(
                f"   [Visual Framing] SALIENCY-CROP | genre={visual_genre or visual_type} "
                f"focus=({saliency_x:.2f},{saliency_y:.2f}) confidence={confidence:.2f}",
                flush=True,
            )
            return _crop_with_focal_point(base, size, saliency_x, saliency_y)

        # A wide source with no reliable focal point is safer as a full-frame
        # visual than as a blind centre crop that can remove the actual subject.
        print(
            f"   [Visual Framing] FULL-FRAME-FIT | genre={visual_genre or visual_type} "
            f"reason=low-confidence-focus",
            flush=True,
        )
        return _fit_with_blurred_background(base, size)

    return _crop_with_focal_point(base, size, 0.5, 0.5)


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
