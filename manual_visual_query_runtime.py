"""Manual visual-query routing for the dashboard visual pipeline.

Manual queries are optional. When present, they are treated as search vocabulary
rather than hard scene indexes. The router assigns them to script scenes using
the scene's factual entity, narration and visual intent, then lets the existing
retrieval/QA stack fetch and verify the resulting image.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any


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
    """Parse the dashboard's semicolon-separated input safely."""
    if raw is None:
        return []
    values = []
    for item in str(raw).split(";"):
        item = re.sub(r"\s+", " ", item).strip()
        if item and item not in values:
            values.append(item[:300])
    return values


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

    The output is scene-aligned. If there are more scenes than queries, the best
    query can be reused only when necessary. If there are more queries than
    scenes, unassigned queries remain available as retrieval alternatives on
    the closest scene.
    """
    queries = parse_manual_visual_queries(raw_queries)
    if not queries:
        return [{} for _ in scenes]

    assignments = [[] for _ in scenes]
    unused = set(range(len(queries)))

    # Global greedy matching is deliberately simple and deterministic.
    pairs = []
    for si, scene in enumerate(scenes):
        for qi, query in enumerate(queries):
            pairs.append((_score(query, scene, si, qi, len(scenes), len(queries)), si, qi))
    pairs.sort(reverse=True)

    used_scenes = set()
    used_queries = set()
    for score, si, qi in pairs:
        if si in used_scenes or qi in used_queries:
            continue
        if score <= 0:
            continue
        assignments[si].append((queries[qi], score))
        used_scenes.add(si)
        used_queries.add(qi)

    # Every scene gets a query when possible. This fallback is only used when
    # semantic evidence is weak; it prevents a manually supplied query from
    # disappearing merely because the script wording is indirect.
    for si in range(len(scenes)):
        if assignments[si]:
            continue
        remaining = [qi for qi in range(len(queries)) if qi not in used_queries]
        if remaining:
            qi = max(remaining, key=lambda q: _score(queries[q], scenes[si], si, q, len(scenes), len(queries)))
            assignments[si].append((queries[qi], _score(queries[qi], scenes[si], si, qi, len(scenes), len(queries))))
            used_queries.add(qi)
        else:
            qi = max(range(len(queries)), key=lambda q: _score(queries[q], scenes[si], si, q, len(scenes), len(queries)))
            assignments[si].append((queries[qi], _score(queries[qi], scenes[si], si, qi, len(scenes), len(queries))))

    result = []
    for si, scene in enumerate(scenes):
        item = assignments[si][0]
        result.append({
            "query": item[0],
            "score": round(float(item[1]), 2),
            "query_index": queries.index(item[0]) + 1,
            "total_queries": len(queries),
        })
    return result
