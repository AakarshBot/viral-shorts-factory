"""Current external-provider adapters used by the production dashboard.

Provider adapters are deliberately kept behind one idempotent patch point so
Streamlit reruns cannot stack wrappers or duplicate expensive external calls.
"""
import hashlib
import io
import os
import threading

from PIL import Image


HF_IMAGE_MODEL = os.getenv(
    "HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell"
)
GEMINI_VISUAL_REQUEST_BUDGET = max(
    1, int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS_PER_RUN", "8"))
)


def _clean_token(value):
    token = str(value or "").strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {"\"", "'"}:
        token = token[1:-1].strip()
    return token


def hf_text_to_image(prompt):
    """Generate a PIL image through Hugging Face Inference Providers."""
    token = _clean_token(os.getenv("HF_TOKEN"))
    if not token:
        print(
            "   [Visual Source] HF Inference Providers unavailable: HF_TOKEN is missing.",
            flush=True,
        )
        return None
    if getattr(hf_text_to_image, "_disabled", False):
        return None

    try:
        from huggingface_hub import InferenceClient

        client = InferenceClient(api_key=token, provider="auto")
        return client.text_to_image(prompt, model=HF_IMAGE_MODEL)
    except Exception as exc:
        message = str(exc).lower()
        if "401" in message or "expired" in message or "unauthorized" in message:
            hf_text_to_image._disabled = True
            print(
                "   [Visual Source] HF Inference Providers disabled for this process: "
                "HF_TOKEN is expired or unauthorized. Refresh the token in Streamlit secrets.",
                flush=True,
            )
        else:
            print(
                f"   [Visual Source] HF Inference Providers failed: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
        return None


hf_text_to_image._disabled = False


def _local_quality_gate(img_data, search_prompt="", video_title=""):
    """Cheap local image sanity only; semantic QA belongs to the strict visual gate."""
    try:
        image = Image.open(io.BytesIO(img_data)).convert("RGB")
        width, height = image.size
        if min(width, height) < 300:
            return False
        ratio = width / max(1, height)
        if ratio > 2.5 or ratio < 0.4:
            return False
        return True
    except Exception:
        return False


def _install_visual_qa_budget(visual_runtime_module):
    current = getattr(visual_runtime_module, "_strict_gemini_check", None)
    if current is None or getattr(current, "_provider_budget_guard", False):
        return current

    lock = threading.Lock()
    cache = {}
    state = {"requests": 0}

    def budgeted(img_bytes, entity, intent, prompt, voice, video_title, api_key):
        key = (
            hashlib.sha256(bytes(img_bytes or b"")).hexdigest(),
            str(entity or ""),
            str(intent or ""),
            str(prompt or ""),
            str(video_title or ""),
        )
        with lock:
            if key in cache:
                return cache[key]
            if state["requests"] >= GEMINI_VISUAL_REQUEST_BUDGET:
                print(
                    "   [Visual QA] Per-run Gemini visual request budget exhausted; "
                    "skipping further verifier calls.",
                    flush=True,
                )
                cache[key] = None
                return None
            state["requests"] += 1
            request_no = state["requests"]

        print(
            f"   [Visual QA] Request budget {request_no}/{GEMINI_VISUAL_REQUEST_BUDGET}",
            flush=True,
        )
        result = current(
            img_bytes, entity, intent, prompt, voice, video_title, api_key
        )
        with lock:
            cache[key] = result
        return result

    budgeted._provider_budget_guard = True
    budgeted._provider_budget_state = state
    visual_runtime_module._strict_gemini_check = budgeted
    return budgeted


def patch_provider_adapters(bot):
    """Bind current providers and ensure the visual gate is the only Gemini QA caller."""
    bot.fetch_hf_ai_image = hf_text_to_image

    # The legacy fetchers call passes_quality_gate() before returning data.
    # Replace that function with a local-only sanity check so it cannot make a
    # second hidden Gemini request before visual_runtime's strict gate runs.
    bot.passes_quality_gate = _local_quality_gate

    try:
        import visual_runtime
        _install_visual_qa_budget(visual_runtime)
    except Exception as exc:
        print(
            f"   [Providers] Visual QA budget guard unavailable: {type(exc).__name__}: {exc}",
            flush=True,
        )

    if not getattr(bot, "_provider_adapters_installed", False):
        bot._provider_adapters_installed = True
        print(
            f"   [Providers] Hugging Face Inference Providers adapter installed "
            f"(model={HF_IMAGE_MODEL}).",
            flush=True,
        )
    return bot
