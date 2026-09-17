"""Fine-grained, persistent progress events for newsroom workflow diagnostics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def install_workflow_progress_bridge(workflow_module) -> None:
    controller_cls = getattr(workflow_module, "WorkflowController", None)
    if controller_cls is None or getattr(controller_cls, "_progress_events_patched", False):
        return

    original_update = controller_cls.update
    original_reset = controller_cls.reset
    original_snapshot = controller_cls.snapshot

    def reset(self):
        original_reset(self)
        with self._lock:
            self.state.progress_events = []

    def update(self, stage: str, percent: int, message: str):
        original_update(self, stage, percent, message)
        event = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stage": str(stage),
            "percent": max(0, min(100, int(percent))),
            "message": str(message or ""),
        }
        with self._lock:
            events = getattr(self.state, "progress_events", None)
            if not isinstance(events, list):
                events = []
                self.state.progress_events = events
            events.append(event)
            del events[:-40]

    def snapshot(self):
        snap = original_snapshot(self)
        with self._lock:
            snap["progress_events"] = list(getattr(self.state, "progress_events", []))
        return snap

    controller_cls.reset = reset
    controller_cls.update = update
    controller_cls.snapshot = snapshot
    controller_cls._progress_events_patched = True


def render_progress_events(snapshot: dict[str, Any], st) -> None:
    events = snapshot.get("progress_events") or []
    if not events:
        return
    with st.expander("Live production events", expanded=False):
        for event in events[-12:]:
            st.caption(f"{event.get('at', '')} · {event.get('stage', '').upper()} · {event.get('percent', 0)}% · {event.get('message', '')}")
