"""Streamlit entrypoint for the Viral Shorts Factory dashboard."""
from __future__ import annotations

import runpy


if __name__ == "__main__":
    runpy.run_module("app_legacy", run_name="__main__")
