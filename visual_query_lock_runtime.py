"""Compatibility shim for the visual query lock.

The old lock conflated the factual primary entity with the exact search string and
then forced one query, one source and one QA decision. That made retrieval brittle.
The current contract is stricter in the right place: the factual subject remains
locked, while the retrieval planner may derive a small, evidence-grounded query
ladder from that subject and the scene context.
"""
from __future__ import annotations

_VERSION = "2026-09-17-v14-semantic-subject-bounded-retrieval"
_INSTALLED = False


def install() -> bool:
    """Install compatibility metadata without overriding the real retriever.

    visual_runtime already implements bounded multi-query and multi-source
    retrieval. Older versions of this shim replaced that logic with a single
    exact query/source, which is precisely what caused false terminal failures.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        import visual_runtime as runtime
        runtime._visual_query_lock_version = _VERSION
    except Exception as exc:
        print(f"   [Visual Query Lock] Compatibility install unavailable: {exc}", flush=True)
        return False
    _INSTALLED = True
    print(
        f"   [Visual Query Lock] Compatibility mode | version={_VERSION} | "
        "subject locked, bounded query/source fallback enabled",
        flush=True,
    )
    return True
