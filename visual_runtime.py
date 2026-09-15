"""Strict visual sourcing for Viral Shorts Factory.

Every scene must receive a real, relevant visual. Placeholder/gradient-only
slides are rejected. Gemini vision is used as the semantic relevance gate when
available; the factory fails the run rather than silently publishing a blank
or unrelated slide.
"""
import asyncio
import base64
import io
import os
import re
import threading

from PIL import Image, ImageDraw

VISUAL_FETCH_TIMEOUT_SECONDS = 15


def _tokens(text):
    return [t.lower() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}", str(text or ""))]


def _entity_context(seg, video_title=""):
    entity = str(seg.get("primary_entity", "")).strip()
    intent = str(seg.get("visual_intent", "")).strip()
    prompt = str(seg.get("specific_search_prompt", "")).strip()
    voice = str(seg.get("voiceover", "")).strip()
    return entity, intent, prompt, voice, str(video_title or "").strip()


def _strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key):
    """Ask Gemini whether the actual pixels match the requested scene."""
    if not api_key:
        print("   [Visual QA] Gemini semantic verifier is not configured (missing GEMINI_API_KEY).")
        return None
    try:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        instruction = (
            "You are a strict visual editor for a factual YouTube Short. "
            "Inspect the image itself. PASS only when the image clearly depicts "
            "the requested primary entity AND is appropriate to the requested visual intent. "
            "For a named person, PASS only if the person is plausibly identifiable as that person. "
            "Do not pass an image merely because it is generally related to the topic. "
            "Reject generic stock photos, unrelated people, wrong teams/products/events, "
            "generic concept art when a specific real entity was requested, memes, screenshots "
            "with misleading context, and images where the requested subject is absent. "
            "If uncertain, return FAIL. Return exactly PASS or FAIL.\n\n"
            f"Video topic: {video_title}\n"
            f"Primary entity: {entity}\n"
            f"Visual intent: {intent}\n"
            f"Search prompt: {prompt}\n"
            f"Scene narration: {voice}"
        )
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
        import requests
        response = requests.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [
                    {"text": instruction},
                    {"inlineData": {"mimeType": "image/jpeg", "data": b64}},
                ]}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 4},
            },
            timeout=12,
        )
        if response.status_code != 200:
            detail = response.text[:300].replace("\n", " ")
            print(f"   [Visual QA] Gemini verifier HTTP {response.status_code}: {detail}")
            return None
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip().upper()
        if text.startswith("PASS"):
            return True
        if text.startswith("FAIL"):
            return False
        print(f"   [Visual QA] Gemini returned an unexpected verdict: {text[:80]}")
    except Exception as exc:
        print(f"   [Visual QA] Semantic verifier unavailable: {exc}")
    return None


def _local_visual_sanity(img_bytes):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        if min(img.size) < 300:
            return False
        if img.width / max(1, img.height) > 2.5 or img.width / max(1, img.height) < 0.4:
            return False
        return True
    except Exception:
        return False


def _strict_gate(bot, img_bytes, seg, video_title=""):
    if not img_bytes or not _local_visual_sanity(img_bytes):
        return False
    entity, intent, prompt, voice, title = _entity_context(seg, video_title)
    if not entity or entity.lower() in {"none", "unknown", "n/a"}:
        print("   [Visual QA] REJECTED: scene has no specific primary_entity.")
        return False

    result = _strict_gemini_check(
        img_bytes, entity, intent, prompt, voice, title,
        os.getenv("GEMINI_API_KEY"),
    )
    if result is True:
        return True
    if result is False:
        print(f"   [Visual QA] REJECTED: image does not match '{entity}' / '{intent}'.")
        return False

    print("   [Visual QA] REJECTED: semantic relevance could not be verified.")
    return False


def _build_search_variants(seg, video_title=""):
    entity, intent, prompt, voice, title = _entity_context(seg, video_title)
    category = str(seg.get("sport_or_topic_category", "")).strip()
    variants = []
    for value in (
        prompt,
        f"{entity} {intent} {category}",
        f"{entity} {title}",
        f"{entity} {voice[:140]}",
    ):
        value = re.sub(r"\s+", " ", value).strip()
        if value and value not in variants:
            variants.append(value)
    return variants[:4]


