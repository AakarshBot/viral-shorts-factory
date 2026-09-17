"""Free multi-source research pass for the selected story."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
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
    """Prefer distinct publishers/domains before adding another result from one domain."""
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


def _prepare_primary_writer_data(story_data: Dict[str, Any], format_mode: str) -> Dict[str, Any]:
    """Adapt Cricket's single-story evidence to the legacy writer without losing facts.

    ``ultimate_bot.write_script`` predates the dedicated Cricket mode and treats any
    non-regular/non-trending/non-tech mode as a Top-5 payload whose ``text`` must be
    a JSON list. Cricket supplies one selected story with ordinary article text, so
    direct JSON decoding otherwise collapses the writer input to an empty heading.
    """
    data = dict(story_data or {})
    if str(format_mode or "").lower() != "cricket":
        return data

    raw_text = _clean(data.get("text"))
    if not raw_text:
        raw_text = _clean(data.get("summary") or data.get("description") or data.get("title"))
    title = _clean(data.get("title") or data.get("topic") or "Selected cricket story")
    research_bundle = _clean(data.get("research_bundle"))
    evidence = raw_text
    if research_bundle:
        evidence = f"{evidence}\n\n{research_bundle}".strip()

    data["text"] = json.dumps([{"title": title, "text": evidence}])
    print(
        f"   [Research] Cricket writer adapter preserved selected-story evidence ({len(evidence)} chars).",
        flush=True,
    )
    return data


def _validate_provider_script(result: Any, story_data: Dict[str, Any], format_mode: str, provider_name: str):
    """Run every fallback through the same canonical acceptance gate."""
    if not isinstance(result, dict) or not isinstance(result.get("script"), list):
        return None
    try:
        from script_runtime import clean_script_data, validate_content_density
        cleaned, diagnostics = clean_script_data(result, story_data, format_mode)
        valid, reason = validate_content_density(cleaned, story_data, format_mode)
        if not valid:
            print(f"   [{provider_name}] Response rejected by script validation: {reason}", flush=True)
            return None
        cleaned["provider_used"] = provider_name
        cleaned["provider_fallback"] = True
        cleaned["provider_diagnostics"] = diagnostics
        return cleaned
    except Exception as exc:
        print(f"   [{provider_name}] Canonical validation unavailable: {type(exc).__name__}: {exc}", flush=True)
        return None


def _openrouter_script_fallback(story_data: Dict[str, Any], language_cfg: Dict[str, Any], genre_key: str, format_mode: str):
    """Ask OpenRouter's free router for a script only after the primary writer fails."""
    api_key = _clean(os.getenv("OPENROUTER_API_KEY"))
    if not api_key:
        return None

    source_text = _clean(story_data.get("text"))
    if not source_text:
        source_text = _clean(story_data.get("summary") or story_data.get("description") or story_data.get("title"))
    if not source_text:
        return None

    language_instruction = _clean((language_cfg or {}).get("script_instruction"))
    scene_count = "exactly 7" if str(format_mode).lower() == "top5" else "5 to 8"
    system_prompt = (
        "You are a factual YouTube Shorts script writer. Return ONLY a valid JSON object. "
        "Use only facts present in the supplied evidence. Do not invent quotes, numbers, motives, predictions, "
        "causal links, or opinions. No prompt text, provider messages, markdown fences, or explanations. "
        f"Write {scene_count} scenes. Each scene voiceover must contain 8 to 30 natural spoken words. "
        "Every scene must be useful factual narration. The first scene must begin with the core factual development. "
        "Do not include subscribe/like/follow requests in voiceover. "
        "Return keys: step_1_headline, step_2_data_points, step_3_critique, step_4_metadata, titles, "
        "recommended_title_index, seo_description, tags, pinned_comment, hook_type, hook_style_used, script. "
        "Each script scene must contain voiceover, primary_entity, visual_intent, specific_search_prompt, "
        "sport_or_topic_category. " + language_instruction
    )
    payload = {
        "model": "openrouter/free",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "EVIDENCE:\n" + source_text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/AakarshBot/viral-shorts-factory",
            "X-Title": "Viral Shorts Factory",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = json.loads(response.read().decode("utf-8"))
        raw = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(raw, dict):
            result = raw
        else:
            text = str(raw or "").strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
            result = json.loads(text)
        return _validate_provider_script(result, story_data, format_mode, "openrouter/free")
    except urllib.error.HTTPError as exc:
        print(f"   [OpenRouter] HTTP {exc.code}; falling through safely.", flush=True)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        print(f"   [OpenRouter] Request failed: {type(exc).__name__}; falling through safely.", flush=True)
    except Exception as exc:
        print(f"   [OpenRouter] Unexpected failure: {type(exc).__name__}; falling through safely.", flush=True)
    return None


def _ollama_script_fallback(story_data: Dict[str, Any], language_cfg: Dict[str, Any], genre_key: str, format_mode: str):
    """Use an optional local Ollama model as the offline/local provider fallback.

    Ollama is intentionally optional: if localhost is unavailable, the pipeline
    immediately continues to the deterministic source-only fallback.
    """
    base_url = _clean(os.getenv("OLLAMA_BASE_URL")) or "http://localhost:11434"
    model = _clean(os.getenv("OLLAMA_SCRIPT_MODEL")) or "gpt-oss:20b"
    source_text = _clean(story_data.get("text"))
    if not source_text:
        source_text = _clean(story_data.get("summary") or story_data.get("description") or story_data.get("title"))
    if not source_text:
        return None

    language_instruction = _clean((language_cfg or {}).get("script_instruction"))
    scene_count = "exactly 7" if str(format_mode).lower() == "top5" else "5 to 8"
    system_prompt = (
        "You are a factual YouTube Shorts script writer. Return ONLY a valid JSON object. "
        "Use only facts present in the supplied evidence. Never invent quotes, numbers, motives, predictions, "
        "causal links, or opinions. Never output provider errors, prompt instructions, markdown fences, or explanations. "
        f"Write {scene_count} scenes. Each scene voiceover must contain 8 to 30 natural spoken words. "
        "Every scene must contain useful factual narration, with the first scene stating the core development. "
        "Do not include subscribe, like, or follow requests in voiceover. "
        "Return keys: step_1_headline, step_2_data_points, step_3_critique, step_4_metadata, titles, "
        "recommended_title_index, seo_description, tags, pinned_comment, hook_type, hook_style_used, script. "
        "Each script scene must contain voiceover, primary_entity, visual_intent, specific_search_prompt, "
        "sport_or_topic_category. " + language_instruction
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "EVIDENCE:\n" + source_text},
        ],
        "stream": False,
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
        raw = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(raw, dict):
            result = raw
        else:
            text = str(raw or "").strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
            result = json.loads(text)
        return _validate_provider_script(result, story_data, format_mode, f"ollama/{model}")
    except urllib.error.HTTPError as exc:
        print(f"   [Ollama] HTTP {exc.code}; falling through safely.", flush=True)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        print(f"   [Ollama] Local service unavailable or invalid response: {type(exc).__name__}; falling through safely.", flush=True)
    except Exception as exc:
        print(f"   [Ollama] Unexpected failure: {type(exc).__name__}; falling through safely.", flush=True)
    return None


