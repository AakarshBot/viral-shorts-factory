"""Small runtime hardening layer for the Streamlit dashboard.

This module deliberately avoids changing the main production pipeline in-place.
It provides safe helpers that the dashboard can use while the larger bot remains
backwards compatible with the existing local CLI workflow.
"""

import sys
import traceback


def install_safe_exception_hook():
    """Install a headless-safe exception hook (never waits for console input)."""
    def safe_hook(exctype, value, tb):
        print("💥 UNCAUGHT EXCEPTION DETECTED:")
        print("!" * 60)
        traceback.print_exception(exctype, value, tb)
        print("!" * 60)

    sys.excepthook = safe_hook


def normalise_publish_mode(value):
    """Return only the two publish modes supported by the dashboard."""
    return "public" if str(value).strip().lower() == "public" else "private"
