"""Entity-only visual QA for the Shorts factory.

QA verifies whether a requested visual subject is visibly represented. It does
not judge the exact scene, action, composition, narration, or search phrase.
A genuine NO is a hard rejection; service unavailability or ambiguity leaves
the candidate uncertain so infrastructure failure is never treated as content
failure.
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
GEMINI_VISUAL_BATCH_SIZE = max(2, min(10, int(os.getenv("GEMINI_VISUAL_BATCH_SIZE", "10"))))
VISUAL_QA_RUNTIME_VERSION = "2026-09-20-v15-entity-only-batch"

_VIDEO_CALLS = 0
_SCENE_CALLS = 0
_CIRCUIT_OPEN = False
LAST_VISUAL_QA_FAILURE = ""
_LOCK = threading.Lock()
_CACHE = {}


def reset_visual_qa_video_budget():
    global _VIDEO_CALLS, _SCENE_CALLS, _CIRCUIT_OPEN, LAST_VISUAL_QA_FAILURE
    with _LOCK:
        _VIDEO_CALLS = 0
        _SCENE_CALLS = 0
        _CIRCUIT_OPEN = False
        LAST_VISUAL_QA_FAILURE = ""


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
    """Ask only whether the requested visual subject is actually present."""
    return f"""Look at this image and answer one question only.

Requested visual subject: {entity}
Subject type: {visual_type or "GENERAL"}

The image does not need to depict a particular action, event, composition, camera angle,
scene, or narration. It only needs to visibly represent the requested subject in a
recognisable form. This applies across people, organisations, teams, locations,
landmarks, products, documents, symbols, objects, concepts, processes, charts, maps,
and other visual formats.

Rules:
1. Judge the IMAGE itself, not the narration.
2. Accept a clearly recognisable representation of the requested subject, even when
   the surrounding scene or context differs from the original slide.
3. For a named person, the image must depict that specific person.
4. For a named organisation, team, product, place, landmark, event, document, symbol,
   or other real-world subject, the image must visibly correspond to that subject.
5. For concepts, processes, charts, maps, or similar non-identity subjects, accept a
   clear visual representation of the requested subject.
6. Do not require the requested action or exact scene. Those are slide-level concerns,
   not entity-bank concerns.
7. Reject unrelated subjects, memes, generic filler, search-page screenshots, or
   images where the requested subject cannot reasonably be identified.
8. If the subject cannot be established from the image, return UNCERTAIN.

