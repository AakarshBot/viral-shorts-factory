"""Hard guard for script generation and source-grounded emergency fallback.

This module keeps article/source text separate from editorial instructions and
rejects generated narration that looks like prompt, schema, workflow, or code
text. It patches script_runtime before the runtime binding imports wrap_write_script.
"""
from __future__ import annotations

import re

_INSTALLED = False

_INSTRUCTION_PATTERNS = (
    r"\beditorial\s+script\s+contract\b",
    r"\bdo\s+not\s+output\b",
    r"\breturn\s+(?:only|a)\s+(?:valid\s+)?json\b",
    r"\bvalid\s+json\s+object\b",
    r"\bjson\s+schema\b",
    r"\bstep[_ -]?\d+[_ -]?(?:headline|data|critique|metadata)\b",
    r"\bcore\s+shape\s*:\s*",
    r"\boriginal\s+contribution\s*:\s*",
    r"\bpacing\s*:\s*",
    r"\bhook\s*:\s*",
    r"\bending\s*:\s*",
    r"\bcta\s*:\s*",
    r"\bstyle\s*:\s*",
    r"\bprimary[_ ]entity\s*[:=]",
    r"\bspecific[_ ]search[_ ]prompt\s*[:=]",
    r"\bvisual[_ ]intent\s*[:=]",
    r"\bvoiceover\s*[:=]\s*[\"']",
    r"\btop[_ ]level\s+fields?\b",
    r"\bworkflow\s+(?:stage|step|contract|instructions?)\b",
    r"\b(?:you are|as an ai|as a language model)\b",
    r"```(?:json|python|text)?\b",
    r"\b(?:import|def|class)\s+[A-Za-z_]\w*\s*[(:]",
)
_COMPILED = tuple(re.compile(p, re.IGNORECASE) for p in _INSTRUCTION_PATTERNS)


def looks_like_instructional_narration(text: str) -> bool:
    """Return True when narration contains internal prompt/schema/code language."""
    raw = str(text or "").strip()
    if not raw:
        return False
    if any(pattern.search(raw) for pattern in _COMPILED):
        return True
    lowered = raw.lower()
    directive_hits = sum(
        token in lowered
        for token in (
            "write an information-first short",
            "start with the strongest factual headline",
            "use this story-specific structure",
            "do not manufacture",
            "do not merely paraphrase",
            "the viewer should learn something",
            "stop when the useful information is exhausted",
            "never use a retention-only hook",
            "a creator comment may handle engagement",
        )
    )
    return directive_hits >= 1


def _safe_source_text(story_data: dict) -> str:
    """Return only article/source material, never an injected editorial contract."""
    if not isinstance(story_data, dict):
        return ""
    parts = []
    for key in ("text", "summary", "description"):
        value = str(story_data.get(key) or "").strip()
        if value:
            parts.append(value)
    text = "\n".join(parts)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _source_sentences(text: str):
    return [
        re.sub(r"\s+", " ", s).strip(" -")
        for s in re.split(r"(?<=[.!?])\s+", text)
        if len(re.findall(r"[A-Za-z0-9]+", s)) >= 5 and not looks_like_instructional_narration(s)
    ]


