"""Compatibility wrapper for the evidence-first visual retrieval planner.

The legacy diagnostics import private planner helpers directly.  ``import *``
intentionally omits underscore-prefixed names, so this wrapper re-exports the
planner's private helpers explicitly and keeps the two legacy helper names
used by the offline diagnostics.
"""

import visual_retrieval_planner as _planner
from visual_retrieval_planner import *

# Re-export every single-underscore planner helper so future private-helper
# compatibility checks do not fail one attribute at a time.
for _name in dir(_planner):
    if _name.startswith("_") and not _name.startswith("__"):
        globals()[_name] = getattr(_planner, _name)

# Backward-compatible aliases expected by the offline strategy diagnostics.
_normalise_query = _planner._normalise


def _add_unique(values, value, *parts):
    """Append a normalised query once and report whether it was added."""
    candidate = _planner._normalise(" ".join(str(part) for part in (value,) + parts if str(part).strip()))
    if not candidate:
        return False
    existing = {str(item).strip().lower() for item in values}
    if candidate.lower() in existing:
        return False
    values.append(candidate)
    return True