def _call_fetcher_with_timeout(fetcher, args, source, query, timeout=VISUAL_FETCH_TIMEOUT_SECONDS):
    """Run legacy synchronous image providers without blocking the async factory forever."""
    result = {"value": None, "error": None}

    def worker():
        try:
            result["value"] = fetcher(*args)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=worker, name=f"visual-{source.lower()}-fetch", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        print(f"   [Visual Source] {source} | timed out after {timeout}s | query='{query}'")
        return None
    if result["error"] is not None:
        print(f"   [Visual Source] {source} | failed: {result['error']} | query='{query}'")
        return None
    return result["value"]


def _relevant_asset(bot, seg, category, used_urls, used_hashes, video_title=""):
    """Try several increasingly broad searches, but verify every candidate."""
    entity = str(seg.get("primary_entity", "")).strip()
    if not entity or entity.lower() in {"none", "unknown", "n/a"}:
        raise RuntimeError("Visual pipeline requires a specific primary_entity for every scene.")

    variants = _build_search_variants(seg, video_title)
    editorial = any(k in str(seg.get("visual_intent", "")).lower() for k in
                    ["editorial", "stadium", "news", "trophy", "event", "person"]) or any(
                        k in category for k in ["sport", "cricket", "football", "news", "politics", "entertainment", "movie"]
                    )

    def try_bytes(data, source, query):
        if not data:
            return None
        try:
            h = bot.get_image_hash(data)
            if h in used_hashes:
                return None
            if not _strict_gate(bot, data, seg, video_title):
                return None
            used_hashes.add(h)
            print(f"   [Visual Source] {source} | verified | query='{query}'")
            return Image.open(io.BytesIO(data)).convert("RGB"), False, source
        except Exception as exc:
            print(f"   [Visual Source] {source} | candidate rejected: {exc}")
            return None

    for query in variants:
        print(f"   [Visual Search] Scene entity='{entity}' | query='{query}'")
        if editorial:
            for fetcher, name, args in (
                (bot.fetch_wiki_person_image, "Wikipedia", (entity, used_urls, query, video_title)),
                (bot.fetch_wikimedia_commons, "Commons", (query, used_urls, query, video_title)),
            ):
                data = _call_fetcher_with_timeout(fetcher, args, name, query)
                result = try_bytes(data, name, query)
                if result:
                    return result

        for fetcher, name in (
            (bot.fetch_pexels, "Pexels"),
            (bot.fetch_unsplash, "Unsplash"),
            (bot.fetch_duckduckgo, "DDG"),
        ):
            data = _call_fetcher_with_timeout(fetcher, (query, used_urls, query, video_title), name, query)
            result = try_bytes(data, name, query)
            if result:
                return result

    ai_prompts = [
        f"Photorealistic editorial image of {entity}. {str(seg.get('visual_intent','')).strip()}. {str(seg.get('voiceover','')).strip()[:180]}",
        f"Photorealistic documentary photograph showing {entity} in context. {video_title}",
    ]
    for ai_prompt in ai_prompts:
        print(f"   [Visual Source] AI-generated attempt | prompt='{ai_prompt[:140]}'")
        ai = _call_fetcher_with_timeout(bot.fetch_hf_ai_image, (ai_prompt,), "HF-AI", ai_prompt)
        if ai is None:
            continue
        try:
            buf = io.BytesIO()
            ai.convert("RGB").save(buf, format="JPEG", quality=95)
            data = buf.getvalue()
            if _strict_gate(bot, data, seg, video_title):
                h = bot.get_image_hash(data)
                if h not in used_hashes:
                    used_hashes.add(h)
                    print("   [Visual Source] AI-generated | verified")
                    return ai.convert("RGB"), True, "AI-generated"
        except Exception as exc:
            print(f"   [Visual Source] AI-generated | candidate rejected: {exc}")

    raise RuntimeError(f"No strictly relevant visual could be verified for scene entity '{entity}'.")


