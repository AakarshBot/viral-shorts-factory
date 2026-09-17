"""Bounded visual identity QA for the Shorts factory.

The visual QA job is deliberately narrow: determine whether the candidate
image corresponds to the requested visual subject. It does not judge whether
the person is performing the narrated action or whether the setting perfectly
illustrates the story. Those are not requirements for a useful Shorts visual.
"""
import hashlib
import io
import os
import threading

from PIL import Image

GEMINI_VISUAL_MAX_REQUESTS = max(1, int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS_PER_RUN", "8")))
GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE = max(1, int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE", "4")))
GEMINI_VISUAL_RETRIES = 0
GEMINI_VISUAL_MODEL = os.getenv("GEMINI_VISUAL_MODEL", "gemini-3.1-flash-lite")
VISUAL_QA_RUNTIME_VERSION = "2026-09-17-v10-identity-only"

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


def _cache_key(img_bytes, entity, tier, visual_type=""):
    h = hashlib.sha256(img_bytes).hexdigest()
    return (h, str(entity).strip().lower(), str(tier).strip().lower(), str(visual_type).strip().upper())


def _tier_for(intent, visual_type, source):
    """Legacy tier classifier retained for runtime/CI compatibility.

    The active QA decision is identity-only; this helper only preserves the
    source-routing contract used by older smoke tests and integrations.
    """
    intent_text = str(intent or "").strip().lower()
    visual_type = str(visual_type or "").strip().upper()
    source_text = str(source or "").strip().lower()
    if intent_text in {"conceptual", "concept"} or visual_type in {"GENERAL_CONTEXT", "CONCEPT", "PROCESS"} and intent_text == "conceptual":
        return "SKIPPED_CONCEPTUAL"
    if visual_type == "PERSON" and source_text == "wikipedia":
        return "CURATED_PERSON"
    if visual_type == "PERSON":
        return "STRICT"
    if intent_text == "stadium_event" and visual_type == "EVENT":
        return "GENRE_PLAUSIBLE_EVENT"
    return "IDENTITY"


def _is_conceptual(intent):
    return str(intent or "").strip().lower() == "conceptual"


def _identity_prompt(entity, visual_type=""):
    return f"""Look at this image and answer one question only:

Does this image visibly correspond to the requested visual subject: {entity}?

Subject type: {visual_type}

Rules:
1. Judge the IMAGE, not the narration or video story.
2. The requested subject must be visibly identifiable in the image.
3. For a PERSON, the image must depict that specific person, not merely another person from the same sport, team or organisation.
4. For a TEAM or GROUP, the visible team/group identity must correspond to the requested subject. If the image clearly depicts a different team or group, return NO.
5. For an ORGANISATION, accept a genuine image that visibly represents that organisation, such as its people, headquarters, office, official setting or clearly identifiable branding. A generic unrelated person is not enough.
6. For a LOCATION or LANDMARK, the image must visibly depict that place or landmark.
7. For an EVENT or TOURNAMENT, the image must visibly correspond to that named event/tournament, rather than merely showing a generic event of the same type.
8. Ignore the person's exact activity, clothing, pose, venue, job setting or what they are doing. Those details do not need to match the story.
9. Do not reject a valid subject image because it does not illustrate the narrated action.
10. Reject memes, unrelated stock imagery, generic illustrations, search-page screenshots, or images where the requested subject cannot actually be identified.
11. If the image is genuinely ambiguous and there is not enough visible evidence to establish the requested subject, return NO.

Return exactly YES or NO followed by one short reason."""


def strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key, tier="STRICT", visual_type=""):
    """Return True/False/None. QA verifies subject identity only."""
    global _VIDEO_CALLS, _SCENE_CALLS, _CIRCUIT_OPEN
    if tier == "CURATED_PERSON" or tier == "STRICT(person)":
        print("   [Visual QA] Tier=STRICT(person) | Gemini=SKIPPED (curated person source).", flush=True)
        return True
    if tier in {"SKIPPED_CONCEPTUAL", "SKIPPED(conceptual)"} or _is_conceptual(intent):
        print("   [Visual QA] Tier=SKIPPED(conceptual) | Gemini=SKIPPED.", flush=True)
        return True
    if not api_key:
        print("   [Visual QA] Tier=IDENTITY | No Gemini API key; semantic verification unavailable.", flush=True)
        return None

    tier = "IDENTITY"
    key = _cache_key(img_bytes, entity, tier, visual_type)
    if key in _CACHE:
        cached = _CACHE[key]
        print(f"   [Visual QA] Tier=IDENTITY | cached verdict={'YES' if cached is True else 'NO' if cached is False else 'UNCERTAIN'}", flush=True)
        return cached

    with _LOCK:
        if _CIRCUIT_OPEN:
            print("   [Visual QA] Circuit breaker open; NO Gemini API call attempted.", flush=True)
            return None
        if _VIDEO_CALLS >= GEMINI_VISUAL_MAX_REQUESTS:
            print(f"   [Visual QA] Per-video Gemini visual request budget exhausted ({GEMINI_VISUAL_MAX_REQUESTS}).", flush=True)
            return None
        if _SCENE_CALLS >= GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE:
            print(f"   [Visual QA] Per-scene Gemini visual request budget exhausted ({GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE}).", flush=True)
            return None
        _VIDEO_CALLS += 1
        _SCENE_CALLS += 1
        call_no = _VIDEO_CALLS

    print(f"   [Visual QA] Tier=IDENTITY | Gemini request {call_no}/{GEMINI_VISUAL_MAX_REQUESTS} (1 attempt only).", flush=True)
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt_text = _identity_prompt(entity, visual_type)
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        response = client.models.generate_content(model=GEMINI_VISUAL_MODEL, contents=[prompt_text, image])
        raw_text = str(getattr(response, "text", "") or "").strip()
        display_text = raw_text if len(raw_text) <= 1000 else raw_text[:1000] + "...[truncated]"
        print(f"   [Visual QA] IDENTITY | Gemini raw verdict: {display_text!r}", flush=True)
        text = raw_text.upper()
        result = True if text.startswith("YES") else False if text.startswith("NO") else None
        if result is None:
            print("   [Visual QA] Gemini returned an uncertain answer.", flush=True)
        _CACHE[key] = result
        return result
    except Exception as exc:
        msg = str(exc).lower()
        if any(x in msg for x in ("429", "quota", "resource exhausted", "rate limit")):
            with _LOCK:
                _CIRCUIT_OPEN = True
            print("   [Visual QA] Gemini quota/rate-limit detected; circuit breaker opened.", flush=True)
        else:
            print(f"   [Visual QA] Gemini request failed: {type(exc).__name__}: {exc}", flush=True)
        return None


def install_visual_qa_bridge(visual_runtime_module):
    """Compatibility bridge used by app.py and the legacy runtime."""
    if visual_runtime_module is None:
        return False
    visual_runtime_module.strict_gemini_check = strict_gemini_check
    visual_runtime_module.reset_visual_qa_video_budget = reset_visual_qa_video_budget
    visual_runtime_module.start_visual_qa_scene = start_visual_qa_scene
    visual_runtime_module.get_visual_qa_calls_used = get_visual_qa_calls_used
    visual_runtime_module._visual_qa_bridge_version = VISUAL_QA_RUNTIME_VERSION
    print(f"[Visual QA] Bounded bridge installed | runtime={VISUAL_QA_RUNTIME_VERSION}", flush=True)
    return True
