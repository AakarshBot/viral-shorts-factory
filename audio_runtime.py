"""Production-safe voice generation for Viral Shorts Factory.

This patch deliberately updates run_robot()'s global namespace as well as the
module attribute. That makes the override unambiguous even when Streamlit has
cached the imported module/function object between reruns.
"""
import asyncio
import gc
import os
import re
import tempfile


async def _render_scene(bot, text, voice, rate, pitch, final_path, timeout_seconds=45):
    timings = []

    async def consume():
        try:
            communicate = bot.edge_tts.Communicate(
                text,
                voice,
                rate=rate,
                pitch=pitch,
                boundary="WordBoundary",
            )
        except TypeError:
            communicate = bot.edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)

        fd, temp_path = tempfile.mkstemp(
            prefix="voiceover_", suffix=".mp3", dir=bot.ASSETS_DIR
        )
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
    print("\n🎙️ Generating Audio & Mapping Karaoke Timestamps [SAFE AUDIO PATCH]...", flush=True)

    audio_paths = []
    word_timings = []
    persona_key = next(
        (
            key for key in bot.PERSONA_PROFILES.keys()
            if key in script_data.get("persona_used", "LISTICLE HOST").upper()
        ),
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
        text = re.sub(
            r'[*_#`\[\]()~^"“”‘’]',
            "",
            seg.get("voiceover", ""),
        ).strip() or f"Point number {idx + 1}."

        success = False
        for attempt in range(1, 4):
            try:
                print(
                    f"   [Audio] Scene {idx + 1}/{len(scenes)} attempt {attempt}...",
                    flush=True,
                )
                timings = await _render_scene(
                    bot,
                    text,
                    language_cfg["voices"][profile["gender"]],
                    profile["rate"],
                    profile["pitch"],
                    path,
                    timeout_seconds=45,
                )
                audio_paths.append(path)
                word_timings.append(timings)
                success = True
                print(
                    f"   [Audio] Scene {idx + 1}/{len(scenes)} complete: "
                    f"{os.path.getsize(path) / 1024:.1f} KB, {len(timings)} word timings.",
                    flush=True,
                )
                break
            except asyncio.TimeoutError:
                print(
                    f"   [Audio] Scene {idx + 1} timed out after 45s on attempt {attempt}.",
                    flush=True,
                )
            except Exception as exc:
                print(
                    f"   [Audio] Scene {idx + 1} failed on attempt {attempt}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            await asyncio.sleep(min(3 * attempt, 9))
            gc.collect()

        if not success:
            print(f"   [Audio] FATAL: Could not generate scene {idx + 1}.", flush=True)
            return [], []

    gc.collect()
    print(
        f"   [+] Audio generation complete: {len(audio_paths)}/{len(scenes)} scenes. "
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

    print(
        "   [Audio Patch] Safe Edge-TTS pipeline installed into run_robot globals.",
        flush=True,
    )
    return bot