def _render_image_slide(bot, bg_img, title_text, subtitle_text="", font_choice=None, accent=None):
    """Image-backed title/CTA slide; never uses a flat gradient as the visual."""
    bg = bg_img.convert("RGBA").resize((1080, 1920), Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    accent = accent or bot.PALETTE.get("accent_primary", (0, 191, 255))
    draw.rectangle([0, 0, 1080, 42], fill=accent + (230,))
    draw.rounded_rectangle([55, 420, 1025, 1500], radius=40, fill=(5, 8, 16, 205), outline=accent + (210,), width=3)
    font, lines = bot.fit_text_in_box(title_text, font_choice, 860, 650, start_size=78)
    y = 620
    for line in lines:
        bb = draw.textbbox((0, 0), line, font=font)
        x = (1080 - (bb[2] - bb[0])) / 2
        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 230))
        y += (bb[3] - bb[1]) + 18
    if subtitle_text:
        sub_font = bot.get_bold_font(52, font_choice)
        bb = draw.textbbox((0, 0), subtitle_text, font=sub_font)
        x = (1080 - (bb[2] - bb[0])) / 2
        y = 1280
        draw.rounded_rectangle([x - 30, y - 18, x + bb[2] - bb[0] + 30, y + bb[3] - bb[1] + 25], radius=30, fill=(0, 0, 0, 220), outline=accent + (220,), width=2)
        draw.text((x, y), subtitle_text, font=sub_font, fill=accent)
    return Image.alpha_composite(bg, overlay)


async def _process_visuals(bot, script_data, language_cfg, format_mode="regular"):
    print("\n🎨 Sourcing strictly relevant visuals for every Shorts slide...")
    width, height = 1080, 1920
    target_size = (width, height)
    scenes = script_data.get("script", [])
    if not scenes:
        raise RuntimeError("Visual pipeline received an empty script.")

    font_choice = language_cfg.get("font")
    packages = [None] * len(scenes)
    used_urls = set()
    used_hashes = set()
    ai_count = 0

    for idx, seg in enumerate(scenes):
        video_title = script_data.get("title", "") or script_data.get("titles", [""])[0]
        print(f"   [Visual Pipeline] Scene {idx + 1}/{len(scenes)} starting...")
        category = str(seg.get("sport_or_topic_category", "")).lower()
        bg_img, used_ai, source_type = _relevant_asset(bot, seg, category, used_urls, used_hashes, video_title)
        ai_count += int(used_ai)
        bg_img = bg_img.resize(target_size, Image.Resampling.LANCZOS).convert("RGBA")
        img_path = os.path.join(bot.ASSETS_DIR, f"scene_{idx+1}_img.jpg")

        if format_mode == "top5":
            if idx == 0:
                rendered = _render_image_slide(bot, bg_img, video_title or seg.get("voiceover", "Top 5"), "TODAY'S SPECIAL", font_choice)
            elif idx == len(scenes) - 1:
                rendered = _render_image_slide(bot, bg_img, seg.get("voiceover", "What do you think?"), "SUBSCRIBE!", font_choice)
            else:
                clean = re.sub(r"(number\s*\d+|story\s*#?\d+|#\d+)", "", seg.get("voiceover", ""), flags=re.IGNORECASE).strip()
                rendered = bot.render_top5_card(bg_img, 6 - idx, 5, clean or seg.get("voiceover", ""), font_choice=font_choice)
        elif idx == 0:
            rendered = bot.render_hook_card(bg_img, seg.get("voiceover", ""), font_choice=font_choice)
        elif idx == len(scenes) - 1:
            rendered = _render_image_slide(bot, bg_img, seg.get("voiceover", "What do you think?"), "SUBSCRIBE FOR MORE!", font_choice)
        else:
            overlay = Image.new("RGBA", target_size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            draw.rectangle([0, 0, width, 40], fill=bot.PALETTE["accent_primary"] + (200,))
            draw.rectangle([0, height - 40, width, height], fill=bot.PALETTE["accent_secondary"] + (200,))
            rendered = Image.alpha_composite(bg_img, overlay)

        rendered.convert("RGB").save(img_path, "JPEG", quality=95)
        packages[idx] = [{
            "image": img_path,
            "text": "" if (format_mode == "top5" or idx in (0, len(scenes) - 1)) else seg.get("voiceover", ""),
            "ai_generated": used_ai,
            "source_type": source_type,
        }]

    script_data["ai_image_ratio"] = round(ai_count / max(1, len(scenes)), 2)
    script_data["visual_coverage"] = 1.0
    print(f"   [+] Visual QA complete: {len(scenes)}/{len(scenes)} slides have verified relevant images.")
    return packages


def patch_visual_pipeline(bot):
    """Install strict visual sourcing and replace the placeholder-producing renderer."""
    async def process(script_data, language_cfg, format_mode="regular"):
        return await _process_visuals(bot, script_data, language_cfg, format_mode)
    bot.process_visuals_async = process
    return bot
