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


def install() -> bool:
    """Install the narration-only leakage and retention-bait guard."""
    global _INSTALLED
    if _INSTALLED:
        return True
    try:
        import script_runtime

        original_validate = script_runtime.validate_content_density

        def guarded_validator(script_data, story_data, format_mode):
            scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
            for index, scene in enumerate(scenes, 1):
                voiceover = str(scene.get("voiceover") or "").strip() if isinstance(scene, dict) else ""
                if looks_like_instructional_narration(voiceover):
                    return False, f"Scene {index} contains prompt/instruction/code text instead of narration."
                if script_runtime.contains_retention_bait(voiceover):
                    return False, f"Scene {index} contains prohibited retention-bait phrasing."
            return original_validate(script_data, story_data, format_mode)

        script_runtime.validate_content_density = guarded_validator
        script_runtime.SCRIPT_OUTPUT_GUARD_VERSION = "2026-09-20-semantic"
        _INSTALLED = True
        print("   [Script Guard] Prompt/schema leakage + retention-bait guard installed.", flush=True)
        return True
    except Exception as exc:
        print(f"   [Script Guard] Installation failed: {type(exc).__name__}: {exc}", flush=True)
        return False
