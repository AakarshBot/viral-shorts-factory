"""Fast story deduplication for Streamlit/Cloud execution.

The previous SentenceTransformer-based deduplication could stall the Streamlit
worker after loading the model on CPU. Story pools are small, so deterministic
headline similarity is sufficient and avoids downloading/running a transformer
model during every production run.
"""
import re
import difflib


def _tokens(value):
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "into", "after",
        "before", "over", "under", "what", "how", "why", "world", "news",
        "latest", "today", "just", "new", "says", "said", "will", "has", "have",
    }
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
    return {x for x in text.split() if len(x) > 2 and x not in stop}


def _similar(a, b):
    """Return a conservative 0..1 similarity for two headlines."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return difflib.SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    sequence = difflib.SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()
    return max(jaccard, sequence * 0.9)


def fast_duplicate_filter(stories, vault_topics, threshold=0.82):
    """Remove near-duplicate stories without a transformer model."""
    if not stories:
        return stories

    refs = [str(x).strip() for x in (vault_topics or []) if str(x).strip()]
    kept = []
    dropped = 0

    for story in stories:
        title = str(story.get("title", "")).strip()
        duplicate = any(_similar(title, ref) >= threshold for ref in refs)
        if not duplicate:
            duplicate = any(_similar(title, str(other.get("title", ""))) >= threshold for other in kept)

        if duplicate:
            dropped += 1
        else:
            kept.append(story)

    if dropped:
        print(f"   [Fast Dedup] Removed {dropped} near-duplicate stories.")
    print(f"   [Fast Dedup] Checked {len(stories)} stories without transformer inference.")
    return kept


def patch_semantic_dedup():
    """Replace factory_runtime's transformer filter before its gather wrapper runs."""
    import factory_runtime
    factory_runtime.semantic_duplicate_filter = fast_duplicate_filter
    # Prevent accidental direct calls from trying to initialise the model.
    factory_runtime._get_semantic_model = lambda: None
    print("   [Fast Dedup] Transformer semantic model disabled for production runs.")
