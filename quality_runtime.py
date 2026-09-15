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
    """Run the factory's existing validator plus cheap deterministic checks."""
    ok, message = original_validate(script_data, source_text, format_mode)
    if not ok:
        return ok, message

    scenes = script_data.get("script", [])
    if not scenes:
        return False, "Script contains no scenes."

    # Every generated scene must have actual narration and visual direction.
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

    # Prevent the model from repeating the same scene in different wording.
    for i in range(len(scenes)):
        for j in range(i + 1, len(scenes)):
            similarity = difflib.SequenceMatcher(
                None,
                _normalise(scenes[i].get("voiceover", "")),
                _normalise(scenes[j].get("voiceover", "")),
            ).ratio()
            if similarity >= 0.88:
                return False, f"Scenes {i+1} and {j+1} are near-duplicates."

    # The first scene must be the factual hook, not a generic creator intro.
    first = _normalise(scenes[0].get("voiceover", ""))
    forbidden_openers = (
        "welcome to", "hey everyone", "hey guys", "today we are going to",
        "in this video", "let us talk about", "here is a crisp script",
        "you will not believe", "you won't believe", "stop scrolling",
    )
    if any(first.startswith(x) for x in forbidden_openers):
        return False, "Scene 1 starts with a generic or performative opener."

    # Final scene must contain the factory's required CTA/question structure.
    final = str(scenes[-1].get("voiceover", "")).strip()
    if "like, share, and subscribe" not in _normalise(final):
        return False, "Final scene is missing the required CTA."

    # Metadata sanity checks prevent malformed output from reaching rendering.
    titles = script_data.get("titles")
    if not isinstance(titles, list) or len(titles) != 3:
        return False, "Exactly three titles are required."
    if any(not str(t).strip() for t in titles):
        return False, "One or more generated titles are empty."
    if any("#shorts" not in str(t).lower() for t in titles):
        return False, "Every generated title must contain #shorts."

    recommended = script_data.get("recommended_title_index")
    if recommended not in (0, 1, 2, 3):
        return False, "Recommended title index is invalid."

    description = str(script_data.get("seo_description", "")).strip()
    if len(_words(description)) < 10:
        return False, "SEO description is too short."

    # For normal deep-dives/trending stories, each middle scene should retain
    # meaningful source overlap. The original validator already catches most
    # weak bridges; this catches scripts that merely repeat the headline.
    source_words = set(_words(source_text))
    if len(source_words) >= 12 and format_mode in ("regular", "trending"):
        weak = 0
        for scene in scenes[1:-1]:
            scene_words = set(_words(scene.get("voiceover", "")))
            if len(scene_words & source_words) < 2:
                weak += 1
        if weak > 2:
            return False, "Too many middle scenes are weakly grounded in the source."

    return True, "Passed deterministic Shorts QC"


def _self_critique(script_data, format_mode):
    """Return a transparent rule-based score instead of a hard-coded 8/10."""
    scenes = script_data.get("script", []) if isinstance(script_data, dict) else []
    if not scenes:
        return 0, "No scenes"

    score = 10.0
    reasons = []
    first = _normalise(scenes[0].get("voiceover", ""))
    if any(first.startswith(x) for x in ("welcome to", "hey everyone", "today we are going to", "in this video")):
        score -= 2
        reasons.append("generic opener")
    if "like, share, and subscribe" not in _normalise(scenes[-1].get("voiceover", "")):
        score -= 2
        reasons.append("missing CTA")
    if len(scenes) < (7 if format_mode == "top5" else 5):
        score -= 2
        reasons.append("too few scenes")
    for i in range(len(scenes)):
        for j in range(i + 1, len(scenes)):
            if difflib.SequenceMatcher(None, _normalise(scenes[i].get("voiceover")), _normalise(scenes[j].get("voiceover"))).ratio() >= 0.88:
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
    print("   [QC] Deterministic Shorts script quality control enabled.")
    return bot
