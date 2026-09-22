"""Manual YouTube channel intelligence and factory learning dashboard."""
from __future__ import annotations

import json
import re
import sqlite3
from statistics import mean
from typing import Any, Dict, List, Tuple


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _feature_from_script(script_json: str, key: str, default: str = "Unknown") -> str:
    try:
        data = json.loads(script_json or "{}") if isinstance(script_json, str) else (script_json or {})
    except Exception:
        return default
    if not isinstance(data, dict):
        return default
    value = data.get(key)
    if value not in (None, "", []):
        return str(value).strip() or default
    return default


def _title_style(title: str) -> str:
    text = str(title or "").strip()
    if re.match(r"^\d+\b", text):
        return "Number-led"
    if "?" in text:
        return "Question"
    if re.search(r"\b[A-Z]{2,}\b", text):
        return "Keyword emphasis"
    if ":" in text:
        return "Colon-led"
    return "Statement"


def _pace_bucket(value) -> str:
    pace = _num(value)
    if pace is None:
        return "Unknown"
    if pace < 2.0:
        return "Slow (<2 w/s)"
    if pace < 3.0:
        return "Moderate (2–3 w/s)"
    return "Fast (3+ w/s)"


def _scene_features(script_json: str) -> Tuple[int | None, float | None, str, str]:
    try:
        data = json.loads(script_json or "{}") if isinstance(script_json, str) else (script_json or {})
    except Exception:
        data = {}
    scenes = data.get("script", []) if isinstance(data, dict) else []
    scenes = [scene for scene in scenes if isinstance(scene, dict)]
    if not scenes:
        return None, None, "Unknown", "Unknown"
    words = 0
    visual_types = []
    ending = str(scenes[-1].get("ending_type") or scenes[-1].get("scene_role") or "").strip()
    for scene in scenes:
        words += len(re.findall(r"\b[\w’'-]+\b", str(scene.get("voiceover", ""))))
        visual = scene.get("visual_type") or scene.get("visual_intent") or scene.get("asset_type")
        if visual:
            visual_types.append(str(visual).strip())
    duration = _num(data.get("audio_duration")) or _num(data.get("duration"))
    pace = (words / duration) if duration and duration > 0 else None
    visual_mix = ", ".join(dict.fromkeys(visual_types)) if visual_types else "Unknown"
    return len(scenes), pace, visual_mix, ending or "Unknown"


def _rows(conn) -> List[Dict[str, Any]]:
    columns = [
        "topic", "genre", "views", "avg_view_percentage", "stayed_to_watch", "avg_view_duration", "title_ctr", "likes", "comments",
        "title_used", "hook_type", "hook_style_used", "structure_used", "persona_used",
        "ai_image_ratio", "voice_gender", "format_used", "language_used", "trend_keyword", "script_json",
        "status", "video_id", "date_used",
    ]
    sql = "SELECT " + ", ".join(columns) + " FROM vault WHERE video_id IS NOT NULL AND video_id NOT IN ('', 'PENDING_QC', 'READY_FOR_UPLOAD', 'REJECTED', 'FAILED') AND status NOT IN ('REJECTED', 'FAILED', 'PENDING_QC', 'READY_FOR_UPLOAD')"
    out = []
    for row in conn.execute(sql).fetchall():
        item = dict(zip(columns, row))
        scene_count, pace, visual_mix, ending = _scene_features(item.get("script_json"))
        item["scene_count"] = scene_count
        item["pace_bucket"] = _pace_bucket(pace)
        item["visual_mix"] = visual_mix
        item["ending"] = ending
        item["title_style"] = _title_style(item.get("title_used"))
        item["angle"] = _feature_from_script(item.get("script_json"), "angle")
        item["hook_pattern"] = str(item.get("hook_style_used") or item.get("hook_type") or "Unknown").strip() or "Unknown"
        item["topic_label"] = str(item.get("topic") or item.get("genre") or "Unknown").strip()
        out.append(item)
    return out


