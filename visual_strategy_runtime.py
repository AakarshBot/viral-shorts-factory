"""Compatibility wrapper for the evidence-first visual retrieval planner."""
from visual_retrieval_planner import *
from visual_retrieval_planner import _clean, _normalise

# Backward-compatible name expected by the offline strategy diagnostics.
_normalise_query = _normalise
