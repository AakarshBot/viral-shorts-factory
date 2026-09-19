"""Strict visual QA for the Shorts factory.

QA verifies identity and visual intent when the verification service is
available. A genuine NO is a hard rejection; service unavailability or an
ambiguous answer is an uncertain candidate, so retrieval can continue and the
factory can still choose a real image rather than treating infrastructure
failure as a content failure.
"""
import hashlib
import io
import os
import threading

from PIL import Image

from visual_taxonomy_runtime import genre_acceptance_rule

GEMINI_VISUAL_MAX_REQUESTS = max(1, int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS_PER_RUN", "16")))
GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE = max(1, int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE", "4")))
GEMINI_VISUAL_RETRIES = 0
GEMINI_VISUAL_MODEL = os.getenv("GEMINI_VISUAL_MODEL", "gemini-3.1-flash-lite")
VISUAL_QA_RUNTIME_VERSION = "2026-09-18-v14-identity-aware-uncertainty"

_VIDEO_CALLS = 0
_SCENE_CALLS = 0
_CIRCUIT_OPEN = False
_LOCK = threading.Lock()
_CACHE = {}


def reset_visual_qa_video_budget():
    global _VIDEO_CALLS, _SCENE_CALLS, _CIRCUIT_OPEN
    with _LOCK:
        _VIDEO_CALLS = 0
        _SCENE_CALLS = 0
        _CIRCUIT_OPEN = False


def start_visual_qa_scene():
    global _SCENE_CALLS
    with _LOCK:
        _SCENE_CALLS = 0


def get_visual_qa_calls_used():
    with _LOCK:
        return _VIDEO_CALLS


def _cache_key(img_bytes, entity, tier, visual_type="", visual_genre=""):
    h = hashlib.sha256(img_bytes).hexdigest()
    return (h, str(entity).strip().lower(), str(tier).strip().upper(), str(visual_type).strip().upper(), str(visual_genre).strip().upper())


def _tier_for(intent, visual_type, source):
    return "IDENTITY"


def _is_conceptual(intent):
    return False


def _identity_prompt(entity, visual_type="", intent="", search_prompt="", visual_genre=""):
    genre_rule = genre_acceptance_rule(visual_genre)
    return f"""Look at this image and answer one question only.

Does this image visibly represent the requested visual subject AND the requested visual intent?

Locked visual subject: {entity}
Subject type: {visual_type}
Visual genre: {visual_genre}
Genre-specific acceptance target: {genre_rule}
Visual intent/context: {intent}
Search phrase used: {search_prompt}

Rules:
1. Judge the IMAGE, not the narration alone.
2. The locked visual subject must be visibly identifiable when the subject is identity-specific.
3. Concrete descriptors in the search phrase such as logo, portrait, headshot, map, emblem, badge, seal, screenshot, poster or flag are requirements, not suggestions.
4. For an action, match, event, ceremony, speech, interview or other scene-specific visual, the requested action/event must be visibly present. A generic stadium, fan, crowd, team photo or related environment is NOT sufficient.
5. For a PERSON, the image must depict that specific person. If the scene asks for that person's action, the visible action/context must also match.
6. For a TEAM or GROUP, the visible team/group identity must correspond to the requested subject and any requested action/context must also be visible.
7. For an ORGANISATION, accept genuine visible branding, headquarters, office or clearly identifiable organisational setting only when it matches the requested genre.
8. For a LOCATION or LANDMARK, the image must visibly depict that specific place.
9. For an EVENT or TOURNAMENT, the image must visibly correspond to that named event/tournament, not merely a generic event of the same type.
10. Do not accept a merely conceptual representation when the request is for a factual photograph or specific real-world scene.
11. Reject memes, unrelated stock imagery, generic illustrations, search-page screenshots, or images where the requested subject/scene cannot actually be identified.
12. If the image is genuinely ambiguous or any required subject/action cannot be established from visible evidence, return UNCERTAIN rather than guessing.
13. A contextual image is acceptable only when the genre-specific acceptance target explicitly allows context and all required visual elements are present.

Return exactly YES, NO, or UNCERTAIN followed by one short reason."""


def strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key, tier="IDENTITY", visual_type="", visual_genre=""):
    global _VIDEO_CALLS, _SCENE_CALLS, _CIRCUIT_OPEN
    if not api_key:
        print("   [Visual QA] IDENTITY | Gemini unavailable (no API key); candidate remains uncertain.", flush=True)
        return None

    key = _cache_key(img_bytes, entity, tier, visual_type, visual_genre)
    if key in _CACHE:
        cached = _CACHE[key]
        print(f"   [Visual QA] IDENTITY | cached verdict={'YES' if cached is True else 'NO'}", flush=True)
        return cached

    with _LOCK:
        if _CIRCUIT_OPEN:
            print("   [Visual QA] Circuit breaker open; candidate remains uncertain.", flush=True)
            return None
        if _VIDEO_CALLS >= GEMINI_VISUAL_MAX_REQUESTS:
            print(f"   [Visual QA] Per-video visual request budget exhausted ({GEMINI_VISUAL_MAX_REQUESTS}); candidate remains uncertain.", flush=True)
            return None
        if _SCENE_CALLS >= GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE:
            print(f"   [Visual QA] Per-scene visual QA budget exhausted ({GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE}); candidate remains uncertain.", flush=True)
            return None
        _VIDEO_CALLS += 1
        _SCENE_CALLS += 1
        call_no = _VIDEO_CALLS

    print(f"   [Visual QA] IDENTITY | Gemini request {call_no}/{GEMINI_VISUAL_MAX_REQUESTS}.", flush=True)
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        response = client.models.generate_content(
            model=GEMINI_VISUAL_MODEL,
            contents=[_identity_prompt(entity, visual_type, intent, prompt, visual_genre), image],
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        raw_text = str(getattr(response, "text", "") or "").strip()
        display_text = raw_text if len(raw_text) <= 1000 else raw_text[:1000] + "...[truncated]"
        print(f"   [Visual QA] IDENTITY | Gemini raw verdict: {display_text!r}", flush=True)
        text = raw_text.upper()
        if text.startswith("YES"):
            result = True
        elif text.startswith("NO"):
            result = False
        else:
            print("   [Visual QA] Ambiguous Gemini answer; candidate remains uncertain.", flush=True)
            return None
        _CACHE[key] = result
        return result
    except Exception as exc:
        msg = str(exc).lower()
        if any(x in msg for x in ("429", "quota", "resource exhausted", "rate limit")):
            with _LOCK:
                _CIRCUIT_OPEN = True
            print("   [Visual QA] Gemini quota/rate-limit detected; circuit breaker opened; candidate remains uncertain.", flush=True)
        else:
            print(f"   [Visual QA] Gemini request failed: {type(exc).__name__}: {exc}; candidate remains uncertain.", flush=True)
        return None


def install_visual_qa_bridge(visual_runtime_module):
    if visual_runtime_module is None:
        return False
    visual_runtime_module.strict_gemini_check = strict_gemini_check
    visual_runtime_module.reset_visual_qa_video_budget = reset_visual_qa_video_budget
    visual_runtime_module.start_visual_qa_scene = start_visual_qa_scene
    visual_runtime_module.get_visual_qa_calls_used = get_visual_qa_calls_used
    visual_runtime_module._visual_qa_bridge_version = VISUAL_QA_RUNTIME_VERSION
    print(f"[Visual QA] Strict identity-aware gate installed | runtime={VISUAL_QA_RUNTIME_VERSION}", flush=True)
    return True
