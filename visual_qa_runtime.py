"""Production Gemini visual-QA bridge.

Keeps the strict visual gate fail-closed while using the supported Gemini SDK
for current API-key authentication and multimodal image understanding.
"""
import os

GEMINI_VISUAL_MODEL = os.getenv("GEMINI_VISUAL_MODEL", "gemini-3.8-flash")
GEMINI_VISUAL_TIMEOUT_SECONDS = 20


def _clean_api_key(value):
    """Remove accidental whitespace/JSON quoting without logging the secret."""
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


def strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key):
    api_key = _clean_api_key(api_key)
    if not api_key:
        print("   [Visual QA] Gemini verifier unavailable: GEMINI_API_KEY is missing.", flush=True)
        return None

    try:
        from google import genai
        from google.genai import types

        instruction = (
            "You are a strict visual editor for a factual YouTube Short. "
            "Inspect the supplied image itself. Return PASS only if the image clearly depicts "
            "the requested primary entity and matches the requested visual intent. "
            "For a named person, PASS only if the person is plausibly identifiable as that person. "
            "Reject generic stock photos, unrelated people, wrong events, generic concept art, "
            "memes, misleading screenshots, and images where the requested subject is absent. "
            "If uncertain, return FAIL. Return exactly one word: PASS or FAIL.\n\n"
            f"Video topic: {video_title}\n"
            f"Primary entity: {entity}\n"
            f"Visual intent: {intent}\n"
            f"Search prompt: {prompt}\n"
            f"Scene narration: {voice}"
        )

        print(
            f"   [Visual QA] Gemini request model={GEMINI_VISUAL_MODEL} "
            f"auth={_key_diagnostic(api_key)}",
            flush=True,
        )

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=GEMINI_VISUAL_TIMEOUT_SECONDS * 1000),
        )
        image_part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
        response = client.models.generate_content(
            model=GEMINI_VISUAL_MODEL,
            contents=[instruction, image_part],
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=8,
                candidate_count=1,
            ),
        )

        text = str(getattr(response, "text", "") or "").strip().upper()
        print(
            f"   [Visual QA] Gemini verdict={text[:40] or '<empty>'} "
            f"model={GEMINI_VISUAL_MODEL}",
            flush=True,
        )
        if text.startswith("PASS"):
            return True
        if text.startswith("FAIL"):
            return False

        print(
            f"   [Visual QA] Gemini returned an unusable verdict: {text[:120] or '<empty>'}",
            flush=True,
        )
        return None
    except Exception as exc:
        print(
            f"   [Visual QA] Gemini verifier exception: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return None


def install_visual_qa_bridge(visual_runtime_module):
    """Replace only the Gemini verifier; keep strict asset-selection logic intact."""
    visual_runtime_module._strict_gemini_check = strict_gemini_check
    return visual_runtime_module
