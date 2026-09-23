"""Research orchestration for the Phase 2 evidence engine."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict

from evidence_runtime import DEFAULT_MAX_SOURCES, build_evidence_pack, discover_sources, format_evidence_pack_for_script


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _story_query(story: Dict[str, Any]) -> str:
    return _clean(story.get("title") or story.get("topic"))[:220]


def _prepare_primary_writer_data(story_data: Dict[str, Any], format_mode: str) -> Dict[str, Any]:
    data = dict(story_data or {})
    if str(format_mode or "").lower() != "cricket":
        return data
    raw_text = _clean(
        data.get("text")
        or data.get("summary")
        or data.get("description")
        or data.get("title")
    )
    evidence_text = _clean(data.get("research_evidence_text") or data.get("research_bundle"))
    if evidence_text:
        raw_text = f"{raw_text}\n\n{evidence_text}".strip()
    title = _clean(data.get("title") or data.get("topic") or "Selected cricket story")
    data["text"] = json.dumps([{"title": title, "text": raw_text}])
    return data


def _validate_provider_script(result: Any, story_data: Dict[str, Any], format_mode: str, provider_name: str):
    if not isinstance(result, dict) or not isinstance(result.get("script"), list):
        return None
    try:
        from script_runtime import clean_script_data, validate_content_density
        cleaned, diagnostics = clean_script_data(result, story_data, format_mode)
        valid, reason = validate_content_density(cleaned, story_data, format_mode)
        if not valid:
            print(f"   [{provider_name}] Script validation rejected response: {reason}", flush=True)
            return None
        cleaned["provider_used"] = provider_name
        cleaned["provider_fallback"] = True
        cleaned["provider_diagnostics"] = diagnostics
        return cleaned
    except Exception as exc:
        print(f"   [{provider_name}] Canonical validation unavailable: {type(exc).__name__}: {exc}", flush=True)
        return None


def _script_evidence_text(story_data: Dict[str, Any]) -> str:
    return _clean(
        story_data.get("research_evidence_text")
        or story_data.get("research_bundle")
        or story_data.get("text")
        or story_data.get("summary")
        or story_data.get("description")
        or story_data.get("title")
    )


def _fallback_prompt(language_cfg: Dict[str, Any], format_mode: str, story_data: Dict[str, Any] | None = None) -> str:
    language_instruction = _clean((language_cfg or {}).get("script_instruction"))
    return (
        "You are the backup original-news Shorts writer. Use only the supplied evidence and never copy a complete "
        "source sentence verbatim. Do not invent facts, quotes, motives, numbers, or outcomes. "
        "Return ONLY valid JSON using the factory schema.\n"
        "RUNTIME CONTRACT — NON-NEGOTIABLE:\n"
        "- Voiceover total: 55–60 words. Never exceed 60 words.\n"
        "- Scene 1: 8–14 words, a factual headline, and the shortest scene.\n"
        "- Prefer 3 or 4 scenes. Put the substance in the later scenes.\n"
        "- The full narration must naturally fit below 30 seconds.\n"
        "- No intro, CTA, generic filler, retention bait, or production instructions.\n"
        "STORY SHAPE: Scene 1 states the concrete event/person immediately. Later scenes carry the key evidence, "
        "context, and consequence. Curiosity must come from a real supported fact.\n"
        f"Language: {language_instruction}"
    )


def _call_chat_completion(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: int,
    story_data: Dict[str, Any],
    format_mode: str,
    provider_name: str,
):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        raw = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(raw, dict):
            result = raw
        else:
            text = re.sub(
                r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$",
                "",
                str(raw or "").strip(),
                flags=re.IGNORECASE,
            ).strip()
            result = json.loads(text)
        return _validate_provider_script(result, story_data, format_mode, provider_name)
    except urllib.error.HTTPError as exc:
        print(f"   [{provider_name}] HTTP {exc.code}; falling through.", flush=True)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        print(f"   [{provider_name}] Request failed: {type(exc).__name__}; falling through.", flush=True)
    except Exception as exc:
        print(f"   [{provider_name}] Unexpected failure: {type(exc).__name__}; falling through.", flush=True)
    return None


def _openrouter_script_fallback(story_data: Dict[str, Any], language_cfg: Dict[str, Any], genre_key: str, format_mode: str):

    api_key = _clean(os.getenv("OPENROUTER_API_KEY"))
    source_text = _script_evidence_text(story_data)
    if not api_key or not source_text:
        return None
    payload = {
        "model": "openrouter/free",
        "messages": [
            {"role": "system", "content": _fallback_prompt(language_cfg, format_mode, story_data)},
            {"role": "user", "content": "PHASE 2 EVIDENCE PACK:\n" + source_text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    return _call_chat_completion(
        "https://openrouter.ai/api/v1/chat/completions",
        payload,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/AakarshBot/viral-shorts-factory",
            "X-Title": "Viral Shorts Factory",
        },
        45,
        story_data,
        format_mode,
        "openrouter/free",
    )


def _ollama_script_fallback(story_data: Dict[str, Any], language_cfg: Dict[str, Any], genre_key: str, format_mode: str):
    source_text = _script_evidence_text(story_data)
    if not source_text:
        return None

    base_url = _clean(os.getenv("OLLAMA_BASE_URL")) or "http://localhost:11434"
    base_host = ""
    try:
        from urllib.parse import urlparse
        base_host = str(urlparse(base_url).hostname or "").strip().lower()
    except Exception:
        pass

    remote_mode = _clean(os.getenv("VSF_REMOTE_MODE")).lower() in {
        "1", "true", "yes", "remote", "cloud", "streamlit", "streamlit_cloud"
    }
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if remote_mode and base_host in local_hosts:
        print(
            "   [Script Pipeline] Local Ollama unavailable in remote mode; "
            "skipping local-only fallback.",
            flush=True,
        )
        return None

    model = _clean(os.getenv("OLLAMA_SCRIPT_MODEL")) or "gpt-oss:20b"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _fallback_prompt(language_cfg, format_mode, story_data)},
            {"role": "user", "content": "PHASE 2 EVIDENCE PACK:\n" + source_text},
        ],
        "stream": False,
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    return _call_chat_completion(
        base_url.rstrip("/") + "/v1/chat/completions",
        payload,
        {"Content-Type": "application/json"},
        30,
        story_data,
        format_mode,
        f"ollama/{model}",
    )
