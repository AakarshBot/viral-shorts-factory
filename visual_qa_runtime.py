"""Strict Gemini visual QA with bounded per-video and per-scene budgets."""
import os
import threading

GEMINI_VISUAL_MAX_REQUESTS = max(1, int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS_PER_RUN", "8")))
GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE = max(1, int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE", "3")))
GEMINI_VISUAL_RETRIES = 0

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


def _cache_key(img_bytes, entity, intent, prompt, video_title):
    import hashlib
    h = hashlib.sha256(img_bytes).hexdigest()
    return (h, str(entity).strip().lower(), str(intent).strip().lower(), str(prompt).strip().lower(), str(video_title).strip().lower())


def strict_gemini_check(img_bytes, entity, intent, prompt, voice, video_title, api_key):
    global _VIDEO_CALLS, _SCENE_CALLS, _CIRCUIT_OPEN
    if not api_key:
        print("   [Visual QA] No Gemini API key; semantic verification unavailable.", flush=True)
        return None
    key = _cache_key(img_bytes, entity, intent, prompt, video_title)
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
    print(f"   [Visual QA] Gemini request {call_no}/{GEMINI_VISUAL_MAX_REQUESTS} (1 attempt only).", flush=True)
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")
        prompt_text = f"""Check whether this image is semantically relevant to the video scene.\nEntity: {entity}\nVisual intent: {intent}\nSpecific search prompt: {prompt}\nVoiceover: {voice}\nVideo title: {video_title}\nReturn only YES if the image clearly and reasonably matches the entity and scene context; otherwise return NO."""
        import PIL.Image
        import io
        image = PIL.Image.open(io.BytesIO(img_bytes))
        response = model.generate_content([prompt_text, image])
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
