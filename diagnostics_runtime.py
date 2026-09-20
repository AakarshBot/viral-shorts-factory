        "visual_qa_runtime", "visual_strategy_runtime", "visual_semantic_guard_runtime",
        "visual_query_entities_runtime", "visual_content_runtime", "visual_retrieval_runtime",
        "visual_provider_boundary_runtime", "provider_runtime", "quality_runtime",
        "runtime_bindings", "workflow_runtime", "subtitle_runtime", "youtube_comment_runtime",
    ]
    for name in modules:
        __import__(name)
    return f"Imported {len(modules)} supported factory modules"


def _test_environment():
    names = (
        "GEMINI_API_KEY", "GROQ_API_KEY",
        "UNSPLASH_ACCESS_KEY", "HF_TOKEN", "PEXELS_API_KEY",
    )
    configured = sum(1 for name in names if str(os.getenv(name) or "").strip())
    return f"Local environment loaded; {configured}/{len(names)} provider keys configured (no API calls made)"

