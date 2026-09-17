"""Unicode-safe text compatibility for multilingual Shorts production.

The factory supports English, Hindi and Telugu, but several historical helpers
used ASCII-only tokenisation.  This module patches those helpers centrally so
validation, deduplication and visual query generation preserve non-Latin text.
It is deliberately deterministic and has no network side effects.
"""
from __future__ import annotations

import re

_VERSION = "2026-09-17-v1"
_WORD_RE = re.compile(r"[^\W_]+(?:['’/-][^\W_]+)*", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def unicode_words(value):
    """Return Unicode-aware words while keeping Latin apostrophes/hyphens."""
    return _WORD_RE.findall(str(value or "").casefold())


def unicode_normalise(value):
    """Normalise text without deleting Devanagari, Telugu or other scripts."""
    text = str(value or "").replace("\u200b", " ")
    text = _SPACE_RE.sub(" ", text).strip()
    return text.casefold()


def _planner_tokens(text):
    return _WORD_RE.findall(str(text or "").replace("’", "'").replace("‘", "'").strip())


def _planner_key(token):
    return re.sub(r"[^\w]", "", str(token or "").casefold())


def _planner_normalise(module, text):
    words = []
    seen = set()
    for raw in _planner_tokens(text):
        key = _planner_key(raw)
        if not key or key in module.NOISE or key in {"none", "unknown", "na"} or key in seen:
            continue
        seen.add(key)
        words.append(raw)
    return " ".join(words[:module.MAX_QUERY_WORDS])


def _script_guard_sentences(module, text):
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return [
        re.sub(r"\s+", " ", sentence).strip(" -")
        for sentence in re.split(r"(?<=[.!?])\s+", cleaned)
        if len(unicode_words(sentence)) >= 5 and not module.looks_like_instructional_narration(sentence)
    ]


def _story_key(text):
    return re.sub(r"\W+", " ", str(text or "").casefold(), flags=re.UNICODE).strip()


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
            if len(word) > 2 and word not in getattr(story_ranker, "STOPWORDS", set())
        }
        patched.append("story_ranker._tokens")

        import semantic_runtime
        semantic_runtime._tokens = lambda value: {
            word for word in unicode_words(value)
            if len(word) > 2 and word not in {
                "the", "and", "for", "with", "from", "this", "that", "into", "after",
                "before", "over", "under", "what", "how", "why", "world", "news",
                "latest", "today", "just", "new", "says", "said", "will", "has", "have",
            }
        }
        patched.append("semantic_runtime._tokens")

        import workflow_runtime
        workflow_runtime._token_set = lambda value: {
            word for word in unicode_words(value)
            if len(word) > 2 and word not in {
                "the", "and", "for", "with", "from", "this", "that", "into", "after",
                "before", "over", "under", "what", "how", "why", "world", "news",
                "latest", "today", "just", "will", "says", "said", "new", "breaking",
            }
        }
        workflow_runtime._story_key = lambda story: re.sub(
            r"\W+", " ",
            f"{str(story.get('title') or '').casefold()} {str(story.get('url') or story.get('link') or '').casefold()}",
            flags=re.UNICODE,
        ).strip()
        patched.extend(["workflow_runtime._token_set", "workflow_runtime._story_key"])

        import visual_retrieval_planner as planner
        planner._tokens = _planner_tokens
        planner._key = _planner_key
        planner._normalise = lambda text: _planner_normalise(planner, text)
        patched.extend(["visual_retrieval_planner._tokens", "visual_retrieval_planner._key", "visual_retrieval_planner._normalise"])

        import visual_strategy_runtime as visual_strategy
        visual_strategy._normalise_query = planner._normalise
        patched.append("visual_strategy_runtime._normalise_query")

        import script_guard_runtime as guard
        guard._source_sentences = lambda text: _script_guard_sentences(guard, text)
        patched.append("script_guard_runtime._source_sentences")

        globals()["_INSTALLED"] = True
        globals()["UNICODE_RUNTIME_VERSION"] = _VERSION
        print("   [Unicode Runtime] Multilingual text contracts installed: " + ", ".join(patched), flush=True)
        return True
    except Exception as exc:
        print(f"   [Unicode Runtime] Installation failed: {type(exc).__name__}: {exc}", flush=True)
        return False
