"""Bounded visual QA for the Shorts factory.

Tiers:
- Curated PERSON assets from Wikipedia/Commons: cheap visual sanity only.
- EVENT/news-event/stadium-event scenes: genre-plausibility Gemini check.
- conceptual scenes: cheap visual sanity only.
- Other third-party real-entity assets: strict Gemini relevance check.

Gemini is a bounded quality-control layer, never an unbounded retry loop.
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


def _cache_key(img_bytes, entity, intent, prompt, video_title, tier):
    h = hashlib.sha256(img_bytes).hexdigest()
    return (h, str(entity).strip().lower(), str(intent).strip().lower(), str(prompt).strip().lower(), str(video_title).strip().lower(), str(tier).strip().lower())


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
Search prompt: {prompt}
Voiceover: {voice}
Video title: {video_title}
Return only YES or NO."""


def _strict_prompt(entity, intent, prompt, voice, video_title):
    return f"""Check whether this image clearly and reasonably matches the named real-world entity and scene.
Entity: {entity}
Visual intent: {intent}
Specific search prompt: {prompt}
Voiceover: {voice}
Video title: {video_title}
Return only YES if the image clearly shows the named entity or is a strong, direct visual match to the scene; otherwise return NO."""


def strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key, tier="STRICT", visual_type=""):
    """Return True/False/None. Gemini is never called for cheap-pass tiers."""
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
    key = _cache_key(img_bytes, entity, intent, prompt, video_title, tier)
    if key in _CACHE:
        return _CACHE[key]
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
        prompt_text = _event_prompt(entity, intent, prompt, voice, video_title) if tier == "GENRE_PLAUSIBLE_EVENT" else _strict_prompt(entity, intent, prompt, voice, video_title)
        image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        response = client.models.generate_content(model=GEMINI_VISUAL_MODEL, contents=[prompt_text, image])
        text = str(getattr(response, "text", "") or "").strip().upper()
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
