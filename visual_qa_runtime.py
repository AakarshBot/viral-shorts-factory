"""Bounded visual QA for the Shorts factory.

Gemini is used only for semantic identity/context checks after cheap local
quality filtering. A candidate with an uncertain verdict is never allowed to
become a fallback image.
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
VISUAL_QA_RUNTIME_VERSION = "2026-09-17-v8"

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


def _cache_key(img_bytes, entity, intent, prompt, video_title, tier, visual_type=""):
    h = hashlib.sha256(img_bytes).hexdigest()
    return (h, str(entity).strip().lower(), str(intent).strip().lower(), str(prompt).strip().lower(), str(video_title).strip().lower(), str(tier).strip().lower(), str(visual_type).strip().upper())


def _is_event_genre(intent, visual_type=""):
    text = f"{intent} {visual_type}".strip().lower()
    return str(visual_type).upper() == "EVENT" or text in {"news_event", "stadium_event"} or any(x in text for x in ("news event", "stadium event", "ceremony", "match", "awards ceremony", "red carpet", "press conference"))


def _is_conceptual(intent):
    return str(intent or "").strip().lower() == "conceptual"


def _tier_for(intent, visual_type, source):
    source_l = str(source or "").strip().lower()
    if str(visual_type).upper() == "PERSON" and source_l in {"wikipedia", "commons"}:
        return "CURATED_PERSON"
    if _is_conceptual(intent):
        return "SKIPPED_CONCEPTUAL"
    if _is_event_genre(intent, visual_type):
        return "GENRE_PLAUSIBLE_EVENT"
    return "STRICT"


def _event_prompt(entity, intent, prompt, voice, video_title):
    return f"""Assess this image for a video scene.
This is an EVENT / NEWS-EVENT / STADIUM-EVENT visual. Do NOT try to prove that it is the exact named event.
Instead answer whether it plausibly depicts the general real-world scene type described: for example a red carpet event, awards ceremony, stadium/sports venue, match, ceremony, or press conference.
It must be a genuine real photo that is broadly relevant to that scene type, not a meme, unrelated stock image, illustration, or completely mismatched scene.
Named entity: {entity}
Visual intent: {intent}
Scene requirement: {prompt}
Voiceover: {voice}
Video title: {video_title}
Return YES or NO followed by one short reason."""


def _strict_prompt(entity, intent, prompt, voice, video_title, visual_type=""):
    """Build a strict identity and context test with contradiction checks."""
    return f"""Look at this image and judge whether it is suitable for ONE specific video scene.

Named subject: {entity}
Subject type: {visual_type}
Scene intent: {intent}
Scene requirement: {prompt}
Narration context: {voice}
Video title: {video_title}

Rules:
1. The image must visibly represent the named subject or place when one is specified.
2. It must also fit the scene requirement/context, not merely be vaguely related to the same topic.
3. Treat organizations, locations, products, documents, and events as their actual entity types; do NOT turn them into people because people are mentioned nearby.
4. Use the narration only to understand the target context; do NOT use the narration as proof that an unrelated image is correct.
5. Actively look for contradictions. If the scene refers to one team, person, event, product, place, sport, or group and the image clearly depicts a different one, return NO.
6. For sports teams, check the visible team identity and, when the narration/context establishes it, the correct sport, competition, era, seniority, and gender category. A clearly identifiable women's team must be rejected for a men's-team scene, and vice versa. Do not solve this by guessing from a generic jersey alone.
7. For people, reject a different person even if they play the same sport or work for the same organization.
8. For events, reject an image that visibly belongs to a different event when the scene identifies a concrete event.
9. Reject memes, generic stock imagery, illustrations, screenshots of search pages, logos by themselves when a real-world scene is required, and clearly mismatched scenes.
10. When the scene requirement is specific (for example a person speaking, an organization press conference, a city street, a product launch), require visible evidence of that specific context.
11. If the image is ambiguous and there is not enough visible evidence to establish the requested identity, prefer NO rather than accepting a merely plausible image.

Return YES or NO followed by one short reason."""


def strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key, tier="STRICT", visual_type=""):
    """Return True/False/None. Gemini is never called for cheap-pass tiers."""
    global _VIDEO_CALLS, _SCENE_CALLS, _CIRCUIT_OPEN
    tier = tier or _tier_for(intent, visual_type, "")
    if tier == "CURATED_PERSON":
        print("   [Visual QA] Tier=STRICT(person) source=curated | Gemini=SKIPPED", flush=True)
        return True
    if tier == "SKIPPED_CONCEPTUAL":
        print("   [Visual QA] Tier=SKIPPED(conceptual) | Gemini=SKIPPED", flush=True)
        return True
    if not api_key:
        print(f"   [Visual QA] Tier={tier} | No Gemini API key; semantic verification unavailable.", flush=True)
        return None
    key = _cache_key(img_bytes, entity, intent, prompt, video_title, tier, visual_type)
    if key in _CACHE:
        cached = _CACHE[key]
        print(f"   [Visual QA] Tier={tier} | cached verdict={'YES' if cached is True else 'NO' if cached is False else 'UNCERTAIN'}", flush=True)
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
    print(f"   [Visual QA] Tier={tier} | Gemini request {call_no}/{GEMINI_VISUAL_MAX_REQUESTS} (1 attempt only).", flush=True)
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        if tier == "GENRE_PLAUSIBLE_EVENT":
            prompt_text = _event_prompt(entity, intent, prompt, voice, video_title)
        else:
            prompt_text = _strict_prompt(entity, intent, prompt, voice, video_title, visual_type=visual_type)
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        response = client.models.generate_content(model=GEMINI_VISUAL_MODEL, contents=[prompt_text, image])
        raw_text = str(getattr(response, "text", "") or "").strip()
        display_text = raw_text if len(raw_text) <= 1000 else raw_text[:1000] + "...[truncated]"
        print(f"   [Visual QA] {tier} | Gemini raw verdict: {display_text!r}", flush=True)
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