def _group_summary(rows: List[Dict[str, Any]], key: str, minimum: int = 2) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        label = str(row.get(key) or "Unknown").strip() or "Unknown"
        buckets.setdefault(label, []).append(row)
    result = []
    for label, bucket in buckets.items():
        if len(bucket) < minimum:
            continue
        views = [x for x in (_num(r.get("views")) for r in bucket) if x is not None]
        retention = [x for x in (_num(r.get("avg_view_percentage")) for r in bucket) if x is not None]
        stayed = [x for x in (_num(r.get("stayed_to_watch")) for r in bucket) if x is not None]
        duration = [x for x in (_num(r.get("avg_view_duration")) for r in bucket) if x is not None]
        ctr = [x for x in (_num(r.get("title_ctr")) for r in bucket) if x is not None]
        like_rate = [
            (likes / views) * 100.0
            for likes, views in (
                (_num(r.get("likes")), _num(r.get("views"))) for r in bucket
            )
            if likes is not None and views is not None and views > 0
        ]
        comment_rate = [
            (comments / views) * 100.0
            for comments, views in (
                (_num(r.get("comments")), _num(r.get("views"))) for r in bucket
            )
            if comments is not None and views is not None and views > 0
        ]
        result.append({
            "Pattern": label,
            "Videos": len(bucket),
            "Avg views": round(mean(views)) if views else None,
            "Avg retention %": round(mean(retention), 1) if retention else None,
            "Avg stayed to watch %": round(mean(stayed), 1) if stayed else None,
            "Avg view duration": round(mean(duration), 1) if duration else None,
            "Avg CTR %": round(mean(ctr), 2) if ctr else None,
            "Avg like rate %": round(mean(like_rate), 2) if like_rate else None,
            "Avg comment rate %": round(mean(comment_rate), 3) if comment_rate else None,
        })
    result.sort(key=lambda x: ((x.get("Avg retention %") is not None, x.get("Avg retention %") or -1), (x.get("Avg views") or -1)), reverse=True)
    return result


def build_intelligence(conn) -> Dict[str, Any]:
    rows = _rows(conn)
    dimensions = {
        "Topic / genre": "genre",
        "Angle": "angle",
        "Hook": "hook_pattern",
        "Structure": "structure_used",
        "Duration / pace": "pace_bucket",
        "Scene count": "scene_count",
        "Visual mix": "visual_mix",
        "Voice / persona": "persona_used",
        "Title style": "title_style",
        "Ending": "ending",
    }
    tables = {label: _group_summary(rows, key) for label, key in dimensions.items()}
    return {
        "videos": len(rows),
        "reported": sum(1 for r in rows if _num(r.get("views")) is not None),
        "retention_ready": sum(1 for r in rows if _num(r.get("avg_view_percentage")) is not None),
        "tables": tables,
    }


def install_channel_intelligence_dialog() -> bool:
    """Enhance the existing app dialog by wrapping Streamlit's dialog decorator."""
    try:
        import streamlit as st
    except Exception:
        return False
    if getattr(st.dialog, "_channel_intelligence_wrapped", False):
        return True

    original_dialog = st.dialog

    def dialog_wrapper(title, *args, **kwargs):
        decorator = original_dialog(title, *args, **kwargs)
        if str(title) != "📊 YouTube Channel Intelligence":
            return decorator

        def decorate(fn):
            original_fn = decorator(fn)

            def wrapped_fn(*fn_args, **fn_kwargs):
                st.caption("Manual intelligence only. No analytics sync runs during production, and nothing here publishes a video.")
                _render_intelligence(st)
                return original_fn(*fn_args, **fn_kwargs)

            return wrapped_fn

        return decorate

    dialog_wrapper._channel_intelligence_wrapped = True
    st.dialog = dialog_wrapper
    return True


def _render_intelligence(st) -> None:
    import ultimate_bot
    from db_architecture import migrate_vault

    conn = None
    try:
        conn = sqlite3.connect(ultimate_bot.DB_PATH)
        migrate_vault(conn)
        report = build_intelligence(conn)
    except Exception as exc:
        st.error(f"Factory learning database unavailable: {type(exc).__name__}: {exc}")
        return
    finally:
        if conn is not None:
            conn.close()

    st.markdown("#### Factory learning")
    c1, c2, c3 = st.columns(3)
    c1.metric("Published factory videos", report["videos"])
    c2.metric("Videos with view data", report["reported"])
    c3.metric("Videos with retention", report["retention_ready"])

    if st.button("↻ Refresh factory learning", key="refresh_factory_learning"):
        try:
            from learning_runtime import sync_factory_analytics
            conn = sqlite3.connect(ultimate_bot.DB_PATH)
            migrate_vault(conn)
            result = sync_factory_analytics(ultimate_bot, conn)
            conn.close()
            st.success(
                f"Refreshed {result['updated']} videos; retention available for "
                f"{result['retention_ready']}; stayed-to-watch available for "
                f"{result.get('stayed_to_watch_ready', 0)}."
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Learning refresh failed: {type(exc).__name__}: {exc}")

    for label, table in report["tables"].items():
        if not table:
            continue
        st.markdown(f"**{label}**")
        st.dataframe(table[:8], width="stretch", hide_index=True)
        st.caption("Patterns require at least two factory videos. Retention, stayed-to-watch, category/format/language fit and prior-topic similarity can influence selection; hook family, structure, pace and engagement patterns can now inform or validate the factory learning layer.")


__all__ = ["build_intelligence", "install_channel_intelligence_dialog"]