Return exactly YES, NO, or UNCERTAIN followed by one short reason."""


def strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key, tier="IDENTITY", visual_type="", visual_genre=""):
    global _VIDEO_CALLS, _SCENE_CALLS, _CIRCUIT_OPEN, LAST_VISUAL_QA_FAILURE
    LAST_VISUAL_QA_FAILURE = ""
    if not api_key:
        LAST_VISUAL_QA_FAILURE = "no_api_key"
        print("   [Visual QA] IDENTITY | Gemini unavailable (no API key); candidate remains uncertain.", flush=True)
        return None

    key = _cache_key(img_bytes, entity, "ENTITY", "ENTITY", "ENTITY")
    if key in _CACHE:
        cached = _CACHE[key]
        print(f"   [Visual QA] IDENTITY | cached verdict={'YES' if cached is True else 'NO'}", flush=True)
        return cached

    with _LOCK:
        if _CIRCUIT_OPEN:
            LAST_VISUAL_QA_FAILURE = "circuit_breaker"
            print("   [Visual QA] Circuit breaker open; candidate remains uncertain.", flush=True)
            return None
        if _VIDEO_CALLS >= GEMINI_VISUAL_MAX_REQUESTS:
            LAST_VISUAL_QA_FAILURE = "video_budget_exhausted"
            print(f"   [Visual QA] Per-video visual request budget exhausted ({GEMINI_VISUAL_MAX_REQUESTS}); candidate remains uncertain.", flush=True)
            return None
        if _SCENE_CALLS >= GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE:
            LAST_VISUAL_QA_FAILURE = "scene_budget_exhausted"
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
            contents=[_identity_prompt(entity, visual_type), image],
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
            LAST_VISUAL_QA_FAILURE = "ambiguous_response"
            print("   [Visual QA] Ambiguous Gemini answer; candidate remains uncertain.", flush=True)
            return None
        _CACHE[key] = result
        return result
    except Exception as exc:
        msg = str(exc).lower()
        if any(x in msg for x in ("429", "quota", "resource exhausted", "rate limit")):
            LAST_VISUAL_QA_FAILURE = "quota_or_rate_limit"
            with _LOCK:
                _CIRCUIT_OPEN = True
            print("   [Visual QA] Gemini quota/rate-limit detected; circuit breaker opened; candidate remains uncertain.", flush=True)
        else:
            LAST_VISUAL_QA_FAILURE = "request_exception"
            print(f"   [Visual QA] Gemini request failed: {type(exc).__name__}: {exc}; candidate remains uncertain.", flush=True)
        return None


def _parse_batch_verdicts(raw_text, expected_count):
    """Parse compact numbered YES/NO/UNCERTAIN verdicts from one batch response."""
    import re
    verdicts = {}
    for match in re.finditer(
        r"(?im)^\s*(\d+)\s*[-:.)]?\s*(YES|NO|UNCERTAIN)\b",
        str(raw_text or ""),
    ):
        index = int(match.group(1)) - 1
        if 0 <= index < int(expected_count):
            verdicts[index] = match.group(2).upper()
    return verdicts


def strict_gemini_check_batch(
    images,
    entity,
    api_key,
    tier="IDENTITY",
    visual_type="",
    visual_genre="",
):
    """Verify several candidates for the same subject in one entity-only Gemini call."""
    global _VIDEO_CALLS, _SCENE_CALLS, _CIRCUIT_OPEN, LAST_VISUAL_QA_FAILURE
    LAST_VISUAL_QA_FAILURE = ""
    image_items = [
        (int(index), bytes(data))
        for index, data in enumerate(images or [])
        if data
    ]
    results = {index: None for index, _data in image_items}
    if not image_items:
        return results
    if not api_key:
        LAST_VISUAL_QA_FAILURE = "no_api_key"
        return results

    uncached = []
    for index, data in image_items:
        key = _cache_key(data, entity, "ENTITY", "ENTITY", "ENTITY")
        if key in _CACHE:
            results[index] = _CACHE[key]
        else:
            uncached.append((index, data, key))

    if not uncached:
        return results

    with _LOCK:
        if _CIRCUIT_OPEN:
            LAST_VISUAL_QA_FAILURE = "circuit_breaker"
            return results
        if _VIDEO_CALLS >= GEMINI_VISUAL_MAX_REQUESTS:
            LAST_VISUAL_QA_FAILURE = "video_budget_exhausted"
            return results
        if _SCENE_CALLS >= GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE:
            LAST_VISUAL_QA_FAILURE = "scene_budget_exhausted"
            return results
        _VIDEO_CALLS += 1
        _SCENE_CALLS += 1
        call_no = _VIDEO_CALLS

    print(
        f"   [Visual QA] ENTITY-BATCH | Gemini request {call_no}/{GEMINI_VISUAL_MAX_REQUESTS} "
        f"for {len(uncached)} candidate(s).",
        flush=True,
    )
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        prompt = (
            f"Check these {len(uncached)} images for one requested visual subject: {entity}\n\n"
            "For each numbered image, answer exactly N YES, N NO, or N UNCERTAIN, "
            "with one very short reason. Do not judge action, scene, composition, narration, "
            "or search intent. Only judge whether the requested subject is visibly represented.\n\n"
            "Requested subject must be recognisable across people, organisations, teams, "
            "places, landmarks, products, documents, symbols, objects, concepts, processes, "
            "charts and maps. Reject unrelated images.\n\n"
            "Images:"
        )
        contents = [prompt]
        for index, data, _key in uncached:
            contents.append(f"\nIMAGE {index + 1}")
            contents.append(Image.open(io.BytesIO(data)).convert("RGB"))

        response = client.models.generate_content(
            model=GEMINI_VISUAL_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        raw_text = str(getattr(response, "text", "") or "").strip()
        verdicts = _parse_batch_verdicts(raw_text, len(uncached))

        for local_index, (index, _data, key) in enumerate(uncached):
            verdict = verdicts.get(local_index)
            if verdict == "YES":
                results[index] = True
                _CACHE[key] = True
            elif verdict == "NO":
                results[index] = False
                _CACHE[key] = False

        if len(verdicts) != len(uncached):
            LAST_VISUAL_QA_FAILURE = "ambiguous_response"
        return results
    except Exception as exc:
        msg = str(exc).lower()
        if any(x in msg for x in ("429", "quota", "resource exhausted", "rate limit")):
            LAST_VISUAL_QA_FAILURE = "quota_or_rate_limit"
            with _LOCK:
                _CIRCUIT_OPEN = True
        else:
            LAST_VISUAL_QA_FAILURE = "request_exception"
        print(
            f"   [Visual QA] ENTITY-BATCH failed: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return results


def install_visual_qa_bridge(visual_runtime_module):
    if visual_runtime_module is None:
        return False
    visual_runtime_module.strict_gemini_check = strict_gemini_check
    visual_runtime_module.strict_gemini_check_batch = strict_gemini_check_batch
    visual_runtime_module.reset_visual_qa_video_budget = reset_visual_qa_video_budget
    visual_runtime_module.start_visual_qa_scene = start_visual_qa_scene
    visual_runtime_module.get_visual_qa_calls_used = get_visual_qa_calls_used
    visual_runtime_module._visual_qa_bridge_version = VISUAL_QA_RUNTIME_VERSION
    print(f"[Visual QA] Entity-only gate installed | runtime={VISUAL_QA_RUNTIME_VERSION}", flush=True)
    return True
