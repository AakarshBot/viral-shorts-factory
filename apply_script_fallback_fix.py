from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATH = ROOT / "script_runtime.py"
if not PATH.exists():
    raise SystemExit(f"Missing required file: {PATH}")

backup = ROOT / "script_runtime.py.pre_script_fallback.bak"
backup.write_text(PATH.read_text(encoding="utf-8"), encoding="utf-8")

text = PATH.read_text(encoding="utf-8")

marker = "\ndef wrap_write_script(bot):\n"
if marker not in text:
    raise SystemExit("PATCH ABORTED: wrap_write_script marker not found")

helper = r'''

def _extractive_script_fallback(story_data, language_cfg, genre_key, format_mode):
    """Build a strictly source-grounded emergency script without an LLM.

    This is used only when all configured script providers are exhausted or
    produce unusable output. It never invents facts; it reuses the selected
    story title/source text and gives every scene explicit visual metadata.
    """
    story_data = story_data if isinstance(story_data, dict) else {}
    title = re.sub(r"\s+", " ", str(story_data.get("title") or story_data.get("topic") or "Untitled story")).strip()
    raw_source = " ".join(
        str(story_data.get(key) or "")
        for key in ("text", "summary", "description")
    )
    raw_source = re.sub(r"<[^>]+>", " ", raw_source)
    raw_source = re.sub(r"https?://\S+", " ", raw_source)
    raw_source = re.sub(r"\s+", " ", raw_source).strip()

    combined = " ".join(part for part in (title, raw_source) if part).strip()
    words = combined.split()

    # Keep chunks contiguous and source-derived. We prefer five distinct
    # chunks; when the source is short, use the title as limited overlap.
    chunks = []
    if len(words) >= 40:
        chunk_size = max(8, (len(words) + 4) // 5)
        for start in range(0, len(words), chunk_size):
            chunk = " ".join(words[start:start + chunk_size]).strip()
            if chunk:
                chunks.append(chunk)
            if len(chunks) == 5:
                break
    else:
        sentences = [
            re.sub(r"\s+", " ", s).strip(" -")
            for s in re.split(r"(?<=[.!?])\s+", raw_source)
            if len(re.findall(r"[A-Za-z0-9]+", s)) >= 5
        ]
        chunks.extend(sentences[:5])
        if title and len(chunks) < 5:
            chunks.insert(0, title)
        # Pad only with source-derived combinations, never new factual claims.
        source_fragments = [title] + [s for s in sentences if s != title]
        cursor = 0
        while len(chunks) < 5 and source_fragments:
            a = source_fragments[cursor % len(source_fragments)]
            b = source_fragments[(cursor + 1) % len(source_fragments)]
            candidate = re.sub(r"\s+", " ", f"{a} {b}").strip()
            if candidate and candidate not in chunks:
                chunks.append(candidate)
            cursor += 1
            if cursor > 12:
                break

    # Guarantee five renderable scenes. The selected headline is the only
    # source we may repeat when upstream text is unusually short.
    fallback_seed = title or "Selected story"
    while len(chunks) < 5:
        chunks.append(fallback_seed)

    def fit_words(value, minimum=8, maximum=30):
        parts = value.split()
        if len(parts) > maximum:
            value = " ".join(parts[:maximum])
            parts = value.split()
        if len(parts) < minimum:
            seed_parts = fallback_seed.split()
            while len(parts) < minimum and seed_parts:
                parts.append(seed_parts[(len(parts) - minimum) % len(seed_parts)])
        return " ".join(parts[:maximum]).strip()

    # Use the strongest obvious entity token from the headline/source.
    entity = ""
    for token in re.findall(r"\b[A-Z][A-Za-z0-9&.-]{2,}\b", title):
        if token.lower() not in {"The", "This", "After", "Report", "Latest"}:
            entity = token
            break
    if not entity:
        entity = title.split(":", 1)[0].strip()[:80] or "Selected story"

    category_label = str(genre_key or "news").replace("_", " ").title()
    search_prompt = re.sub(r"[|#]+", " ", title).strip()
    if len(search_prompt.split()) < 3:
        search_prompt = f"{search_prompt} {category_label}".strip()

    scenes = []
    for idx in range(5):
        voiceover = fit_words(chunks[idx])
        scenes.append({
            "voiceover": voiceover,
            "primary_entity": entity,
            "visual_intent": "news_event" if idx else "editorial_person",
            "specific_search_prompt": search_prompt,
            "sport_or_topic_category": category_label,
        })

    clean_title = title[:92].strip()
    description_source = re.sub(r"\s+", " ", raw_source or title).strip()
    description_source = description_source[:700]
    return {
        "step_1_headline": title,
        "step_2_data_points": raw_source or title,
        "step_3_critique": "Deterministic source-grounded fallback used because script providers were unavailable.",
        "step_4_metadata": entity,
        "titles": [
            f"{clean_title} #shorts",
            f"{clean_title} | Latest Update #shorts",
            f"{clean_title} | What We Know #shorts",
        ],
        "recommended_title_index": 1,
        "seo_description": f"{description_source}\n\n#News #Sports #Trending",
        "tags": [entity, category_label, "Shorts"],
        "pinned_comment": "What do you make of this latest development?",
        "hook_type": "Direct Factual Headline",
        "hook_style_used": "Direct Factual Headline",
        "structure_used": "Source-grounded explainer",
        "persona_used": "Analytical Insider",
        "script": scenes,
        "fallback_mode": "extractive_source_grounded",
    }
'''

