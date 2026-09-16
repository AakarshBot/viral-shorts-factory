"""Free branded finishing layer for rendered Shorts."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


ACCENT = "0x40C4FF"
ACCENT_SOFT = "0x40C4FF@0.72"


def _assets(bot):
    root = Path(getattr(bot, "BASE_DIR", Path(__file__).resolve().parent))
    brand = root / "brand_assets"
    logo = brand / "logo.png.jpg"
    if not logo.exists():
        logo = brand / "channels4_profile.jpg"
    overlay = brand / "overlay.png"
    return logo, overlay


def apply_branded_finish(bot, video_path: str) -> str:
    """Add a restrained glossy border and top-right logo to the completed MP4."""
    if not video_path or not os.path.isfile(video_path):
        return video_path

    logo, overlay = _assets(bot)
    if not logo.exists() and not overlay.exists():
        print("   [Branding] No logo/overlay asset found; leaving video unchanged.", flush=True)
        return video_path

    output = str(Path(video_path).with_name(Path(video_path).stem + "_branded.mp4"))
    filters = []

    # A light frame is intentionally used as brand identity, not as a way of
    # changing or hiding platform review signals.
    base = "[0:v]drawbox=x=10:y=10:w=iw-20:h=ih-20:color=%s:t=5[framed]" % ACCENT_SOFT
    filters.append(base)

    last = "[framed]"
    if overlay.exists():
        # Existing transparent overlay takes precedence if the user has one.
        filters.append("[1:v]format=rgba[brand_overlay]")
        filters.append("[framed][brand_overlay]overlay=0:0:format=auto[with_overlay]")
        last = "[with_overlay]"
    elif logo.exists():
        filters.append("[1:v]scale=132:-1,format=rgba[logo]")
        filters.append(
            "color=c=0x08111d@0.72:s=160x160:d=1,format=rgba[logo_card]"
        )
        filters.append(
            "[logo_card][logo]overlay=(W-w)/2:(H-h)/2[logo_ready]"
        )
        filters.append(
            "[logo_ready]drawbox=x=1:y=1:w=158:h=158:color=%s:t=3[logo_card_final]" % ACCENT
        )
        filters.append(
            f"{last}[logo_card_final]overlay=W-w-28:28:format=auto[finalv]"
        )
        last = "[finalv]"

    filter_complex = ";".join(filters)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
    ]
    if overlay.exists() or logo.exists():
        cmd += ["-i", str(overlay if overlay.exists() else logo)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", last,
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output,
    ]

    try:
        completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=240)
        if completed.returncode != 0 or not os.path.isfile(output):
            print(f"   [Branding] FFmpeg finish failed: {completed.stderr[-800:]}", flush=True)
            return video_path
        os.replace(output, video_path)
        print("   [Branding] Glossy border + top-right logo applied.", flush=True)
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
        if isinstance(result, str):
            return apply_branded_finish(bot, result)
        return result

    branded_compile._branding_wrapped = True
    globals_dict["compile_video"] = branded_compile
    bot.compile_video = branded_compile
    bot._branding_pipeline_patch_installed = True
    print("   [Branding Patch] Branded finishing layer installed.", flush=True)
    return bot
