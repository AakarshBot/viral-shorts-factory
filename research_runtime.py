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
        raise ValueError(f"{provider_name} returned no usable script array.")
    try:
        from script_runtime import clean_script_data, validate_content_density
        cleaned, diagnostics = clean_script_data(result, story_data, format_mode)
        valid, reason = validate_content_density(cleaned, story_data, format_mode)
        if not valid:
            raise ValueError(f"{provider_name} script validation rejected the response: {reason}")
        cleaned["provider_used"] = provider_name
        cleaned["provider_fallback"] = True
        cleaned["provider_diagnostics"] = diagnostics
        return cleaned
    except Exception as exc:
        print(f"   [{provider_name}] Canonical validation failed: {type(exc).__name__}: {exc}", flush=True)
        raise


def _script_evidence_text(story_data: Dict[str, Any]) -> str:
    return _clean(
        story_data.get("research_evidence_text")
        or story_data.get("research_bundle")
        or story_data.get("text")
        or story_data.get("summary")
        or story_data.get("description")
        or story_data.get("title")
    )


def _http_error_detail(raw_error: Any, max_chars: int = 900) -> str:
    """Extract a compact provider error message without leaking credentials."""
    detail = ""
    raw_text = str(raw_error or "").strip()
    if raw_text:
        try:
            payload = json.loads(raw_text)
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict):
                    detail = str(
                        error.get("message")
                        or error.get("detail")
                        or error.get("type")
                        or ""
                    ).strip()
                if not detail:
                    detail = str(
                        payload.get("message")
                        or payload.get("detail")
                        or ""
                    ).strip()
        except Exception:
            detail = ""
    if not detail:
        detail = raw_text
    detail = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", detail)
    detail = re.sub(r"(?i)(?:gsk_|sk-or-v1-|AIza)[A-Za-z0-9._-]+", "[redacted]", detail)
    return re.sub(r"\s+", " ", detail).strip()[:max_chars]


def _parse_provider_json(raw: Any) -> Dict[str, Any]:
    """Parse provider JSON even when a compatible model adds a code fence or short preamble."""
    text = str(raw or "").strip()
    text = re.sub(
        r"^\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("Provider returned no parseable JSON object.")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Provider returned JSON that is not an object.")
    return parsed


def _gemini_script_fallback(
    story_data: Dict[str, Any],
    language_cfg: Dict[str, Any],
    genre_key: str,
    format_mode: str,
):
    """Generate the same schema through the current Gemini REST API."""
    api_key = _clean(os.getenv("GEMINI_API_KEY"))
    source_text = _script_evidence_text(story_data)
    if not api_key:
        raise RuntimeError("Gemini script provider unavailable: GEMINI_API_KEY is not configured.")
    if not source_text:
        raise RuntimeError("Gemini script provider unavailable: script evidence text is empty.")

    payload = {
        "system_instruction": {
            "parts": [{"text": _fallback_prompt(language_cfg, format_mode, story_data)}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "PHASE 2 EVIDENCE PACK:\n" + source_text}],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 900,
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    request = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
        candidates = body.get("candidates") or []
        if not candidates:
            raise ValueError("Gemini returned no candidates.")
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
        raw = "".join(
            str(part.get("text") or "")
            for part in parts
            if isinstance(part, dict)
        ).strip()
        result = _parse_provider_json(raw)
        return _validate_provider_script(
            result,
            story_data,
            format_mode,
            "gemini-3.8-flash",
        )
    except urllib.error.HTTPError as exc:
        try:
            raw_error = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw_error = ""
        detail = _http_error_detail(raw_error)
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"Gemini script provider HTTP {exc.code}{suffix}; falling through to the next provider."
        ) from exc
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(
            f"Gemini script provider failed: {type(exc).__name__}: {exc}"
        ) from exc


