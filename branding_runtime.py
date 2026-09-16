"""Free branded finishing layer for rendered Shorts."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from PIL import Image


ACCENT = "0x40C4FF"
ACCENT_SOFT = "0x40C4FF@0.72"
BRANDING_VERSION = "2026-09-16-v4"


def _assets(bot):
    root = Path(getattr(bot, "BASE_DIR", Path(__file__).resolve().parent))
    brand = root / "brand_assets"
    logo = brand / "logo.png.jpg"
    if not logo.exists():
        logo = brand / "channels4_profile.jpg"
    overlay = brand / "overlay.png"
    return logo, overlay


def _probe(path: str) -> tuple[int, int, float, int]:
    """Return width, height, duration and audio-stream count for a video."""
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=index,codec_type,width,height",
                "-show_entries", "format=duration",
                "-of", "json", path,
            ], capture_output=True, text=True, timeout=10, check=False,
        )
        data = json.loads(completed.stdout or "{}")
        streams = data.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio_count = sum(1 for item in streams if item.get("codec_type") == "audio")
        duration = float((data.get("format") or {}).get("duration") or 0.0)
        return (
            int(video.get("width") or 0),
            int(video.get("height") or 0),
            duration,
            audio_count,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError):
        return 0, 0, 0.0, 0


def _overlay_is_caption_safe(overlay_path: Path, width: int, height: int) -> tuple[bool, str]:
    """Reject full-frame brand overlays that could cover the central subtitle safe area."""
    try:
        with Image.open(overlay_path) as image:
            rgba = image.convert("RGBA")
            if rgba.size != (width, height):
                return False, f"overlay geometry {rgba.size[0]}x{rgba.size[1]} does not match video {width}x{height}"
            alpha = rgba.getchannel("A")
            alpha = alpha.point(lambda value: 255 if value >= 48 else 0)
            safe_x0 = int(width * 0.08)
            safe_x1 = int(width * 0.92)
            safe_y0 = max(0, height - 360)
            if alpha.crop((safe_x0, safe_y0, safe_x1, height)).getbbox():
                return False, "overlay has visible pixels in the central bottom caption-safe area"
            return True, "overlay geometry and caption-safe area passed"
    except (OSError, ValueError, TypeError):
        return False, "overlay could not be inspected"


def apply_branded_finish(bot, video_path: str) -> str:
    """Add restrained branding while preserving geometry, timing, audio and subtitle readability."""
    if not video_path or not os.path.isfile(video_path):
        return video_path
    logo, overlay = _assets(bot)
    if not logo.exists() and not overlay.exists():
        print("   [Branding] No logo/overlay asset found; leaving video unchanged.", flush=True)
        return video_path

    source_w, source_h, source_duration, source_audio_count = _probe(video_path)
    if source_w <= 0 or source_h <= 0 or source_duration <= 0:
        print("   [Branding] Source video metadata could not be verified; leaving video unchanged.", flush=True)
        return video_path

    use_overlay = False
    if overlay.exists():
        use_overlay, reason = _overlay_is_caption_safe(overlay, source_w, source_h)
        if not use_overlay:
            print(f"   [Branding] Unsafe overlay; trying logo fallback: {reason}", flush=True)
            if not logo.exists():
                print("   [Branding] No safe secondary brand asset available; leaving video unchanged.", flush=True)
                return video_path

    brand_asset = overlay if use_overlay else (logo if logo.exists() else None)
    output = str(Path(video_path).with_name(Path(video_path).stem + "_branded.mp4"))
    filters = [f"[0:v]drawbox=x=10:y=10:w=iw-20:h=ih-20:color={ACCENT_SOFT}:t=5[framed]"]
    last = "[framed]"

    if brand_asset is not None and use_overlay:
        filters.extend([
            "[1:v]format=rgba[brand_overlay]",
            "[framed][brand_overlay]overlay=0:0:eof_action=repeat:shortest=0:format=auto[finalv]",
        ])
        last = "[finalv]"
    elif brand_asset is not None:
        filters.extend([
            "[1:v]scale=132:-1,format=rgba[logo]",
            "color=c=0x08111d@0.72:s=160x160,format=rgba[logo_card]",
            "[logo_card][logo]overlay=(W-w)/2:(H-h)/2[logo_ready]",
            f"[logo_ready]drawbox=x=1:y=1:w=158:h=158:color={ACCENT}:t=3[logo_card_final]",
            f"{last}[logo_card_final]overlay=W-w-28:28:eof_action=repeat:shortest=0:format=auto[finalv]",
        ])
        last = "[finalv]"

    cmd = ["ffmpeg", "-y", "-i", video_path]
    if brand_asset is not None:
        cmd += ["-loop", "1", "-i", str(brand_asset)]
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", last, "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-map_metadata", "0", "-movflags", "+faststart",
        "-t", f"{source_duration:.3f}", output,
    ]

    try:
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
        if completed.returncode != 0 or not os.path.isfile(output):
            print(f"   [Branding] FFmpeg finish failed: {completed.stderr[-800:]}", flush=True)
            return video_path
        out_w, out_h, out_duration, out_audio_count = _probe(output)
        if (out_w, out_h) != (source_w, source_h):
            print(f"   [Branding] QC failed: geometry changed {source_w}x{source_h} -> {out_w}x{out_h}.", flush=True)
            os.remove(output)
            return video_path
        if abs(out_duration - source_duration) > 0.15:
            print(f"   [Branding] QC failed: duration changed {source_duration:.2f}s -> {out_duration:.2f}s.", flush=True)
            os.remove(output)
            return video_path
        if out_audio_count != source_audio_count:
            print(f"   [Branding] QC failed: audio stream count changed {source_audio_count} -> {out_audio_count}.", flush=True)
            os.remove(output)
            return video_path
        os.replace(output, video_path)
        mode = "overlay" if use_overlay else ("logo" if logo.exists() else "border-only")
        print(
            f"   [Branding] Finishing applied: border + {mode}; "
            f"{source_w}x{source_h}, {source_duration:.2f}s, audio_streams={source_audio_count}, version={BRANDING_VERSION}.",
            flush=True,
        )
        return video_path
    except Exception as exc:
        print(f"   [Branding] Finish failed: {type(exc).__name__}: {exc}", flush=True)
        try:
            if os.path.isfile(output):
                os.remove(output)
        except OSError:
            pass
        return video_path


def patch_branding_pipeline(bot):
    if getattr(bot, "_branding_pipeline_patch_installed", False):
        return bot
    run_robot = getattr(bot, "run_robot", None)
    if run_robot is None or not hasattr(run_robot, "__globals__"):
        return bot
    globals_dict = run_robot.__globals__
    original_compile = globals_dict.get("compile_video")
    if not callable(original_compile) or getattr(original_compile, "_branding_wrapped", False):
        bot._branding_pipeline_patch_installed = True
        return bot

    def branded_compile(*args, **kwargs):
        result = original_compile(*args, **kwargs)
        return apply_branded_finish(bot, result) if isinstance(result, str) else result

    branded_compile._branding_wrapped = True
    globals_dict["compile_video"] = branded_compile
    bot.compile_video = branded_compile
    bot._branding_pipeline_patch_installed = True
    print("   [Branding Patch] Branded finishing layer installed with output QC.", flush=True)
    return bot
