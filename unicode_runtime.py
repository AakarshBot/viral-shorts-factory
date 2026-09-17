"""Unicode-safe text compatibility for multilingual Shorts production.

The factory supports English, Hindi and Telugu, but several historical helpers
used ASCII-only tokenisation. This module patches those helpers centrally so
validation, deduplication and visual query generation preserve non-Latin text.
It also hardens the legacy visual repeat-limit wrapper so it cannot blank the
primary entity that the strict visual runtime requires.
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


def _planner_tokens(text):
    """Tokenise visual queries without changing their original casing.

    Visual search has a stricter contract than generic text matching: the
    locked primary_entity must be emitted exactly as supplied. Generic Unicode
    helpers intentionally casefold for comparisons, but that behaviour must
    never leak into the actual visual search query.
    """
    text = str(text or "").replace("’", "'").replace("‘", "'")
    words = []
    current = []
    for char in text:
        category = unicodedata.category(char)
        if char.isalnum() or category.startswith("M"):
            current.append(char)
            continue
        if char in {"'", "-", "/"} and current:
            current.append(char)
            continue
        if current:
            token = "".join(current).strip("'-/")
            if token:
                words.append(token)
            current = []
    if current:
        token = "".join(current).strip("'-/")
        if token:
            words.append(token)
    return words


def _planner_key(token):
    return "".join(
        char
        for char in str(token or "").casefold()
        if char.isalnum() or unicodedata.category(char).startswith("M")
    )


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


def _safe_subject_limit_wrapper(module, original_builder):
    """Build a repeat-limited visual wrapper that never blanks primary_entity."""
    def limited_relevant_asset(original):
        def wrapped(bot, seg, category, used_urls, used_hashes, video_title=""):
            entity = str(seg.get("primary_entity", "") or "").strip()
            key = re.sub(r"\s+", " ", entity.casefold()).strip()
            chosen_seg = seg
            run_key = id(used_hashes)
            if key:
                with module._SUBJECT_LOCK:
                    run_counts = module._SUBJECT_RUNS.setdefault(run_key, module.defaultdict(int))
                    count = run_counts[key]
                    if count < 2:
                        run_counts[key] += 1
                    else:
                        chosen_seg = dict(seg)
                        intent = str(seg.get("visual_intent", "") or "").strip()
                        prompt = str(seg.get("specific_search_prompt", "") or "").strip()
                        scene_no = str(seg.get("scene_index") or seg.get("scene_number") or "").strip()
                        context = " ".join(x for x in (prompt, intent, f"scene {scene_no}" if scene_no else "") if x)
                        chosen_seg["specific_search_prompt"] = context or entity
            try:
                return original(bot, chosen_seg, category, used_urls, used_hashes, video_title)
            finally:
                with module._SUBJECT_LOCK:
                    if len(module._SUBJECT_RUNS) > 32:
                        module._SUBJECT_RUNS.pop(next(iter(module._SUBJECT_RUNS)), None)

        wrapped._subject_limit_bound = True
        return wrapped
    return limited_relevant_asset(original_builder)


def _patch_subject_limit_before_install():
    """Replace the legacy wrapper factory before it can create blank entities."""
    try:
        import visual_policy_runtime as policy
        if getattr(policy, "_unicode_safe_subject_policy", False):
            return

        original_builder = getattr(policy, "_subject_limit_wrapper", None)
        if not callable(original_builder):
            return

        def safe_builder(original):
            return _safe_subject_limit_wrapper(policy, original)

        policy._subject_limit_wrapper = safe_builder
        policy._unicode_safe_subject_policy = True
        print("   [Unicode Runtime] Visual repeat-limit wrapper hardened: primary_entity is always preserved.", flush=True)
    except Exception as exc:
        print(f"   [Unicode Runtime] Visual repeat-limit hardening unavailable: {type(exc).__name__}: {exc}", flush=True)


def install() -> bool:
    """Patch all known ASCII-only text helpers once."""
    if globals().get("_INSTALLED", False):
        return True

    patched = []
    try:
        _patch_subject_limit_before_install()

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