def _fallback_subject(title: str) -> str:
    """Choose one simple visual subject from the headline."""
    text = re.sub(r"\s+", " ", title or "").strip(" ,.-:;|")
    stop = {
        "the", "a", "an", "and", "or", "but", "for", "with", "from", "into", "after", "before",
        "this", "that", "these", "those", "why", "how", "what", "when", "where", "who", "will",
        "would", "could", "should", "just", "now", "today", "latest", "breaking", "news", "update",
        "updates", "story", "stories", "report", "reports",
    }
    words = [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9&./'-]*", text) if w.lower() not in stop]
    if not words:
        return "Selected story"
    proper = [w.strip("'\"") for w in words if any(c.isupper() for c in w if c.isalpha())]
    return " ".join(proper[:3]) if proper else words[0]


def source_only_fallback(story_data, language_cfg, genre_key, format_mode):
    """Build an emergency script from article sentences only; never from prompts."""
    story_data = story_data if isinstance(story_data, dict) else {}
    title = re.sub(r"\s+", " ", str(story_data.get("title") or story_data.get("topic") or "Selected story")).strip()
    source = _safe_source_text(story_data)
    sentences = _source_sentences(source)

    # Keep factual source sentences intact where possible. When long sentences
    # exceed the narration limit, trim only at word boundaries without adding text.
    pieces = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= 30:
            pieces.append(sentence)
        else:
            for start in range(0, len(words), 30):
                chunk = " ".join(words[start:start + 30]).strip()
                if len(chunk.split()) >= 8:
                    pieces.append(chunk)
                if len(pieces) >= 8:
                    break
        if len(pieces) >= 8:
            break

    if not pieces:
        raw_words = source.split()
        if title:
            raw_words = (title + " " + " ".join(raw_words)).split()
        for start in range(0, len(raw_words), 24):
            chunk = " ".join(raw_words[start:start + 24]).strip()
            if len(chunk.split()) >= 8:
                pieces.append(chunk)
            if len(pieces) >= 8:
                break

    required = 7 if str(format_mode or "").lower() == "top5" else 5
    if not pieces:
        raise ValueError("Source-grounded fallback could not find usable article narration.")

    # Do not invent filler to reach scene count. Reuse the cleanest source
    # sentence only when necessary; it remains source-derived and factual.
    while len(pieces) < required:
        candidate = pieces[len(pieces) % len(pieces)]
        if candidate not in pieces[-2:]:
            pieces.append(candidate)
        else:
            break

    subject = _fallback_subject(title)
    category = str(genre_key or "news").replace("_", " ").title()
    scenes = []
    for piece in pieces[:required]:
        scenes.append({
            "voiceover": piece,
            "primary_entity": subject,
            "visual_intent": "news_event",
            "specific_search_prompt": subject,
            "sport_or_topic_category": category,
        })

    description = re.sub(r"\s+", " ", source or title).strip()[:700]
    return {
        "step_1_headline": title,
        "step_2_data_points": source or title,
        "step_3_critique": "Source-grounded emergency fallback.",
        "step_4_metadata": subject,
        "titles": [
            title[:100].strip(),
            f"{title[:84].strip()} | Latest Update",
            f"{title[:84].strip()} | What We Know",
        ],
        "recommended_title_index": 1,
        "seo_description": description,
        "tags": [subject, category, "Shorts"],
        "pinned_comment": "What do you make of this latest development?",
        "hook_type": "Direct Factual Headline",
        "hook_style_used": "Direct Factual Headline",
        "structure_used": "Source-grounded explainer",
        "persona_used": "Analytical Insider",
        "script": scenes,
        "fallback_mode": "extractive_source_grounded",
    }


def install() -> bool:
    """Patch script_runtime before runtime_bindings imports wrap_write_script."""
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        import script_runtime

        original_contract = script_runtime._add_editorial_contract
        original_validate = script_runtime.validate_content_density
        script_runtime._original_editorial_contract = original_contract
        script_runtime._original_content_density_validator = original_validate

        def clean_contract_only(story_data, format_mode):
            if not isinstance(story_data, dict):
                return story_data
            copy = dict(story_data)
            # Preserve the article exactly as source data. Store the editorial
            # guidance separately so it can never become article content.
            copy["editorial_contract"] = script_runtime._story_structure(story_data, format_mode)
            return copy

        def guarded_validator(script_data, story_data, format_mode):
            scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
            for index, scene in enumerate(scenes, 1):
                voiceover = str(scene.get("voiceover", "")).strip() if isinstance(scene, dict) else ""
                if looks_like_instructional_narration(voiceover):
                    return False, f"Scene {index} contains prompt/instruction/code text instead of narration."
            return original_validate(script_data, story_data, format_mode)

        script_runtime._add_editorial_contract = clean_contract_only
        script_runtime.validate_content_density = guarded_validator
        script_runtime._extractive_script_fallback = source_only_fallback
        script_runtime.SCRIPT_OUTPUT_GUARD_VERSION = "2026-09-17-v1"
        _INSTALLED = True
        print("   [Script Guard] Source/instruction separation + narration-only validation installed.", flush=True)
        return True
    except Exception as exc:
        print(f"   [Script Guard] Installation failed: {type(exc).__name__}: {exc}", flush=True)
        return False
