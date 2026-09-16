"""Production-safe voice generation and word timing for Viral Shorts Factory.

A scene that cannot produce real speech fails instead of being replaced with
filler narration. Edge-TTS WordBoundary metadata is normalised and stored on
the script so subtitle/render layers can use the same timing source.
"""
from __future__ import annotations

import asyncio
import gc
import os
import re
import subprocess
import tempfile
from typing import Any

_AUDIO_MARKUP_RE = re.compile(r"[*_#`\[\]()~^\"“”‘’]")


def clean_audio_text(value: Any) -> str:
    """Return only actual narration text; never invent replacement speech."""
    text = _AUDIO_MARKUP_RE.sub("", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def normalise_word_timings(timings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate, sort and de-duplicate Edge-TTS word timings."""
    cleaned: list[dict[str, Any]] = []
    last_start = -1.0
    for item in timings or []:
        try:
            word = str(item.get("word", "")).strip()
            start = max(0.0, float(item.get("start", 0.0)))
            end = max(start, float(item.get("end", start)))
        except (AttributeError, TypeError, ValueError):
            continue
        if not word or start < last_start:
            continue
        if cleaned and start == cleaned[-1]["start"] and word == cleaned[-1]["word"]:
            continue
        cleaned.append({"word": word, "start": start, "end": end})
        last_start = start
    return cleaned


def validate_audio_timing(text: str, timings: list[dict[str, Any]]) -> tuple[bool, str]:
    """Ensure a real narration scene has usable word-level timing data."""
    clean_text = clean_audio_text(text)
    if not clean_text:
        return False, "Narration text is empty."
    expected = len(re.findall(r"\S+", clean_text))
    observed = len(timings)
    if not timings:
        return False, "Edge-TTS returned no word-boundary timings."
    if observed < max(1, int(expected * 0.55)):
        return False, f"Word-boundary timing coverage is too low ({observed}/{expected})."
    previous_end = -1.0
    for item in timings:
        if item["start"] < previous_end - 0.05:
            return False, "Word timings are not monotonic."
        if item["end"] < item["start"]:
            return False, "Word timing contains a negative duration."
        previous_end = item["end"]
    return True, "Valid word-level audio timing"


def validate_timing_against_duration(timings: list[dict[str, Any]], duration: float, tolerance: float = 0.35) -> tuple[bool, str]:
    """Ensure the word-boundary timeline fits inside the encoded media duration."""
    if not timings:
        return False, "No word timings are available for duration alignment."
    if duration <= 0:
        return False, "Encoded audio duration is not positive."
    first_start = max(0.0, float(timings[0]["start"]))
    last_end = max(0.0, float(timings[-1]["end"]))
    if first_start > duration + tolerance:
        return False, f"First word timing begins after encoded audio ends ({first_start:.2f}s > {duration:.2f}s)."
    if last_end > duration + tolerance:
        return False, f"Word timing extends beyond encoded audio ({last_end:.2f}s > {duration:.2f}s)."
    return True, "Word timings fit encoded audio duration"


def get_audio_duration(path: str) -> float:
    """Read the real encoded media duration without importing another Python dependency."""
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        value = float((completed.stdout or "").strip())
        return value if value > 0 else 0.0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


async def _render_scene(bot, text, voice, rate, pitch, final_path, timeout_seconds=45):
    timings = []

    async def consume():
        try:
            communicate = bot.edge_tts.Communicate(
                text, voice, rate=rate, pitch=pitch, boundary="WordBoundary"
            )
        except TypeError:
            communicate = bot.edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)

        fd, temp_path = tempfile.mkstemp(prefix="voiceover_", suffix=".mp3", dir=bot.ASSETS_DIR)
        os.close(fd)
        try:
            with open(temp_path, "wb") as f:
                async for chunk in communicate.stream():
                    chunk_type = chunk.get("type")
                    if chunk_type == "audio":
                        f.write(chunk["data"])
                    elif chunk_type == "WordBoundary":
                        word = str(chunk.get("text", "")).strip()
                        if word:
                            offset = max(0.0, float(chunk.get("offset", 0)) / 10000000.0)
                            duration = max(0.0, float(chunk.get("duration", 0)) / 10000000.0)
                            timings.append({
                                "word": word,
                                "start": offset,
                                "end": max(offset, offset + duration),
                            })
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) <= 500:
                raise RuntimeError("Edge TTS returned an empty or invalid audio file.")
            timings[:] = normalise_word_timings(timings)
            valid, reason = validate_audio_timing(text, timings)
            if not valid:
                raise RuntimeError(reason)
            os.replace(temp_path, final_path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    await asyncio.wait_for(consume(), timeout=timeout_seconds)
    return timings


async def generate_voiceover_and_timestamps(bot, script_data, language_cfg):
    print("\n🎙️ Generating Audio & Mapping Word Timings [STRICT AUDIO PIPELINE]...", flush=True)

    audio_paths = []
    word_timings = []
    scene_durations = []
    persona_key = next(
        (key for key in bot.PERSONA_PROFILES.keys() if key in script_data.get("persona_used", "LISTICLE HOST").upper()),
        "LISTICLE HOST",
    )
    profile = bot.PERSONA_PROFILES[persona_key]
    script_data["voice_gender"] = profile["gender"]
    scenes = script_data.get("script", [])

    if not scenes:
        print("   [Audio] ERROR: script contains no scenes.", flush=True)
        return [], []

    for idx, seg in enumerate(scenes):
        path = os.path.join(bot.ASSETS_DIR, f"voiceover_{idx + 1}.mp3")
        text = clean_audio_text(seg.get("voiceover", ""))
        if not text:
            print(f"   [Audio] FATAL: Scene {idx + 1} has no narration text.", flush=True)
            return [], []

        success = False
        for attempt in range(1, 4):
            try:
                print(f"   [Audio] Scene {idx + 1}/{len(scenes)} attempt {attempt}...", flush=True)
                timings = await _render_scene(
                    bot, text, language_cfg["voices"][profile["gender"]], profile["rate"],
                    profile["pitch"], path, timeout_seconds=45,
                )
                actual_duration = get_audio_duration(path)
                if actual_duration <= 0:
                    raise RuntimeError("Unable to determine encoded audio duration.")
                aligned, alignment_reason = validate_timing_against_duration(timings, actual_duration)
                if not aligned:
                    raise RuntimeError(alignment_reason)
                timing_duration = (timings[-1]["end"] + 0.15) if timings else 0.0
                duration = max(actual_duration, timing_duration)
                audio_paths.append(path)
                word_timings.append(timings)
                scene_durations.append(duration)
                success = True
                print(
                    f"   [Audio] Scene {idx + 1}/{len(scenes)} complete: "
                    f"{os.path.getsize(path) / 1024:.1f} KB, {len(timings)} word timings, "
                    f"{duration:.2f}s audio duration, timing aligned.",
                    flush=True,
                )
                break
            except asyncio.TimeoutError:
                print(f"   [Audio] Scene {idx + 1} timed out after 45s on attempt {attempt}.", flush=True)
            except Exception as exc:
                print(f"   [Audio] Scene {idx + 1} failed on attempt {attempt}: {type(exc).__name__}: {exc}", flush=True)
            await asyncio.sleep(min(3 * attempt, 9))
            gc.collect()

        if not success:
            print(f"   [Audio] FATAL: Could not generate real narration for scene {idx + 1}.", flush=True)
            return [], []

    script_data["audio_scene_durations"] = scene_durations
    script_data["audio_total_duration"] = round(sum(scene_durations), 3)
    script_data["word_timing_version"] = 3
    script_data["word_timing_counts"] = [len(items) for items in word_timings]

    gc.collect()
    print(
        f"   [+] Audio generation complete: {len(audio_paths)}/{len(scenes)} scenes. "
        f"Actual audio duration: {script_data['audio_total_duration']:.2f}s. "
        "Transitioning to visual sourcing...",
        flush=True,
    )
    return audio_paths, word_timings


def patch_audio_pipeline(bot):
    """Patch both the module attribute and run_robot's actual global lookup."""
    if getattr(bot, "_audio_pipeline_patch_installed", False):
        return bot

    async def process(script_data, language_cfg):
        return await generate_voiceover_and_timestamps(bot, script_data, language_cfg)

    bot.generate_voiceover_and_timestamps = process
    bot._audio_pipeline_patch_installed = True

    run_robot = getattr(bot, "run_robot", None)
    if run_robot is not None and hasattr(run_robot, "__globals__"):
        run_robot.__globals__["generate_voiceover_and_timestamps"] = process

    print("   [Audio Patch] Strict Edge-TTS pipeline installed into run_robot globals.", flush=True)
    return bot