def patch_research_pipeline(bot):
    """Install a deterministic research -> content-density wrapper pair."""
    current = getattr(bot, "write_script", None)
    run_robot = getattr(bot, "run_robot", None)
    if not callable(current) or run_robot is None or not hasattr(run_robot, "__globals__"):
        return bot

    # The desired externally visible chain is:
    #   content-density wrapper -> research wrapper -> existing writer
    if getattr(current, "_content_dense_bound", False) and getattr(current, "_research_layer_live", False):
        bot._research_pipeline_patch_installed = True
        run_robot.__globals__["write_script"] = current
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
        data["research_instruction"] = (
            "MULTI-SOURCE EVIDENCE PACK — synthesize the strongest factual Short from the evidence below. "
            "Treat the selected story as the subject, not as the final script. Cross-check details across distinct publishers/domains and prefer details repeated or directly supported by multiple sources. "
            "Merge the strongest verified facts, useful context, numbers and consequences into one coherent story; do not mechanically paraphrase one article. "
            "When sources conflict, omit the disputed detail unless the conflict itself is the verified news point. "
            "Never invent facts, quotes, motives, predictions, statistics or causal links.\n\n"
            + data["research_bundle"]
        )
        data["text"] = _clean(data.get("text"))
        prepared_for_primary = _prepare_primary_writer_data(data, format_mode)
        try:
            result = current(prepared_for_primary, language_cfg, genre_key, conn, format_mode)
        except Exception as exc:
            # Never expose provider exception payloads to later script stages.
            # Convert the failure into a safe chain diagnostic so OpenRouter and
            # Ollama still get their opportunity before deterministic fallback.
            print(
                f"   [Research] Primary script providers raised {type(exc).__name__}; continuing through fallback chain.",
                flush=True,
            )
            result = None
        if result is None:
            print("   [Research] Primary script providers exhausted; trying OpenRouter free router.", flush=True)
            result = _openrouter_script_fallback(data, language_cfg, genre_key, format_mode)
        if result is None:
            print("   [Research] OpenRouter unavailable or rejected; trying local Ollama.", flush=True)
            result = _ollama_script_fallback(data, language_cfg, genre_key, format_mode)
        if isinstance(result, dict):
            result["research_sources"] = sources
            result["research_source_count"] = len(sources)
            result["research_distinct_domains"] = data["research_distinct_domains"]
            result["research_synthesis_required"] = True
        return result

    researched_write_script._research_wrapped = True
    bot.write_script = researched_write_script
    run_robot.__globals__["write_script"] = researched_write_script

    try:
        from script_runtime import wrap_write_script
        active = wrap_write_script(bot)
    except Exception as exc:
        print(f"   [Research] Content-density wrapper could not be restored: {type(exc).__name__}: {exc}", flush=True)
        active = getattr(bot, "write_script", researched_write_script)

    if callable(active):
        active._research_layer_live = True
        bot.write_script = active
        run_robot.__globals__["write_script"] = active

    bot._research_pipeline_patch_installed = True
    return bot
