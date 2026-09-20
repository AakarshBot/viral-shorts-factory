"""Manual visual-query routing for the dashboard visual pipeline.

Manual queries are optional. When present, the first query is a hard contract for
scene/frame 1. Any remaining queries are routed to later scenes using the scene's
factual entity, narration and visual intent, then the existing retrieval/QA stack
fetches and verifies the resulting image.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from typing import Any

import requests


_STOPWORDS = {
    "the","a","an","and","or","of","to","in","on","at","for","with","from",
    "by","is","are","was","were","be","been","this","that","these","those",
    "as","into","over","after","before","during","about","their","his","her",
    "its","they","them","he","she","it","will","would","could","should",
    "has","have","had","not","but","than","then","also","very","more",
    "news","latest","today","story","report","reports","says","said",
}


def _tokens(value: Any) -> list[str]:
    text = re.sub(r"[^\w\s-]", " ", str(value or "").lower(), flags=re.UNICODE)
    words = re.findall(r"[\w-]+", text, flags=re.UNICODE)
    return [w for w in words if len(w) > 1 and w not in _STOPWORDS]


def parse_manual_visual_queries(raw: Any) -> list[str]:
    """Parse dashboard visual queries using semicolons, newlines, or commas.

    The dashboard commonly receives either:
      Saurav Ganguly, BCCI logo, Indian Cricket Team
    or:
      'Saurav Ganguly', 'BCCI logo', 'Indian Cricket Team'

    Lists/tuples are accepted directly so callers do not accidentally turn an
    already-parsed list back into one string.
    """
    if raw is None:
        return []

    if isinstance(raw, (list, tuple, set)):
        values: list[str] = []
        for item in raw:
            value = re.sub(r"\s+", " ", str(item or "")).strip().strip(",").strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1].strip()
            if value and value not in values:
                values.append(value[:300])
        return values

    raw_text = str(raw)
    if not raw_text.strip():
        return []

    values: list[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", str(value or "")).strip().strip(",").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1].strip()
        if value and value not in values:
            values.append(value[:300])

    # Semicolon/newline remain explicit high-confidence delimiters.
    if ";" in raw_text or "\n" in raw_text or "\r" in raw_text:
        for item in re.split(r";|\r?\n+", raw_text):
            add(item)
        return values

    text = re.sub(r"\s+", " ", raw_text).strip()

    # First support quoted comma-separated input.
    quoted = re.findall(
        r"""(?:^|,\s*)['"]([^'"]+)['"](?=\s*(?:,|$))""",
        text,
    )
    if quoted:
        for item in quoted:
            add(item)
        return values

    # The dashboard also accepts plain comma-separated query lists. Split only
    # on commas that are surrounded by whitespace; this preserves commas used
    # as punctuation inside compact tokens while handling the normal UI form.
    if re.search(r",\s+", text):
        for item in re.split(r",\s+", text):
            add(item)
        return values

    add(text)
    return values


def _local_query_fallback(title: str, body: str, max_queries: int = 5) -> list[dict[str, str]]:
    """Produce useful deterministic visual terms when the single AI planning call is unavailable."""
    text = re.sub(r"\s+", " ", f"{title} {body}".strip())
    candidates: list[str] = []
    seen: set[str] = set()
    patterns = [
        r"\b[A-Z][A-Za-z.'-]{2,}(?:\s+[A-Z][A-Za-z.'-]{2,}){0,3}\b",
        r"\b[A-Z]{2,8}\b",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = re.sub(r"\s+", " ", match).strip(" ,.;:!?()[]{}\"'")
            key = value.casefold()
            if len(value) < 3 or key in seen:
                continue
            seen.add(key)
            candidates.append(value)
            if len(candidates) >= max_queries:
                break
        if len(candidates) >= max_queries:
            break
    if not candidates and title:
        candidates.append(re.sub(r"\s+", " ", str(title)).strip()[:180])
    return [
        {
            "query": value,
            "source_hint": "Configured visual sources",
            "reason": "Deterministic fallback from named entities in the selected story.",
        }
        for value in candidates[:max(1, int(max_queries))]
    ]


def _parse_query_planner_response(raw_text: str, max_queries: int) -> list[dict[str, str]]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except Exception:
            return []
    rows = parsed.get("queries") if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        return []
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in rows:
        if isinstance(item, dict):
            query = str(item.get("query") or "").strip()
            source_hint = str(item.get("source_hint") or "Commons / configured image source").strip()
            reason = str(item.get("reason") or "").strip()
        else:
            query = str(item or "").strip()
            source_hint = "Commons / configured image source"
            reason = ""
        query = re.sub(r"\s+", " ", query).strip(" ,.;:!?")
        words = re.findall(r"[\w&.'-]+", query, flags=re.UNICODE)
        if not query or not words or len(words) > 8:
            continue
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "query": query[:180],
                "source_hint": source_hint[:80],
                "reason": reason[:180],
            }
        )
        if len(output) >= max(1, int(max_queries)):
            break
    return output


def generate_visual_query_suggestions(
    story_title: str,
    story_text: str = "",
    category: str = "",
    max_queries: int = 5,
) -> list[dict[str, str]]:
    """Make one text-AI call for ranked image-search terms; never starts an image search."""
    title = re.sub(r"\s+", " ", str(story_title or "")).strip()
    body = re.sub(r"\s+", " ", str(story_text or "")).strip()
    limit = max(1, min(8, int(max_queries or 5)))
    system_prompt = (
        "You are the visual-search query planner for a monetized YouTube Shorts factory. "
        "Audit the selected story and return a small ranked set of concrete image-search phrases. "
        "These are SEARCH TERMS ONLY, not image results. Prioritize exact named people, organizations, "
        "logos, teams, places, products, events, landmarks or other concrete entities. Do not assume "
        "Wikimedia Commons is the primary source. For sports or live-event stories, prioritize a real "
        "action context when the story supports it, such as batting, bowling, playing, match action, "
        "celebration, training, interview or on-stage action. For a person, prefer the person's exact "
        "name plus the supported action/context; for a logo, use the organization name plus logo; "
        "for a team or event, use the exact team/event/entity plus one useful action/context noun only "
        "when the story supports it. Do not invent facts. Avoid generic phrases such as 'news', 'latest update', "
        "'editorial photo', or 'interesting image'. Keep every query concise and directly searchable. "
        "Return JSON only: {\"queries\":[{\"query\":\"...\",\"source_hint\":\"...\",\"reason\":\"...\"}]}. "
        "Rank the most likely-to-return result first."
    )
    user_prompt = json.dumps(
        {
            "title": title,
            "story": body[:7000],
            "category": str(category or "").strip(),
            "max_queries": limit,
        },
        ensure_ascii=False,
    )
    groq_key = str(os.getenv("GROQ_API_KEY") or "").strip()
    gemini_key = str(os.getenv("GEMINI_API_KEY") or "").strip()
    raw = ""
    if groq_key:
        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-oss-120b",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=20,
            )
            if response.status_code == 200:
                raw = str(response.json().get("choices", [{}])[0].get("message", {}).get("content", "") or "")
        except Exception:
            raw = ""
    elif gemini_key:
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={gemini_key}",
                json={
                    "contents": [{"role": "user", "parts": [{"text": system_prompt + "\n\nSTORY:\n" + user_prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"},
                },
                timeout=20,
            )
            if response.status_code == 200:
                raw = str(response.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "") or "")
        except Exception:
            raw = ""
    return _parse_query_planner_response(raw, limit) or _local_query_fallback(title, body, limit)


def _scene_text(scene: dict[str, Any]) -> str:
    return " ".join(
        str(scene.get(key, "") or "")
        for key in (
            "primary_entity",
            "factual_primary_entity",
            "visual_search_subject",
            "voiceover",
            "visual_intent",
            "specific_search_prompt",
            "visual_context",
        )
    )


def _score(query: str, scene: dict[str, Any], scene_index: int, query_index: int, total_scenes: int, total_queries: int) -> float:
    q_tokens = _tokens(query)
    if not q_tokens:
        return -1000.0
    q_set = set(q_tokens)

    entity = str(
        scene.get("factual_primary_entity")
        or scene.get("primary_entity")
        or scene.get("visual_search_subject")
        or ""
    ).strip().lower()
    entity_tokens = set(_tokens(entity))
    scene_tokens = _tokens(_scene_text(scene))
    scene_counts = Counter(scene_tokens)

    score = 0.0
    if entity and entity in query.lower():
        score += 100.0

    entity_overlap = len(q_set & entity_tokens)
    score += min(60.0, entity_overlap * 22.0)

    scene_overlap = len(q_set & set(scene_tokens))
    score += min(36.0, scene_overlap * 6.0)

    # Rare query terms are more informative than generic terms.
    score += sum(min(3, scene_counts[token]) for token in q_set if token in scene_counts) * 1.5

    # If the user supplied exactly one query per scene, preserve their intended
    # sequence as a weak tie-breaker, never as the primary assignment rule.
    if total_queries == total_scenes:
        distance = abs(scene_index - query_index)
        score += max(0.0, 8.0 - distance * 2.0)

    return score


def assign_manual_queries(scenes: list[dict[str, Any]], raw_queries: Any) -> list[dict[str, Any]]:
    """Assign each supplied query to at most one scene, with graceful reuse.

    The output is scene-aligned. Queries are assigned only when they have
    positive semantic overlap with a scene. Unmatched scenes intentionally
    receive an empty query so the normal identity-first retrieval path can take
    over; unrelated manual vocabulary is never forced onto them.
    """
    queries = parse_manual_visual_queries(raw_queries)
    if not queries:
        return [{} for _ in scenes]

    assignments = [[] for _ in scenes]

    # HARD CONTRACT: when the user supplies any manual queries, query #1 is
    # always the visual query for scene/frame #1. It is never semantically
    # reassigned to another scene. This makes the first manual query a direct
    # control for the opening frame while preserving automatic routing for the
    # remaining queries.
    assignments[0].append((queries[0], 10000.0))

    # Remaining queries use the existing semantic router. Query #1 is removed
    # from the candidate pool so it cannot leak into another scene.
    remaining_queries = queries[1:]
    remaining_scene_indices = range(1, len(scenes))

    pairs = []
    for si in remaining_scene_indices:
        for local_qi, query in enumerate(remaining_queries, start=1):
            pairs.append((_score(query, scenes[si], si, local_qi, len(scenes), len(queries)), si, local_qi))
    pairs.sort(reverse=True)

    used_scenes = {0}
    used_queries = {0}
    for score, si, qi in pairs:
        if si in used_scenes or qi in used_queries:
            continue
        if score <= 0:
            continue
        assignments[si].append((queries[qi], score))
        used_scenes.add(si)
        used_queries.add(qi)

    # Do not force an unrelated query onto a scene. An unassigned scene must
    # fall back to normal identity-first retrieval, not search for an arbitrary
    # leftover person/logo/topic supplied for another scene.
    for si in range(len(scenes)):
        if assignments[si]:
            continue
        assignments[si].append(("", 0.0))

    result = []
    for si, scene in enumerate(scenes):
        item = assignments[si][0]
        query = item[0]
        result.append({
            "query": query,
            "score": round(float(item[1]), 2),
            "query_index": (queries.index(query) + 1) if query else 0,
            "strict_first_frame": bool(query and queries.index(query) == 0 and si == 0),
            "total_queries": len(queries),
        })
    return result
