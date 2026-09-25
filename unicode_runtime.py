"""Unicode-safe text compatibility for multilingual Shorts production.

The factory supports English, Hindi and Telugu, but several historical helpers
used ASCII-only tokenisation. This module patches those helpers centrally so
validation, deduplication and visual query generation preserve non-Latin text.
It keeps the shared text normalization contracts used by multilingual production.
"""
from __future__ import annotations

import re
import unicodedata

_VERSION = "2026-09-17-v4"
_SPACE_RE = re.compile(r"\s+")


def unicode_words(value):
    """Return words while preserving base letters plus Unicode combining marks."""
    text = str(value or "").casefold()
    words = []
    current = []
    for char in text:
        category = unicodedata.category(char)
        if char.isalnum() or category.startswith("M"):
            current.append(char)
            continue
        if char in {"'", "’", "-", "/"} and current:
            current.append(char)
            continue
        if current:
            token = "".join(current).strip("'-/’")
            if token:
                words.append(token)
            current = []
    if current:
        token = "".join(current).strip("'-/’")
        if token:
            words.append(token)
    return words


def unicode_normalise(value):
    """Normalise text without deleting Devanagari, Telugu or other scripts."""
    text = str(value or "").replace("\u200b", " ")
    text = _SPACE_RE.sub(" ", text).strip()
    return text.casefold()


def install() -> bool:
    """Patch all known ASCII-only text helpers once."""
    if globals().get("_INSTALLED", False):
        return True

    patched = []
    try:
        import script_runtime
        script_runtime._words = unicode_words
        script_runtime._normalise = unicode_normalise
        patched.extend(["script_runtime._words", "script_runtime._normalise"])

        import quality_runtime
        quality_runtime._words = unicode_words
        quality_runtime._normalise = unicode_normalise
        patched.extend(["quality_runtime._words", "quality_runtime._normalise"])

        import story_ranker
        story_ranker._tokens = lambda value: {
            word for word in unicode_words(value)
            if len(word) > 2 and word not in {
                "the", "and", "for", "with", "from", "this", "that", "into", "after", "before",
                "over", "under", "what", "how", "why", "world", "news", "latest", "today", "just",
                "will", "says", "said", "new", "breaking", "report", "reports", "official", "update",
            }
        }
        patched.append("story_ranker._tokens")


        import script_guard_runtime as guard
        if guard.install():
            patched.append("script_guard_runtime")

        globals()["_INSTALLED"] = True
        globals()["UNICODE_RUNTIME_VERSION"] = _VERSION
        print("   [Unicode Runtime] Multilingual text contracts installed: " + ", ".join(patched), flush=True)
        return True
    except Exception as exc:
        print(f"   [Unicode Runtime] Installation failed: {type(exc).__name__}: {exc}", flush=True)
        return False
