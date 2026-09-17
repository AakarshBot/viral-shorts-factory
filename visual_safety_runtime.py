"""Central visual resource budgets and bounded fetch-worker safety.

This module is deliberately a small compatibility layer around the existing
visual runtime. It keeps one authoritative set of operational limits while
avoiding unbounded growth of daemon threads when a third-party fetcher hangs.
"""
from __future__ import annotations

import os
import threading
import traceback


VISUAL_FETCH_TIMEOUT_SECONDS = max(1, int(os.getenv("VISUAL_FETCH_TIMEOUT_SECONDS", "15")))
VISUAL_MAX_SEARCH_QUERIES = min(6, max(1, int(os.getenv("VISUAL_MAX_SEARCH_QUERIES", "6"))))
VISUAL_MAX_VERIFICATION_ATTEMPTS = min(6, max(1, int(os.getenv("VISUAL_MAX_VERIFICATION_ATTEMPTS", "4"))))
GEMINI_VISUAL_MAX_REQUESTS = min(12, max(1, int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS_PER_RUN", "8"))))
GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE = min(6, max(1, int(os.getenv("GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE", "4"))))

# A timeout cannot kill an arbitrary third-party Python function. Keep the
# existing daemon-thread approach, but cap the number of abandoned workers so
# one stuck provider cannot create an unbounded thread pile-up.
_FETCH_WORKER_SLOTS = threading.BoundedSemaphore(2)
_INSTALLED = False


def _bounded_fetcher(fetcher, args, source, query, timeout=VISUAL_FETCH_TIMEOUT_SECONDS):
    if not callable(fetcher):
        return None
    if not _FETCH_WORKER_SLOTS.acquire(timeout=max(1, int(timeout))):
        print(
            f"   [Visual Source] {source} | fetch worker capacity exhausted; skipped query='{query}'",
            flush=True,
        )
        return None

    result = {"value": None, "error": None}

    def worker():
        try:
            result["value"] = fetcher(*args)
        except Exception as exc:
            result["error"] = exc
        finally:
            _FETCH_WORKER_SLOTS.release()

    thread = threading.Thread(
        target=worker,
        name=f"visual-{str(source).lower()}-fetch",
        daemon=True,
    )
    thread.start()
    thread.join(max(1, int(timeout)))

    if thread.is_alive():
        print(
            f"   [Visual Source] {source} | caller deadline exceeded after {int(timeout)}s; "
            f"underlying worker remains bounded in the background | query='{query}'",
            flush=True,
        )
        return None
    if result["error"] is not None:
        print(
            f"   [Visual Source] {source} | failed: {result['error']} | query='{query}'",
            flush=True,
        )
        return None
    return result["value"]


def install() -> bool:
    """Install the central budgets and bounded fetch wrapper once."""
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        import visual_runtime
        import visual_qa_runtime

        visual_runtime.VISUAL_FETCH_TIMEOUT_SECONDS = VISUAL_FETCH_TIMEOUT_SECONDS
        visual_runtime.VISUAL_MAX_SEARCH_QUERIES = VISUAL_MAX_SEARCH_QUERIES
        visual_runtime.VISUAL_MAX_VERIFICATION_ATTEMPTS = VISUAL_MAX_VERIFICATION_ATTEMPTS
        visual_qa_runtime.GEMINI_VISUAL_MAX_REQUESTS = GEMINI_VISUAL_MAX_REQUESTS
        visual_qa_runtime.GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE = GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE

        visual_runtime._call_fetcher_with_timeout = _bounded_fetcher
        visual_runtime.VISUAL_BUDGET_RUNTIME_VERSION = "2026-09-17-v1"
        visual_qa_runtime.VISUAL_BUDGET_RUNTIME_VERSION = "2026-09-17-v1"

        # Surface binding mistakes immediately. This is diagnostic-only: a
        # warning never blocks startup, but it makes a future patch-order/signature
        # regression visible before a production render reaches that function.
        try:
            from runtime_hardener import validate_runtime_contracts
            import ultimate_bot
            contract_errors = validate_runtime_contracts(ultimate_bot)
            if contract_errors:
                print("   [Runtime Hardener] Contract warnings: " + " | ".join(contract_errors), flush=True)
            else:
                print("   [Runtime Hardener] Renderer/runtime contracts passed.", flush=True)
        except Exception as contract_exc:
            print(
                f"   [Runtime Hardener] Contract check unavailable: {type(contract_exc).__name__}: {contract_exc}",
                flush=True,
            )

        _INSTALLED = True
        print(
            "   [Visual Safety] Central budgets + bounded fetch workers installed: "
            f"searches={VISUAL_MAX_SEARCH_QUERIES}, verification={VISUAL_MAX_VERIFICATION_ATTEMPTS}, "
            f"Gemini/run={GEMINI_VISUAL_MAX_REQUESTS}, Gemini/scene={GEMINI_VISUAL_MAX_REQUESTS_PER_SCENE}, "
            f"fetch_deadline={VISUAL_FETCH_TIMEOUT_SECONDS}s, worker_slots=2.",
            flush=True,
        )
        return True
    except Exception as exc:
        print(
            f"   [Visual Safety] Installation failed: {type(exc).__name__}: {exc}",
            flush=True,
        )
        traceback.print_exc()
        return False
