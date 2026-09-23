"""Deterministic pre-render quality controls for Viral Shorts Factory."""
import difflib
import re


def _words(text):
    return re.findall(r"\b\w+\b", str(text or "").lower(), flags=re.UNICODE)


def _overlap(a, b):
    aa, bb = set(_words(a)), set(_words(b))
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def _normalise(text):
    # Keep non-Latin scripts intact so multilingual narration is not
    # collapsed to an empty string and falsely marked as duplicate.
    return re.sub(r"\s+", " ", re.sub(r"[^\w ]", " ", str(text or "").casefold(), flags=re.UNICODE)).strip()




def _normalise_numeric_token(value):
    return re.sub(r"[, ]", "", str(value or "")).strip()


def _numeric_tokens(text):
    return {
        _normalise_numeric_token(token)
        for token in re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:%)?", str(text or ""))
        if _normalise_numeric_token(token)
    }

def validate_deterministic_script_quality(script_data, format_mode="regular", story_data=None):
    """Validate deterministic writer-output quality without making another model/API call."""
    if not isinstance(script_data, dict):
        return False, "Script payload is not an object."

    scenes = script_data.get("script", [])
    if not isinstance(scenes, list) or not scenes:
        return False, "Script contains no scenes."

    story_data = story_data if isinstance(story_data, dict) else {}
    evidence_blob = " ".join(
        str(story_data.get(key) or "")
        for key in ("title", "topic", "text", "summary", "description", "snippet", "research_evidence_text", "research_bundle")
    )
    evidence_numbers = _numeric_tokens(evidence_blob)
    if evidence_blob:
        for index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict):
                continue
            scene_numbers = _numeric_tokens(scene.get("voiceover", ""))
            unsupported = sorted(scene_numbers - evidence_numbers)
            if unsupported:
                return False, (
                    f"Scene {index} contains unsupported numeric detail(s): "
                    + ", ".join(unsupported[:6])
                )

    mode = str(format_mode or "").strip().lower()
    expected = 6 if mode == "top5" else None
    if expected is not None and len(scenes) != expected:
        return False, "Top-5 script must contain exactly 6 scenes."
    if expected is None and len(scenes) not in (3, 4):
        return False, "Regular Short must contain exactly 3 or 4 scenes."

    allowed_roles = {"hook", "development", "context", "consequence"}
    for i, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            return False, f"Scene {i} is malformed."
        voice = str(scene.get("voiceover", "")).strip()
        entity = str(scene.get("primary_entity", "")).strip()
        prompt = str(scene.get("specific_search_prompt", "")).strip()
        intent = str(scene.get("visual_intent", "")).strip()
        category = str(scene.get("sport_or_topic_category", "")).strip()
        role = str(scene.get("narrative_role", "")).strip().lower()
        if not voice:
            return False, f"Scene {i} has empty narration."
        if not entity or entity.lower() == "none":
            return False, f"Scene {i} has no primary visual entity."
        if len(_words(prompt)) < 2:
            return False, f"Scene {i} has an unusable visual search prompt."
        if not intent:
            return False, f"Scene {i} has no visual intent."
        if not category:
            return False, f"Scene {i} has no topic category."
        if role not in allowed_roles:
            return False, f"Scene {i} has an invalid narrative role."

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
    if recommended == 0:
        recommended = 1
        script_data["recommended_title_index"] = recommended
    if recommended not in (1, 2, 3):
        return False, "Recommended title index is invalid."

    description = str(script_data.get("seo_description", "")).strip()
    if len(_words(description)) < 10:
        return False, "SEO description is too short."

    creator_insight = str(script_data.get("creator_insight", "")).strip()
    if len(_words(creator_insight)) < 6:
        return False, "Creator insight is missing or too short."

    editorial_angle = str(script_data.get("editorial_angle", "")).strip()
    if not editorial_angle:
        return False, "Editorial angle is missing."

    return True, "Passed deterministic Shorts QC"


def _quality_validate(original_validate, script_data, source_text, format_mode):
    """Run the existing validator plus deterministic non-numeric script QC."""
    ok, message = original_validate(script_data, source_text, format_mode)
    if not ok:
        return ok, message
    return validate_deterministic_script_quality(script_data, format_mode)

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
    except Exception as exc:
        score -= 1
        reasons.append(f"narrative critique unavailable: {type(exc).__name__}")

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
