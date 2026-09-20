    return [left, right]


def repair_script_structure(script_data, format_mode):
    """Repair scene-count/word-count defects without inventing narration."""
    if not isinstance(script_data, dict) or not isinstance(script_data.get("script"), list):
        return None, {"changed": False, "reason": "script is missing or malformed"}

    minimum, maximum = _script_scene_bounds(format_mode)
    original_scenes = [scene for scene in script_data.get("script") if isinstance(scene, dict)]
    if not original_scenes:
        return None, {"changed": False, "reason": "script contains no scenes"}

    if (
        minimum <= len(original_scenes) <= maximum
        and all(
            SCENE_MIN_WORDS <= _scene_word_count(scene.get("voiceover")) <= SCENE_MAX_WORDS
            for scene in original_scenes
        )
    ):
        return script_data, {"changed": False, "reason": "scene contract already satisfied"}

    expanded = []
    for scene in original_scenes:
        voiceover = str(scene.get("voiceover") or "").strip()
        parts = _split_scene_text(voiceover)
        if not parts:
            parts = [voiceover] if 8 <= _scene_word_count(voiceover) <= 30 else []
        for part in parts:
            copy = dict(scene)
            copy["voiceover"] = part
            expanded.append(copy)

    if not expanded:
        return None, {"changed": False, "reason": "no scene text can satisfy the word contract"}

    while len(expanded) < minimum:
        candidate_index = max(
            range(len(expanded)),
            key=lambda index: _scene_word_count(expanded[index].get("voiceover")),
            default=-1,
        )
        if candidate_index < 0:
            break
        pieces = _split_scene_at_midpoint(expanded[candidate_index].get("voiceover"))
        if not pieces:
            break
        original = expanded[candidate_index]
        expanded[candidate_index:candidate_index + 1] = [
            dict(original, voiceover=pieces[0]),
            dict(original, voiceover=pieces[1]),
        ]

    while len(expanded) > maximum:
        best_pair = None
        best_size = None
        for index in range(len(expanded) - 1):
            combined = (
                str(expanded[index].get("voiceover") or "").strip()
                + " "
                + str(expanded[index + 1].get("voiceover") or "").strip()
            ).strip()
            size = _scene_word_count(combined)
            if SCENE_MIN_WORDS <= size <= SCENE_MAX_WORDS and (best_size is None or size < best_size):
                best_pair = index
                best_size = size
        if best_pair is None:
            break
        left = expanded[best_pair]
        right = expanded[best_pair + 1]
        merged = dict(
            left,
            voiceover=(
                str(left.get("voiceover") or "").strip()
                + " "
                + str(right.get("voiceover") or "").strip()
            ).strip(),
        )
        expanded[best_pair:best_pair + 2] = [merged]

    if len(expanded) < minimum or len(expanded) > maximum:
        return None, {
            "changed": False,
            "reason": f"could not safely reach {minimum}-{maximum} scenes from supplied narration",
        }
    if not all(
        SCENE_MIN_WORDS <= _scene_word_count(scene.get("voiceover")) <= SCENE_MAX_WORDS
        for scene in expanded
    ):
        return None, {"changed": False, "reason": "repaired scenes still violate the word contract"}

    repaired = dict(script_data)