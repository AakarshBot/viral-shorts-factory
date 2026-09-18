"""Free branded finishing layer for rendered Shorts."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from PIL import Image


ACCENT = "0x40C4FF"
ACCENT_SOFT = "0x40C4FF@0.72"
HIGHLIGHT = "white@0.20"
BRANDING_VERSION = "2026-09-16-v6"


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


def _artifact_qc(
    path: str,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_duration: float | None = None,
    expected_audio_count: int | None = None,
) -> tuple[bool, str]:
    """Validate the actual rendered MP4 before it is allowed out of the factory."""
    if not path or not os.path.isfile(path):
        return False, "rendered video file is missing"
    try:
        if os.path.getsize(path) < 10_000:
            return False, "rendered video file is unexpectedly small"
    except OSError:
        return False, "rendered video file size could not be checked"

    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=index,codec_type,width,height,codec_name",
                "-show_entries", "format=duration,format_name",
                "-of", "json", path,
            ], capture_output=True, text=True, timeout=10, check=False,
        )
        if completed.returncode != 0:
            return False, "ffprobe could not read the rendered video"
        data = json.loads(completed.stdout or "{}")
        streams = data.get("streams") or []
        video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
        audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if len(video_streams) != 1:
            return False, f"expected exactly one video stream, found {len(video_streams)}"
        if not audio_streams:
            return False, "final rendered video has no audio stream"

        video = video_streams[0]
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
        duration = float((data.get("format") or {}).get("duration") or 0.0)
        format_name = str((data.get("format") or {}).get("format_name") or "")
        if width <= 0 or height <= 0 or duration <= 0:
            return False, "rendered video has invalid dimensions or duration"
        if abs((width / height) - (9 / 16)) > 0.015:
            return False, f"rendered video is not Shorts-shaped: {width}x{height}"
        if "mp4" not in format_name.lower() and not str(path).lower().endswith(".mp4"):
            return False, "rendered video is not an MP4 container"
        if expected_width is not None and expected_height is not None and (width, height) != (expected_width, expected_height):
            return False, f"rendered geometry changed {expected_width}x{expected_height} -> {width}x{height}"
        if expected_duration is not None and abs(duration - expected_duration) > 0.15:
            return False, f"rendered duration changed {expected_duration:.2f}s -> {duration:.2f}s"
        if expected_audio_count is not None and len(audio_streams) != expected_audio_count:
            return False, f"audio stream count changed {expected_audio_count} -> {len(audio_streams)}"
        return True, f"artifact passed: {width}x{height}, {duration:.2f}s, audio_streams={len(audio_streams)}"
    except (OSError, ValueError, TypeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        return False, f"artifact QC exception: {type(exc).__name__}: {exc}"


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
    """Add restrained branding while enforcing final rendered-artifact QC."""
    if not video_path or not os.path.isfile(video_path):
        return video_path

    source_w, source_h, source_duration, source_audio_count = _probe(video_path)
    valid, reason = _artifact_qc(
        video_path,
        expected_width=source_w or None,
        expected_height=source_h or None,
        expected_duration=source_duration or None,
        expected_audio_count=source_audio_count or None,
    )
    if not valid:
        raise RuntimeError(f"Final render QC failed before branding: {reason}")

    logo, _legacy_overlay = _assets(bot)
    # overlay.png is a legacy composite layer that can contain channel-name text
    # and an obsolete subtitle-safe box. The final finish owns only the border + logo.
    overlay = None
    if not logo.exists():
        print(f"   [Branding] No logo/overlay asset found; final artifact QC passed: {reason}", flush=True)
        return video_path

    if source_w <= 0 or source_h <= 0 or source_duration <= 0:
        raise RuntimeError("Final render QC failed: source video metadata could not be verified")

    use_overlay = False
    brand_asset = logo if logo.exists() else None
    output = str(Path(video_path).with_name(Path(video_path).stem + "_branded.mp4"))
    filters = [
        f"[0:v]drawbox=x=10:y=10:w=iw-20:h=ih-20:color={ACCENT_SOFT}:t=5[frame_outer]",
        f"[frame_outer]drawbox=x=16:y=16:w=iw-32:h=ih-32:color={HIGHLIGHT}:t=2[framed]",
    ]
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
            "color=c=0xFFFFFF@0.82:s=166x166,format=rgba[logo_card]",
            "color=c=0xFFFFFF@0.22:s=174x174,format=rgba[logo_gloss]",
            "[logo_card][logo]overlay=(W-w)/2:(H-h)/2[logo_ready]",
            f"[logo_ready]drawbox=x=1:y=1:w=164:h=164:color={ACCENT}:t=3[logo_card_final]",
            "[logo_gloss]crop=174:28:0:0[logo_highlight]",
            "[logo_card_final][logo_highlight]overlay=(W-w)/2:0[logo_gloss_ready]",
            f"{last}[logo_gloss_ready]overlay=W-w-24:24:eof_action=repeat:shortest=0:format=auto[finalv]",
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
            raise RuntimeError(f"FFmpeg branding finish failed: {completed.stderr[-800:]}")

        out_w, out_h, out_duration, out_audio_count = _probe(output)
        valid, reason = _artifact_qc(
            output,
            expected_width=source_w,
            expected_height=source_h,
            expected_duration=source_duration,
            expected_audio_count=source_audio_count,
        )
        if not valid:
            os.remove(output)
            raise RuntimeError(f"Final render QC failed after branding: {reason}")
        if (out_w, out_h) != (source_w, source_h) or abs(out_duration - source_duration) > 0.15 or out_audio_count != source_audio_count:
            os.remove(output)
            raise RuntimeError("Final render QC failed after branding: geometry, duration or audio changed")

        os.replace(output, video_path)
        mode = "overlay" if use_overlay else ("logo" if logo.exists() else "border-only")
        print(
            f"   [Branding] Finishing applied: dual-gloss frame + {mode}; "
            f"{source_w}x{source_h}, {source_duration:.2f}s, audio_streams={source_audio_count}, version={BRANDING_VERSION}.",
            flush=True,
        )
        return video_path
    except Exception as exc:
        try:
            if os.path.isfile(output):
                os.remove(output)
        except OSError:
            pass
        print(f"   [Branding] Finish failed: {type(exc).__name__}: {exc}", flush=True)
        raise


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
    print("   [Branding Patch] Branded finishing layer installed with final artifact QC.", flush=True)
    return bot
