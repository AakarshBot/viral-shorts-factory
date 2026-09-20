        elif isinstance(value, dict):
            for key in ("text","content","extracted_text","body","summary","snippet","title","claim","claims","evidence","source_text","sources","articles","items"):
                if key in value: collect(value[key])
        elif isinstance(value, (list, tuple)):
            for item in value: collect(item)
    pack = story.get("research_evidence_pack")
    collect(pack.get("sources") if isinstance(pack, dict) else pack)
    for key in ("research_evidence_text","research_bundle","text","summary","description"):
        collect(story.get(key))
    seen, unique = set(), []
    for value in values:
        clean = re.sub(r"\s+", " ", value).strip()
        if clean and clean not in seen:
            seen.add(clean); unique.append(clean)
    return unique


def _longest_originality_run(left, right):
    previous = [0] * (len(right) + 1)
    best = 0
    for token in left:
        current = [0]
        for index, other in enumerate(right, 1):
            current.append(previous[index - 1] + 1 if token == other else 0)
            best = max(best, current[-1])
        previous = current
    return best


def check_script_originality(script_data, story_data):
    sources = _originality_sources(story_data)
    failures = []
    for scene_index, scene in enumerate(script_data.get("script", []) if isinstance(script_data, dict) else [], 1):
        if not isinstance(scene, dict) or scene.get("human_contributed"): continue
        words = _originality_words(scene.get("voiceover"))
        sixgrams = {tuple(words[i:i+6]) for i in range(max(0, len(words)-5))}
        for source_index, source in enumerate(sources):
            source_words = _originality_words(source)
            source_sixgrams = {tuple(source_words[i:i+6]) for i in range(max(0, len(source_words)-5))}
            longest = _longest_originality_run(words, source_words)
            ratio = len(sixgrams & source_sixgrams) / max(1, len(sixgrams))
            # Proper names and unavoidable factual phrases can overlap. Flag only
            # substantial contiguous reuse or a high proportion of matching phrasing.
            if longest >= 10 or (longest >= 7 and ratio > 0.20):
                failures.append({"scene": scene_index, "source_index": source_index, "longest_run": longest, "sixgram_ratio": ratio})
                break
    return {"passed": not failures, "failures": failures, "source_count": len(sources)}


def _normalise(text): return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())).strip()
def _words(text): return re.findall(r"[A-Za-z0-9]+", str(text or "").lower())

def _script_scene_bounds(format_mode):
    return (
        (7, 7)
        if str(format_mode or "").lower() == "top5"
        else (SCRIPT_MIN_SCENES, SCRIPT_MAX_SCENES)
    )