text = text.replace(marker, helper + marker, 1)

old = r'''    def write_script(story_data, language_cfg, genre_key, conn, format_mode):
        contracted_story = _add_editorial_contract(story_data, format_mode)
        result = current(contracted_story, language_cfg, genre_key, conn, format_mode)
        cleaned, diagnostics = clean_script_data(result, story_data, format_mode)
        if diagnostics["changed_scenes"] or diagnostics["removed_scenes"]:
            print(
                "   [Script QC] Structural cleanup: "
                f"{diagnostics['changed_scenes']} scene(s) edited, {diagnostics['removed_scenes']} scene(s) removed.",
                flush=True,
            )
        ok, reason = validate_content_density(cleaned, story_data, format_mode)
        if not ok:
            raise ValueError(f"Content-density gate failed: {reason}")
        return cleaned
'''

new = r'''    def write_script(story_data, language_cfg, genre_key, conn, format_mode):
        contracted_story = _add_editorial_contract(story_data, format_mode)
        result = current(contracted_story, language_cfg, genre_key, conn, format_mode)

        cleaned, diagnostics = clean_script_data(result, story_data, format_mode)
        if diagnostics["changed_scenes"] or diagnostics["removed_scenes"]:
            print(
                "   [Script QC] Structural cleanup: "
                f"{diagnostics['changed_scenes']} scene(s) edited, {diagnostics['removed_scenes']} scene(s) removed.",
                flush=True,
            )

        ok, reason = validate_content_density(cleaned, story_data, format_mode)
        if not ok:
            print(
                f"   [Script Fallback] AI script unavailable/invalid ({reason}). "
                "Using deterministic source-grounded fallback.",
                flush=True,
            )
            fallback = _extractive_script_fallback(story_data, language_cfg, genre_key, format_mode)
            cleaned, fallback_diag = clean_script_data(fallback, story_data, format_mode)
            ok, reason = validate_content_density(cleaned, story_data, format_mode)
            if not ok:
                raise ValueError(f"Content-density gate failed after deterministic fallback: {reason}")
            cleaned["fallback_diagnostics"] = fallback_diag
            return cleaned

        return cleaned
'''

if text.count(old) != 1:
    raise SystemExit(f"PATCH ABORTED: expected 1 wrap body match, found {text.count(old)}")
text = text.replace(old, new, 1)
PATH.write_text(text, encoding="utf-8")
print("[PATCH] Deterministic source-grounded script fallback")
print("[PATCH] AI script failure no longer immediately terminates production")
print(f"[BACKUP] {backup}")
print("\nSCRIPT FALLBACK FIX APPLIED")
