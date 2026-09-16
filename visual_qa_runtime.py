"""Production Gemini visual-QA bridge.

Keeps the strict visual gate fail-closed while using the documented Gemini REST
JSON field names and emitting actionable diagnostics when verification fails.
"""
import base64
import os
import requests

GEMINI_VISUAL_MODEL = os.getenv("GEMINI_VISUAL_MODEL", "gemini-3.6-flash")
GEMINI_VISUAL_TIMEOUT_SECONDS = 15


def strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key):
    if not api_key:
        print("   [Visual QA] Gemini verifier unavailable: GEMINI_API_KEY is missing.", flush=True)
        return None

    try:
        encoded = base64.b64encode(img_bytes).decode("utf-8")
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
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{GEMINI_VISUAL_MODEL}:generateContent"
        )
        payload = {
            "contents": [{
                "parts": [
                    {"text": instruction},
                    {
                        "inline_data": {
                            "mime_type": "image/jpeg",
                            "data": encoded,
                        }
                    },
                ]
            }],
            "generation_config": {
                "temperature": 0.0,
                "max_output_tokens": 8,
            },
        }
        response = requests.post(
            url,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=GEMINI_VISUAL_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            detail = response.text[:500].replace("\n", " ")
            print(
                f"   [Visual QA] Gemini HTTP {response.status_code} "
                f"using {GEMINI_VISUAL_MODEL}: {detail}",
                flush=True,
            )
            return None

        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            print(
                f"   [Visual QA] Gemini returned no candidates: {str(body)[:500]}",
                flush=True,
            )
            return None

        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = " ".join(str(part.get("text", "")) for part in parts).strip().upper()
        finish_reason = candidates[0].get("finishReason", "unknown")
        print(
            f"   [Visual QA] Gemini verdict={text[:40] or '<empty>'} "
            f"finish={finish_reason} model={GEMINI_VISUAL_MODEL}",
            flush=True,
        )
        if text.startswith("PASS"):
            return True
        if text.startswith("FAIL"):
            return False
        return None
    except Exception as exc:
        print(f"   [Visual QA] Gemini verifier exception: {type(exc).__name__}: {exc}", flush=True)
        return None


def install_visual_qa_bridge(visual_runtime_module):
    """Replace only the Gemini verifier; keep the strict asset-selection logic intact."""
    visual_runtime_module._strict_gemini_check = strict_gemini_check
    return visual_runtime_module