def _fallback_prompt(language_cfg: Dict[str, Any], format_mode: str, story_data: Dict[str, Any] | None = None) -> str:
    language_instruction = _clean((language_cfg or {}).get("script_instruction"))
    return (
        "You are the backup original-news Shorts writer. Use only the supplied evidence and never copy a complete "
        "source sentence verbatim. Do not invent facts, quotes, motives, numbers, or outcomes. "
        "Return ONLY JSON matching this exact object shape; no Markdown or commentary. "
        "{\"editorial_angle\":\"...\",\"titles\":[\"...\",\"...\",\"...\"],"
        "\"recommended_title_index\":1,\"seo_description\":\"...\",\"pinned_comment\":\"...\","
        "\"script\":[{\"voiceover\":\"...\",\"narrative_role\":\"hook\","
        "\"primary_entity\":\"...\",\"visual_intent\":\"news_event\","
        "\"specific_search_prompt\":\"...\",\"sport_or_topic_category\":\"...\"}]}. "
        "Use narrative_role values hook, development, context, consequence.\n"
        "RUNTIME CONTRACT — NON-NEGOTIABLE:\n"
        "- Target roughly 55–65 spoken words; never exceed the 90-word safety ceiling.\n"
        "- Scene 1: 8–14 words, a factual headline, and the most compact scene.\n"
        "- A regular Short MUST contain 3 or 4 scenes: hook, development/context, and consequence/payoff.\n"
        "- Put the substance in the middle beats; do not let Scene 1 carry the detail.\n"
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
            result = _parse_provider_json(raw)
        return _validate_provider_script(result, story_data, format_mode, provider_name)
    except urllib.error.HTTPError as exc:
        try:
            raw_error = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw_error = ""
        detail = _http_error_detail(raw_error)
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"{provider_name} HTTP {exc.code}{suffix}; falling through to the next provider."
        ) from exc
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{provider_name} request failed: {type(exc).__name__}; falling through to the next provider.") from exc
    except Exception as exc:
        raise RuntimeError(f"{provider_name} failed: {type(exc).__name__}: {exc}") from exc


def _openrouter_script_fallback(story_data: Dict[str, Any], language_cfg: Dict[str, Any], genre_key: str, format_mode: str):

    api_key = _clean(os.getenv("OPENROUTER_API_KEY"))
    source_text = _script_evidence_text(story_data)
    if not api_key:
        raise RuntimeError("OpenRouter free unavailable: OPENROUTER_API_KEY is not configured.")
    if not source_text:
        raise RuntimeError("OpenRouter free unavailable: script evidence text is empty.")
    payload = {
        "model": "openrouter/free",
        "messages": [
            {"role": "system", "content": _fallback_prompt(language_cfg, format_mode, story_data)},
            {"role": "user", "content": "PHASE 2 EVIDENCE PACK:\n" + source_text},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 450,
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
        raise RuntimeError("Local Ollama unavailable: script evidence text is empty.")

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
            "   [Research] Local Ollama skipped in remote mode; using the next script provider.",
            flush=True,
        )
        return None

    configured_model = _clean(os.getenv("OLLAMA_SCRIPT_MODEL"))
    model = configured_model or "gpt-oss:20b"

    # Local Ollama exposes /api/tags, so verify the model exists before sending
    # a generation request. If the default is missing, use an installed model
    # rather than producing a confusing 404 from the OpenAI-compatible endpoint.
    if base_host in local_hosts:
        try:
            tags_request = urllib.request.Request(
                base_url.rstrip("/") + "/api/tags",
                headers={"Accept": "application/json"},
                method="GET",
            )
            with urllib.request.urlopen(tags_request, timeout=5) as tags_response:
                tags_body = json.loads(tags_response.read().decode("utf-8"))
            installed = [
                str(item.get("name") or "").strip()
                for item in (tags_body.get("models") or [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            ]
        except Exception as exc:
            raise RuntimeError(
                f"Local Ollama preflight failed at {base_url}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if model not in installed:
            if configured_model:
                raise RuntimeError(
                    f"Local Ollama unavailable: configured model '{model}' is not installed. "
                    f"Installed models: {', '.join(installed[:12]) or 'none'}."
                )
            preferred = [
                name for name in installed
                if name.casefold().startswith(
                    ("gpt-oss", "llama", "qwen", "mistral", "gemma", "deepseek", "phi")
                )
            ]
            if preferred:
                model = preferred[0]
            elif installed:
                model = installed[0]
            else:
                raise RuntimeError(
                    "Local Ollama unavailable: no installed models were found. "
                    "Install a chat model or set OLLAMA_SCRIPT_MODEL."
                )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _fallback_prompt(language_cfg, format_mode, story_data)},
            {"role": "user", "content": "PHASE 2 EVIDENCE PACK:\n" + source_text},
        ],
        "stream": False,
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 450,
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
