"""Free multi-source research pass for the selected story."""
from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urlparse


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _story_query(story: Dict[str, Any]) -> str:
    title = _clean(story.get("title") or story.get("topic"))
    return title[:220]


def _domain(url: Any) -> str:
    try:
        return urlparse(_clean(url)).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen_urls = set()
    seen_titles = set()
    output = []
    for item in items:
        url = _clean(item.get("url")).lower()
        title = _clean(item.get("title")).lower()
        if not url and not title:
            continue
        if url and url in seen_urls:
            continue
        if title and title in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)
        output.append(item)
    return output


def _distinct_domain_pack(items: List[Dict[str, Any]], max_sources: int) -> List[Dict[str, Any]]:
    """Prefer independent publishers before adding another result from the same domain."""
    selected: List[Dict[str, Any]] = []
    seen_domains = set()
    remainder: List[Dict[str, Any]] = []
    for item in items:
        domain = _domain(item.get("url"))
        if domain and domain not in seen_domains:
            selected.append(item)
            seen_domains.add(domain)
            if len(selected) >= max_sources:
                return selected
        else:
            remainder.append(item)
    for item in remainder:
        if len(selected) >= max_sources:
            break
        selected.append(item)
    return selected[:max_sources]


def collect_source_bundle(bot, story: Dict[str, Any], max_sources: int = 5) -> List[Dict[str, Any]]:
    query = _story_query(story)
    sources: List[Dict[str, Any]] = []

    original_url = _clean(story.get("url") or story.get("link"))
    if original_url:
        sources.append({
            "title": _clean(story.get("title")),
            "url": original_url,
            "snippet": _clean(story.get("summary") or story.get("description") or story.get("text")),
            "source": _clean(story.get("source") or story.get("publisher") or "Original story source"),
        })

    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=10))
        for item in results:
            sources.append({
                "title": _clean(item.get("title")),
                "url": _clean(item.get("href") or item.get("url")),
                "snippet": _clean(item.get("body") or item.get("description")),
                "source": _clean(item.get("domain") or item.get("source")),
            })
    except Exception as exc:
        print(f"   [Research] DDG source search unavailable: {exc}", flush=True)

    sources = _distinct_domain_pack(_dedupe(sources), max_sources=max_sources)
    return sources


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
        data["research_source_count"] = len(sources)
        data["research_distinct_domains"] = len({_domain(item.get("url")) for item in sources if _domain(item.get("url"))})
        data["research_synthesis_required"] = True
        data["research_bundle"] = format_source_brief(sources)
        instruction = (
            "\n\nMULTI-SOURCE EVIDENCE PACK — synthesize the strongest factual Short from the evidence below. "
            "Treat the selected story as the subject, not as the final script. Cross-check details across independent publishers and prefer details repeated or directly supported by multiple sources. "
            "Merge the strongest verified facts, useful context, numbers and consequences into one coherent story; do not mechanically paraphrase one article. "
            "When sources conflict, omit the disputed detail unless the conflict itself is the verified news point. "
            "Never invent facts, quotes, motives, predictions, statistics or causal links.\n\n"
            + data["research_bundle"]
        )
        data["text"] = _clean(data.get("text")) + instruction
        result = current(data, language_cfg, genre_key, conn, format_mode)
        if isinstance(result, dict):
            result["research_sources"] = sources
            result["research_source_count"] = len(sources)
            result["research_distinct_domains"] = data["research_distinct_domains"]
            result["research_synthesis_required"] = True
        return result

    researched_write_script._research_wrapped = True
    bot.write_script = researched_write_script
    run_robot.__globals__["write_script"] = researched_write_script
    bot._research_pipeline_patch_installed = True
    return bot
