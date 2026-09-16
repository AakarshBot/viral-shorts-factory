"""Production Gemini visual-QA bridge.

Keeps visual QA fail-closed while adding caching and a per-process request
budget so deep visual search cannot accidentally burn unlimited Gemini calls.
"""
import hashlib
import os
import time

GEMINI_VISUAL_MODEL = os.getenv("GEMINI_VISUAL_MODEL", "gemini-3.8-flash")
GEMINI_VISUAL_TIMEOUT_SECONDS = 20
GEMINI_VISUAL_RETRIES = 2
GEMINI_VISUAL_MAX_REQUESTS = int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS", "24"))
_GEMINI_QUOTA_EXHAUSTED = False
_GEMINI_REQUESTS = 0
_GEMINI_CACHE = {}


def _clean_api_key(value):
    key = str(value or "").strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {"\"", "'"}:
        key = key[1:-1].strip()
    return key


def _key_diagnostic(api_key):
    key = _clean_api_key(api_key)
    if not key:
        return "missing"
    if key.startswith("AQ."):
        return f"AQ authorization key (length={len(key)})"
    if key.startswith("AIza"):
        return f"standard API key (length={len(key)})"
    return f"unrecognized key format (length={len(key)})"


def _extract_verdict(response):
    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            part_text = str(getattr(part, "text", "") or "").strip()
            if part_text and not bool(getattr(part, "thought", False)):
                return part_text
    return ""


def _response_diagnostic(response):
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "candidates=0"
    candidate = candidates[0]
    finish_reason = getattr(candidate, "finish_reason", None)
    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None) or []
    visible_parts = sum(1 for part in parts if str(getattr(part, "text", "") or "").strip() and not bool(getattr(part, "thought", False)))
    thought_parts = sum(1 for part in parts if bool(getattr(part, "thought", False)))
    return f"finish_reason={finish_reason or '<none>'} parts={len(parts)} visible_text_parts={visible_parts} thought_parts={thought_parts}"


def _is_institutional_entity(entity):
    text = str(entity or "").lower()
    markers = ("government", "ministry", "department", "administration", "authority", "council", "commission", "committee", "organization", "organisation", "party", "company", "corporation", "university", "institution", "agency")
    return any(marker in text for marker in markers)


def _build_instruction(entity, intent, prompt, voice, video_title):
    if _is_institutional_entity(entity):
        entity_rule = (
            "The primary entity is an institution. Do not require its name or logo to be visible. "
            "PASS only when the image shows a concrete, recognizable manifestation of the requested "
            "institution/event/context. Reject generic buildings, generic meetings and unrelated events."
        )
    else:
        entity_rule = (
            "The primary entity must be visibly present. For a named person, PASS only if the person "
            "is plausibly identifiable as that person. Reject images where the requested subject is absent."
        )
    return (
        "You are a strict visual editor for a factual YouTube Short. Inspect the supplied image itself "
        "and judge the whole requested scene, not just keyword overlap. "
        f"{entity_rule} The image must match the visual intent and event/context. Reject generic stock "
        "photos, unrelated people, wrong events, generic concept art, memes, misleading screenshots, "
        "and loosely related images. If uncertain, return FAIL. Return exactly one word: PASS or FAIL.\n\n"
        f"Video topic: {video_title}\nPrimary entity: {entity}\nVisual intent: {intent}\n"
        f"Search prompt: {prompt}\nScene narration: {voice}"
    )


def strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key):
    global _GEMINI_QUOTA_EXHAUSTED, _GEMINI_REQUESTS
    api_key = _clean_api_key(api_key)
    if not api_key:
        print("   [Visual QA] Gemini verifier unavailable: GEMINI_API_KEY is missing.", flush=True)
        return None
    if _GEMINI_QUOTA_EXHAUSTED:
        print("   [Visual QA] Gemini quota circuit breaker is open; continuing source search without unverifiable acceptance.", flush=True)
        return None

    try:
        image_hash = hashlib.sha256(img_bytes).hexdigest()
        cache_key = (image_hash, str(entity), str(intent), str(prompt), str(video_title))
        if cache_key in _GEMINI_CACHE:
            return _GEMINI_CACHE[cache_key]

        if _GEMINI_REQUESTS >= GEMINI_VISUAL_MAX_REQUESTS:
            print(
                f"   [Visual QA] Per-run Gemini budget reached ({GEMINI_VISUAL_MAX_REQUESTS}); "
                "continuing deeper image search without accepting unverified candidates.",
                flush=True,
            )
            return None

        from google import genai
        from google.genai import types
        instruction = _build_instruction(entity, intent, prompt, voice, video_title)
        print(
            f"   [Visual QA] Gemini request {_GEMINI_REQUESTS + 1}/{GEMINI_VISUAL_MAX_REQUESTS} "
            f"model={GEMINI_VISUAL_MODEL} auth={_key_diagnostic(api_key)}",
            flush=True,
        )
        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=GEMINI_VISUAL_TIMEOUT_SECONDS * 1000))
        image_part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
        for attempt in range(GEMINI_VISUAL_RETRIES + 1):
            try:
                _GEMINI_REQUESTS += 1
                response = client.models.generate_content(
                    model=GEMINI_VISUAL_MODEL,
                    contents=[instruction, image_part],
                    config=types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_level="low"),
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    ),
                )
                text = _extract_verdict(response).strip().upper()
                diagnostic = _response_diagnostic(response)
                print(f"   [Visual QA] Gemini verdict={text[:40] or '<empty>'} {diagnostic}", flush=True)
                if text.startswith("PASS"):
                    _GEMINI_CACHE[cache_key] = True
                    return True
                if text.startswith("FAIL"):
                    _GEMINI_CACHE[cache_key] = False
                    return False
                return None
            except Exception as exc:
                message = str(exc).upper()
                if "429" in message or "RESOURCE_EXHAUSTED" in message or "QUOTA" in message:
                    _GEMINI_QUOTA_EXHAUSTED = True
                    print("   [Visual QA] Gemini quota exhausted; source search will continue but unverified candidates remain rejected.", flush=True)
                    return None
                transient = any(code in message for code in ("503", "UNAVAILABLE", "DEADLINE_EXCEEDED"))
                if transient and attempt < GEMINI_VISUAL_RETRIES:
                    delay = 2 ** attempt
                    print(f"   [Visual QA] Gemini transient error; retrying in {delay}s", flush=True)
                    time.sleep(delay)
                    continue
                raise
    except Exception as exc:
        print(f"   [Visual QA] Gemini verifier exception: {type(exc).__name__}: {exc}", flush=True)
        return None


def _install_render_safety_patch():
    try:
        import factory_runtime
        from PIL import Image
        def safe_vignette(size, strength=0.5):
            small = Image.new("L", (120, 213), 0)
            pixels = small.load(); cx, cy = 60, 106.5
            max_distance = (cx * cx + cy * cy) ** 0.5
            for y in range(213):
                for x in range(120):
                    distance = (((x - cx) ** 2 + (y - cy) ** 2) ** 0.5) / max_distance
                    normalized = max(0.0, (distance - 0.18) / 0.82)
                    pixels[x, y] = int(max(0, min(255, normalized ** 1.8 * 255 * strength)))
            mask = small.resize(size, Image.Resampling.BILINEAR)
            output = Image.new("RGBA", size, (0, 0, 0, 0)); output.paste((0, 0, 0, 255), (0, 0, *size), mask)
            return output
        factory_runtime._vignette = safe_vignette
        print("   [Render Safety] Fractional-power vignette guard installed.", flush=True)
    except Exception as exc:
        print(f"   [Render Safety] Patch unavailable: {type(exc).__name__}: {exc}", flush=True)


def install_visual_qa_bridge(visual_runtime_module):
    visual_runtime_module._strict_gemini_check = strict_gemini_check
    _install_render_safety_patch()
    return visual_runtime_module
