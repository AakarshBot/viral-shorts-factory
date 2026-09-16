"""Current external-provider adapters used by the production dashboard."""
import os


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
        print("   [Visual Source] HF Inference Providers unavailable: HF_TOKEN is missing.", flush=True)
        return None
    if getattr(hf_text_to_image, "_disabled", False):
        return None

    try:
        from huggingface_hub import InferenceClient

        client = InferenceClient(api_key=token, provider="auto")
        return client.text_to_image(
            prompt,
            model=HF_IMAGE_MODEL,
        )
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
    """Bind current provider implementations once, without wrapper stacking."""
    if getattr(bot, "_provider_adapters_installed", False):
        return bot
    bot.fetch_hf_ai_image = hf_text_to_image
    bot._provider_adapters_installed = True
    print(
        f"   [Providers] Hugging Face Inference Providers adapter installed "
        f"(model={HF_IMAGE_MODEL}).",
        flush=True,
    )
    return bot
