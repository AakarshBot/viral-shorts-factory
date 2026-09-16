"""Free multi-source research pass for the selected story."""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _story_query(story: Dict[str, Any]) -> str:
    title = _clean(story.get("title") or story.get("topic"))
    return title[:220]


def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    output = []
    for item in items:
        url = _clean(item.get("url")).lower()
        title = _clean(item.get("title")).lower()
        key = url or title
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def collect_source_bundle(bot, story: Dict[str, Any], max_sources: int = 5) -> List[Dict[str, Any]]:
    query = _story_query(story)
    sources: List[Dict[str, Any]] = []

    original_url = _clean(story.get("url") or story.get("link"))
    if original_url:
        sources.append({
            "title": _clean(story.get("title")),
            "url": original_url,
            "snippet": _clean(story.get("summary") or story.get("description")),
            "source": _clean(story.get("source") or story.get("publisher") or "Original story source"),
        })

    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=8))
        for item in results:
            sources.append({
                "title": _clean(item.get("title")),
                "url": _clean(item.get("href") or item.get("url")),
                "snippet": _clean(item.get("body") or item.get("description")),
                "source": _clean(item.get("domain") or item.get("source")),
            })
    except Exception as exc:
        print(f"   [Research] DDG source search unavailable: {exc}", flush=True)

    sources = _dedupe(sources)
    return sources[:max_sources]


def format_source_brief(sources: List[Dict[str, Any]]) -> str:
    rows = []
    for index, source in enumerate(sources, 1):
        rows.append(
            f"SOURCE {index}\n"
            f"Publisher: {_clean(source.get('source'))}\n"
            f"Headline: {_clean(source.get('title'))}\n"
            f"URL: {_clean(source.get('url'))}\n"
            f"Reported detail: {_clean(source.get('snippet'))}"
        )
    return "\n\n".join(rows)


def patch_research_pipeline(bot):
    if getattr(bot, "_research_pipeline_patch_installed", False):
        return bot

    current = getattr(bot, "write_script", None)
    run_robot = getattr(bot, "run_robot", None)
    if not callable(current) or run_robot is None or not hasattr(run_robot, "__globals__"):
        return bot

    if getattr(current, "_research_wrapped", False):
        bot._research_pipeline_patch_installed = True
        return bot

    def researched_write_script(story_data, language_cfg, genre_key, conn, format_mode):
        data = dict(story_data or {})
        query = _story_query(data)
        print(f"   [Research] Building multi-source evidence pack for: {query[:100]}", flush=True)
        sources = collect_source_bundle(bot, data, max_sources=5)
        data["research_sources"] = sources
        data["research_bundle"] = format_source_brief(sources)
        instruction = (
            "\n\nMULTI-SOURCE EVIDENCE PACK — use this only to corroborate and enrich the selected story. "
            "Prefer facts supported by more than one source. Do not invent facts, quotes, motives or predictions. "
            "Ignore conflicting or unsupported claims unless the conflict itself is the verified news point.\n\n"
            + data["research_bundle"]
        )
        data["text"] = _clean(data.get("text")) + instruction
        result = current(data, language_cfg, genre_key, conn, format_mode)
        if isinstance(result, dict):
            result["research_sources"] = sources
        return result

    researched_write_script._research_wrapped = True
    bot.write_script = researched_write_script
    run_robot.__globals__["write_script"] = researched_write_script
    bot._research_pipeline_patch_installed = True
    return bot
