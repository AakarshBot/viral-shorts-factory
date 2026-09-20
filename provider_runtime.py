"""Current external-provider adapters used by the production dashboard.

Provider adapters are deliberately kept behind one idempotent patch point so
Streamlit reruns cannot stack wrappers or duplicate expensive external calls.

Gemini visual-QA budgeting is owned by visual_qa_runtime. Keeping a second
provider-level counter here caused misleading logs and could consume a stale
process-level budget across multiple videos.
"""
import io
import os

from PIL import Image


HF_IMAGE_MODEL = os.getenv(
    "HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell"
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



def patch_provider_adapters(bot):
    """Bind current providers; visual_qa_runtime owns Gemini QA budgets/circuit breaking."""
    bot.fetch_hf_ai_image = hf_text_to_image

    if not getattr(bot, "_provider_adapters_installed", False):
        bot._provider_adapters_installed = True
        print(
            f"   [Providers] Hugging Face Inference Providers adapter installed "
            f"(model={HF_IMAGE_MODEL}).",
            flush=True,
        )
    return bot
