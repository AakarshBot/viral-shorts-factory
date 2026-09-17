"""Persistent Test Phase execution ledger."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

HISTORY_FILENAME = "test_phase_history.jsonl"
MAX_TEXT = 6000


def _history_path(bot) -> Path:
    root = Path(getattr(bot, "ASSETS_DIR", Path.cwd()))
    root.mkdir(parents=True, exist_ok=True)
    return root / HISTORY_FILENAME


def _safe(value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"
    if isinstance(value, dict):
        return {str(k): _safe(v, depth + 1) for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [_safe(v, depth + 1) for v in list(value)[:80]]
    return str(value)[:MAX_TEXT]


def _read(bot, limit: int = 60) -> list[dict[str, Any]]:
    path = _history_path(bot)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-limit:]:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return rows


def record(bot, step: str, function: str, inputs: Dict[str, Any], status: str, output: Any = None, error: str = "") -> None:
    elapsed = inputs.pop("__elapsed_ms", 0) if isinstance(inputs, dict) else 0
    item = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "step": str(step),
        "function": str(function),
        "status": str(status),
        "elapsed_ms": int(max(0.0, float(elapsed or 0))),
        "inputs": _safe(inputs),
        "output": _safe(output),
        "error": str(error or ""),
    }
    path = _history_path(bot)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"   [Test History] Could not write ledger: {exc}", flush=True)


def _step_inputs(st, step: str) -> Dict[str, Any]:
    keys = {
        "topic": ("tp_format", "tp_language", "tp_cricket_scope", "tp_topic", "tp_category"),
        "script": ("tp_config", "tp_story", "tp_manual_story_title", "tp_manual_story_topic", "tp_manual_story_summary", "tp_manual_story_url"),
        "audio": ("tp_config", "tp_story", "tp_script"),
        "visuals": ("tp_config", "tp_story", "tp_visual_query", "tp_script"),
        "render": ("tp_config", "tp_script", "tp_visuals", "tp_audio"),
        "metadata": ("tp_config", "tp_script"),
        "upload": ("tp_upload_path", "tp_upload_title", "tp_upload_description", "tp_upload_comment", "tp_upload_visibility", "tp_real_upload_ack"),
    }.get(step, ())
    result = {key: st.session_state.get(key) for key in keys if key in st.session_state}
    for key in ("tp_script", "tp_visuals", "tp_audio"):
        if key not in result:
            continue
        value = result[key]
        if key == "tp_script":
            try:
                scenes = value.get("script", []) if isinstance(value, dict) else []
                result[key] = {
                    "scene_count": len(scenes),
                    "narration": "\n".join(str(scene.get("voiceover", "")) for scene in scenes if isinstance(scene, dict)),
                }
            except Exception:
                result[key] = str(value)
        elif key == "tp_visuals":
            result[key] = {"item_count": len(value) if isinstance(value, list) else 0}
        elif key == "tp_audio":
            try:
                paths, timings = value
                result[key] = {"track_count": len(paths or []), "timing_count": len(timings or [])}
            except Exception:
                result[key] = {"present": True}
    return result


def _function_for_step(step: str) -> str:
    return {
        "topic": "discover_three_candidates",
        "script": "bot.write_script",
        "audio": "bot.generate_voiceover_and_timestamps",
        "visuals": "bot.process_visuals_async",
        "render": "bot.compile_video",
        "metadata": "_build_clean_metadata + build_pinned_comment",
        "upload": "WorkflowController.upload_manual",
    }.get(step, "unknown")


def _state_output(st, step: str) -> Dict[str, Any]:
    state_keys = {
        "topic": ("tp_candidates", "tp_story"),
        "script": ("tp_script",),
        "audio": ("tp_audio",),
        "visuals": ("tp_visuals",),
        "render": ("tp_rendered",),
        "metadata": ("tp_metadata",),
        "upload": ("tp_upload_result",),
    }[step]
    state: Dict[str, Any] = {}
    for key in state_keys:
        value = st.session_state.get(key)
        if key == "tp_candidates":
            state[key] = {"count": len(value or []), "titles": [x.get("title", "") for x in (value or [])[:3] if isinstance(x, dict)]}
        elif key == "tp_story":
            state[key] = value.get("title", "") if isinstance(value, dict) else value
        elif key == "tp_script":
            scenes = value.get("script", []) if isinstance(value, dict) else []
            state[key] = {"scene_count": len(scenes), "narration": "\n".join(str(x.get("voiceover", "")) for x in scenes if isinstance(x, dict))}
        elif key == "tp_audio":
            try:
                paths, timings = value
                state[key] = {"track_count": len(paths or []), "timing_count": len(timings or [])}
            except Exception:
                state[key] = {"present": value is not None}
        elif key == "tp_visuals":
            state[key] = {"item_count": len(value or [])}
        elif key == "tp_rendered":
            state[key] = {"path": value, "exists": bool(value and os.path.isfile(value))}
        elif key == "tp_metadata":
            state[key] = value
        elif key == "tp_upload_result":
            state[key] = value
    return {"result": any(bool(v) for v in state.values()), "state": state}


def install_test_history_bridge(module, bot) -> None:
    if getattr(module, "_test_history_bridge_installed", False):
        return

    step_map = {
        "_topic_test": "topic",
        "_script_test": "script",
        "_audio_test": "audio",
        "_visual_test": "visuals",
        "_render_test": "render",
        "_metadata_test": "metadata",
        "_upload_test": "upload",
    }

    for name, step in step_map.items():
        original = getattr(module, name, None)
        if not callable(original):
            continue

        def make_wrapper(original_fn: Callable[..., Any], step_name: str):
            def wrapped(*args, **kwargs):
                started = time.perf_counter()
                inputs = _step_inputs(module.st, step_name)
                before = _state_output(module.st, step_name)
                captured_errors: list[str] = []
                original_error = getattr(module.st, "error")

                def capture_error(message, *a, **k):
                    captured_errors.append(str(message))
                    return original_error(message, *a, **k)

                module.st.error = capture_error
                try:
                    result = original_fn(*args, **kwargs)
                    after = _state_output(module.st, step_name)
                    elapsed = (time.perf_counter() - started) * 1000
                    changed = before != after
                    if captured_errors or changed:
                        inputs["__elapsed_ms"] = elapsed
                        status = "FAIL" if captured_errors else ("PASS" if after.get("result") else "NO_RESULT")
                        record(bot, step_name, _function_for_step(step_name), inputs, status, after, captured_errors[-1] if captured_errors else "")
                    return result
                except Exception as exc:
                    elapsed = (time.perf_counter() - started) * 1000
                    inputs["__elapsed_ms"] = elapsed
                    record(bot, step_name, _function_for_step(step_name), inputs, "FAIL", _state_output(module.st, step_name), f"{type(exc).__name__}: {exc}")
                    raise
                finally:
                    module.st.error = original_error
            return wrapped

        setattr(module, name, make_wrapper(original, step))
    module._test_history_bridge_installed = True


def render_test_history(bot, st) -> None:
    rows = list(reversed(_read(bot, 60)))
    with st.expander("📜 Test history — previous diagnostic runs", expanded=False):
        if not rows:
            st.caption("No test executions have been recorded yet.")
            return
        for index, row in enumerate(rows, 1):
            status = row.get("status", "?")
            icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⚠️"
            st.markdown(f"**{icon} {row.get('step', '')}** · {row.get('at', '')} · {row.get('function', '')} · {row.get('elapsed_ms', 0)} ms")
            if row.get("error"):
                st.error(row["error"])
            with st.expander(f"Details #{index}", expanded=False):
                st.json({"inputs": row.get("inputs", {}), "output": row.get("output", {})})
        st.caption(f"Ledger: {_history_path(bot)}")
