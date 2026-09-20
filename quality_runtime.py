"""Deterministic pre-render quality controls for Viral Shorts Factory."""
import difflib
import re


def _words(text):
    return re.findall(r"[A-Za-z0-9]+", str(text or "").lower())


def _overlap(a, b):
    aa, bb = set(_words(a)), set(_words(b))
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def _normalise(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()


def _quality_validate(original_validate, script_data, source_text, format_mode):
    """Run the existing validator plus deterministic non-numeric script QC."""
    ok, message = original_validate(script_data, source_text, format_mode)
    if not ok:
        return ok, message

    scenes = script_data.get("script", [])
    if not isinstance(scenes, list) or not scenes:
        return False, "Script contains no scenes."

    for i, scene in enumerate(scenes, 1):
        voice = str(scene.get("voiceover", "")).strip()
        entity = str(scene.get("primary_entity", "")).strip()
        prompt = str(scene.get("specific_search_prompt", "")).strip()
        if not voice:
            return False, f"Scene {i} has empty narration."
        if not entity or entity.lower() == "none":
            return False, f"Scene {i} has no primary visual entity."
        if len(_words(prompt)) < 2:
            return False, f"Scene {i} has an unusable visual search prompt."

    for i in range(len(scenes)):
        for j in range(i + 1, len(scenes)):
            similarity = difflib.SequenceMatcher(
                None,
                _normalise(scenes[i].get("voiceover", "")),
                _normalise(scenes[j].get("voiceover", "")),
            ).ratio()
            if similarity >= 0.88:
                return False, f"Scenes {i+1} and {j+1} are near-duplicates."

    first = _normalise(scenes[0].get("voiceover", ""))
    forbidden_openers = (
        "welcome to", "hey everyone", "hey guys", "today we are going to",
        "in this video", "let us talk about", "here is a crisp script",
        "you will not believe", "you won't believe", "stop scrolling",
    )
    if any(first.startswith(x) for x in forbidden_openers):
        return False, "Scene 1 starts with a generic or performative opener."

    titles = script_data.get("titles")
    if not isinstance(titles, list) or len(titles) != 3:
        return False, "Exactly three titles are required."
    if any(not str(t).strip() for t in titles):
        return False, "One or more generated titles are empty."

    recommended = script_data.get("recommended_title_index")
    if recommended not in (0, 1, 2):
        return False, "Recommended title index is invalid."

    description = str(script_data.get("seo_description", "")).strip()
    if len(_words(description)) < 10:
        return False, "SEO description is too short."

    return True, "Passed deterministic Shorts QC"


def _self_critique(script_data, format_mode):
    """Score useful storytelling properties without rewarding CTAs, filler or scene count."""
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not scenes:
        return 0, "No scenes"

    score = 10.0
    reasons = []
    first = _normalise(scenes[0].get("voiceover", ""))
    if any(first.startswith(x) for x in ("welcome to", "hey everyone", "today we are going to", "in this video")):
        score -= 2
        reasons.append("generic opener")

    try:
        from script_runtime import assess_narrative_completeness
        completeness = assess_narrative_completeness(script_data)
        if not completeness["passed"]:
            score -= 3
            reasons.append("incomplete narrative")
    except Exception:
        pass

    for i in range(len(scenes)):
        for j in range(i + 1, len(scenes)):
            if difflib.SequenceMatcher(
                None,
                _normalise(scenes[i].get("voiceover")),
                _normalise(scenes[j].get("voiceover")),
            ).ratio() >= 0.88:
                score -= 1
                reasons.append("repeated scene")
                break

    score = max(0, min(10, round(score, 1)))
    return score, ("Passed" if not reasons else "; ".join(dict.fromkeys(reasons)))


def patch_quality_control(bot):
    """Patch the legacy module's validator and self-critique globally."""
    original_validate = bot.validate_script

    def validate(script_data, source_text, format_mode):
        return _quality_validate(original_validate, script_data, source_text, format_mode)

    bot.validate_script = validate
    bot.self_critique_pass = _self_critique
    print("   [QC] Deterministic content-first Shorts quality control enabled.")
    return bot
