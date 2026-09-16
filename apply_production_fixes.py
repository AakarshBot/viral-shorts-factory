from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
FILES = ["app.py", "workflow_runtime.py", "story_ranker.py", "script_runtime.py"]

for name in FILES:
    src = ROOT / name
    if not src.exists():
        raise SystemExit(f"Missing required file: {src}")
    shutil.copy2(src, ROOT / f"{name}.pre_topic_fix.bak")


def replace_exact(path, old, new, label):
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"PATCH ABORTED: {label}: expected 1 match, found {count} in {path.name}")
    path.write_text(text.replace(old, new), encoding="utf-8")
    print(f"[PATCH] {label}")

# 1) Dashboard: expose and persist an explicit requested topic in Cricket mode.
app = ROOT / "app.py"
replace_exact(
    app,
    '''    if st.session_state.format_label == "Cricket":\n        st.selectbox("Cricket category", list(CRICKET_CATEGORIES.keys()), key="cricket_category")\n        st.caption("Cricket is a dedicated pipeline. The AI-assisted option searches for the strongest cricket story available today.")\n''',
    '''    if st.session_state.format_label == "Cricket":\n        st.selectbox("Cricket category", list(CRICKET_CATEGORIES.keys()), key="cricket_category")\n        st.text_input(\n            "Specific cricket topic (optional)",\n            placeholder="e.g. BCCI to suspend Impact Player rule",\n            key="requested_topic",\n            help="When supplied, discovery is locked to this topic instead of selecting any broad cricket story.",\n        )\n        st.caption("Leave the topic blank for AI-assisted broad cricket discovery. Enter a topic to force topic-specific discovery.")\n''',
    "Cricket topic input",
)
replace_exact(
    app,
    '''            "cricket_pipeline": True,\n            "cricket_category": st.session_state.get("cricket_category", "AI-assisted top story in cricket"),\n            "language_label": language_label,\n''',
    '''            "cricket_pipeline": True,\n            "cricket_category": st.session_state.get("cricket_category", "AI-assisted top story in cricket"),\n            "requested_topic": str(st.session_state.get("requested_topic", "") or "").strip(),\n            "language_label": language_label,\n''',
    "Persist requested topic",
)

# 2) Workflow: use an explicit requested topic instead of the broad cricket query.
workflow = ROOT / "workflow_runtime.py"
replace_exact(
    workflow,
    '''        custom_q = cricket_cfg["query"]\n        custom_rss = cricket_cfg["rss"]\n''',
    '''        requested_topic = str(web_config.get("requested_topic", "") or "").strip()\n        custom_q = requested_topic or cricket_cfg["query"]\n        custom_rss = cricket_cfg["rss"]\n''',
    "Use requested topic for cricket discovery",
)
replace_exact(
    workflow,
    '''    print(f"   [Workflow] Discovery only: format={fmt}, category={category}, language={language}", flush=True)\n''',
    '''    requested_topic = str(web_config.get("requested_topic", "") or "").strip()\n    if requested_topic:\n        print(f"   [Workflow] Requested topic locked: {requested_topic}", flush=True)\n    print(f"   [Workflow] Discovery only: format={fmt}, category={category}, language={language}", flush=True)\n''',
    "Report requested topic",
)

# 3) Story ranker: reject unrelated stories before editorial ranking.
story = ROOT / "story_ranker.py"
insert_after = '''def _apply_sports_niche_bonus(story, target_category):\n    if _clean(target_category) != "sports":\n        return 0.0\n    return 3.0 if any(term in _text_blob(story) for term in SPORTS_NICHE_TERMS) else 0.0\n\n\n'''
helper = '''def _requested_topic_pass(story, requested_topic):\n    topic_terms = _tokens(requested_topic)\n    if not topic_terms:\n        return True\n    story_terms = _tokens(_text_blob(story))\n    overlap = len(topic_terms & story_terms)\n    required = 2 if len(topic_terms) >= 3 else 1\n    if overlap >= required:\n        story["requested_topic_overlap"] = overlap\n        return True\n    story["discovery_rejection"] = "Does not match requested topic"\n    story["requested_topic_overlap"] = overlap\n    return False\n\n\ndef _cricket_relevance_pass(story, genre_key):\n    if _clean(genre_key) != "sports_stories_of_day":\n        return True\n    story_terms = _tokens(_text_blob(story))\n    if story_terms & {term for term in CRICKET_TERMS if " " not in term}:\n        return True\n    if "test cricket" in _text_blob(story) or "formula 1" in _text_blob(story):\n        return False\n    story["discovery_rejection"] = "Not cricket-relevant"\n    return False\n\n\n'''
replace_exact(story, insert_after, insert_after + helper, "Add topic and cricket relevance gates")

replace_exact(
    story,
    '''        config = getattr(bot, "_active_web_config", {}) or {}\n        ai_cricket = genre_key == "sports_stories_of_day" and str(config.get("cricket_category", "")) == "AI-assisted top story in cricket"\n        ranked = rank_story_candidates(\n''',
    '''        config = getattr(bot, "_active_web_config", {}) or {}\n        requested_topic = str(config.get("requested_topic", "") or "").strip()\n        relevance_filtered = []\n        for candidate in compact:\n            if not _cricket_relevance_pass(candidate, genre_key):\n                continue\n            if not _requested_topic_pass(candidate, requested_topic):\n                continue\n            relevance_filtered.append(candidate)\n        if requested_topic or genre_key == "sports_stories_of_day":\n            print(\n                f"   [Discovery Relevance] {len(compact)} intake -> {len(relevance_filtered)} topic/category-relevant candidates",\n                flush=True,\n            )\n        compact = relevance_filtered\n        ai_cricket = genre_key == "sports_stories_of_day" and str(config.get("cricket_category", "")) == "AI-assisted top story in cricket"\n        ranked = rank_story_candidates(\n''',
    "Apply relevance gates before ranking",
)

# 4) Content density: allow declared primary entities / visual prompts to carry grounding.
script = ROOT / "script_runtime.py"
replace_exact(
    script,
    '''    topic_terms = _topic_terms(story_data)\n    all_words = []\n    filler_hits = []\n    for index, scene in enumerate(scenes, 1):\n        text = str(scene.get("voiceover", "")).strip()\n        words = _words(text)\n        all_words.extend(words)\n''',
    '''    topic_terms = _topic_terms(story_data)\n    grounding_words = []\n    filler_hits = []\n    for index, scene in enumerate(scenes, 1):\n        text = str(scene.get("voiceover", "")).strip()\n        words = _words(text)\n        grounding_words.extend(words)\n        grounding_words.extend(_words(scene.get("primary_entity", "")))\n        grounding_words.extend(_words(scene.get("specific_search_prompt", "")))\n''',
    "Grounding includes scene entities",
)
replace_exact(
    script,
    '''    if topic_terms and len(set(all_words) & topic_terms) < min(2, len(topic_terms)):\n        return False, "Narration is not sufficiently grounded in the selected topic."\n''',
    '''    if topic_terms:\n        overlap = len(set(grounding_words) & topic_terms)\n        required = 1 if any(\n            set(_words(str(scene.get("primary_entity", "")))) & topic_terms\n            for scene in scenes\n        ) else min(2, len(topic_terms))\n        if overlap < required:\n            return False, "Narration is not sufficiently grounded in the selected topic."\n''',
    "Robust content grounding gate",
)

print("\nPRODUCTION FIXES APPLIED")
print("Backups created with .pre_topic_fix.bak")
