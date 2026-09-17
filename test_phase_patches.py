"""Test-only diagnostics layered onto the step-isolated Test Phase.

Production functions are not changed here. The Test Phase gets:
- visible progress bars around long-running checks;
- an offline source-grounded script test with zero model/API calls;
- a local topic-selection diagnostic with zero discovery API calls;
- reuse of the latest output audio with zero voice API calls;
- three planned visual search terms for every slide, while executing exactly one
  real visual fetch for one randomly selected slide.
"""
from __future__ import annotations

import os
import random
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import streamlit as st

import script_guard_runtime
import test_phase_runtime
import visual_runtime
from newsroom_dashboard import _collect_visual_items, _script_text
from visual_policy_runtime import _clean_search_subject


def _run_with_progress(label: str, fn: Callable[[], Any]) -> Any:
    """Show a simple diagnostic progress bar while executing a real test."""
    st.caption(f"⏳ {label}")
    bar = st.progress(0, text=f"{label}: starting")
    bar.progress(0.18, text=f"{label}: preparing")
    try:
        bar.progress(0.40, text=f"{label}: running")
        result = fn()
        bar.progress(0.82, text=f"{label}: checking result")
        bar.progress(1.0, text=f"{label}: complete")
        return result
    except Exception:
        bar.progress(1.0, text=f"{label}: failed")
        raise


def _wrap_for_progress(owner: Any, attr: str, label: str):
    """Temporarily wrap a callable so the Test Phase shows real run progress."""
    original = getattr(owner, attr)
    if not callable(original):
        return lambda: None
    state = {"restored": False}

    def wrapped(*args, **kwargs):
        try:
            return _run_with_progress(label, lambda: original(*args, **kwargs))
        finally:
            if not state["restored"]:
                try:
                    setattr(owner, attr, original)
                except Exception:
                    pass
                state["restored"] = True

    setattr(owner, attr, wrapped)
    return lambda: setattr(owner, attr, original)


def _clean_subject(value: Any) -> str:
    """Use the same deterministic subject sanitizer as the production visual path."""
    return _clean_search_subject(value)


def _scene_phrase(seg: dict[str, Any], limit: int = 6) -> str:
    """Extract useful visual words from one narration segment, not sentence debris."""
    raw = str(seg.get("voiceover", ""))
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", raw)
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "into", "after", "before", "about",
        "they", "their", "there", "here", "when", "what", "which", "where", "while", "have", "has",
        "had", "will", "would", "could", "should", "just", "been", "were", "was", "are", "our",
        "you", "your", "today", "is", "a", "an", "to", "of", "in", "on", "as", "it", "its", "these", "those",
        "he", "she", "his", "her", "them", "than", "then", "also", "can", "may", "might", "more", "most",
    }
    sentence_cues = {
        "every", "each", "because", "given", "since", "but", "or", "so", "if", "although", "though",
        "we", "try", "tried", "tries", "get", "gets", "got", "getting", "happen", "happens", "happened",
        "year", "years", "season", "don't", "doesn't", "didn't", "isn't", "aren't", "wasn't", "weren't",
    }
    meaningful: list[str] = []
    for word in words:
        lower = word.lower().strip(".,!?;:")
        if lower in sentence_cues and meaningful:
            break
        if lower not in stop:
            meaningful.append(word)
        if len(meaningful) >= limit:
            break
    return " ".join(meaningful).strip(" ,.-")


def _content_chunks(seg: dict[str, Any], video_title: str, entity: str) -> list[str]:
    """Build short, searchable scene phrases from prompt, narration and title."""
    entity_tokens = {
        t.lower() for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", entity)
    }
    sources = [
        str(seg.get("specific_search_prompt", "")),
        _scene_phrase(seg, limit=8),
        str(video_title or ""),
    ]
    stop = {
        "editorial", "person", "organization", "organisation", "event", "location", "photo", "image",
        "news_event", "news", "real", "high", "resolution", "official", "press", "story", "today",
        "the", "and", "for", "with", "this", "that", "from", "into", "after", "before", "about",
        "every", "each", "because", "given", "since", "but", "or", "so", "if", "although", "though",
        "he", "she", "his", "her", "them", "they", "their", "there", "here", "when", "what", "which", "where",
        "while", "have", "has", "had", "will", "would", "could", "should", "just", "been", "were", "was",
        "are", "our", "you", "your", "today", "is", "a", "an", "to", "of", "in", "on", "as", "it", "its",
        "these", "those", "can", "may", "might", "more", "most", "than", "then", "also",
    }
    chunks: list[str] = []
    for source in sources:
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", source)
        useful: list[str] = []
        for token in tokens:
            low = token.lower().strip(".,!?;:")
            if low in entity_tokens or low in stop:
                continue
            if len(low) <= 1:
                continue
            useful.append(token)
        if useful:
            for start in range(0, len(useful), 3):
                chunk = " ".join(useful[start:start + 4]).strip()
                if len(chunk.split()) >= 2:
                    clean = re.sub(r"\s+", " ", chunk).strip(" ,.-")
                    if clean and clean.lower() not in {x.lower() for x in chunks}:
                        chunks.append(clean)
                if len(chunks) >= 6:
                    return chunks
    return chunks


def _three_visual_terms(seg: dict[str, Any], video_title: str, used_terms: set[str] | None = None) -> list[str]:
    """Produce three distinct, scene-specific diagnostic search terms without an API call."""
    entity = _clean_subject(seg.get("primary_entity", ""))
    title = _clean_subject(video_title)
    visual_type = _clean_subject(seg.get("visual_type", ""))
    intent = _clean_subject(seg.get("visual_intent", ""))
    chunks = _content_chunks(seg, video_title, entity)
    used = used_terms if used_terms is not None else set()

    modifiers = {
        "PERSON": "portrait",
        "ORGANIZATION": "headquarters",
        "EVENT": "event photo",
        "LOCATION": "location photo",
        "PRODUCT": "product photo",
        "STATISTIC": "chart",
        "COMPARISON": "comparison",
        "TIMELINE": "historical photo",
        "PROCESS": "diagram",
        "QUOTE": "press conference",
        "DOCUMENT": "official document",
        "CONCEPT": "concept illustration",
    }

    terms: list[str] = []

    def add(parts: list[str]) -> None:
        clean = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip(" ,.-")
        key = clean.lower()
        if not clean or key in {t.lower() for t in terms} or key in used:
            return
        if len(clean.split()) > 8:
            clean = " ".join(clean.split()[:8])
            key = clean.lower()
        terms.append(clean)
        used.add(key)

    if entity:
        for chunk in chunks:
            add([entity, chunk])
            if len(terms) >= 3:
                break
        if len(terms) < 3:
            add([entity, intent or modifiers.get(visual_type, "editorial photo")])
        if len(terms) < 3:
            add([entity, title])
        if len(terms) < 3:
            add([entity, modifiers.get(visual_type, "editorial photo")])
    else:
        for chunk in chunks:
            add([chunk])
            if len(terms) >= 3:
                break
        if len(terms) < 3:
            add([title])
        if len(terms) < 3:
            add([intent or modifiers.get(visual_type, "editorial photo")])

    if not terms:
        terms = [title or "selected story"]
    fallback_pool = [
        [entity, modifiers.get(visual_type, "editorial photo")],
        [entity, "press photo"],
        [entity, "documentary photo"],
        [title, "editorial photo"],
    ]
    for parts in fallback_pool:
        if len(terms) >= 3:
            break
        add(parts)

    while len(terms) < 3:
        extra = f"{entity or title or 'selected story'} editorial context"
        if extra.lower() not in {t.lower() for t in terms}:
            terms.append(extra)
        else:
            terms.append(terms[-1])

    return terms[:3]


def _offline_script_test(bot) -> None:
    """Build the same source-grounded emergency script path without any API call."""
    with st.expander("2. Script writing — offline / no-API test", expanded=True):
        st.info("This test deliberately makes **zero AI/model API calls**. It exercises the factory's source-grounded fallback and narration guard.")
        title = st.text_input("Story title", key="tp_offline_story_title", placeholder="Example: RBI changes liquidity rules")
        topic = st.text_input("Topic", key="tp_offline_story_topic", placeholder="Example: Reserve Bank of India policy update")
        summary = st.text_area("Source/article text", key="tp_offline_story_summary", height=180, placeholder="Paste the article/source text to test script extraction.")
        url = st.text_input("Article URL (optional)", key="tp_offline_story_url")
        if st.button("▶ Run offline script test", key="tp_run_script_offline", type="primary", use_container_width=True):
            if not st.session_state.tp_config:
                st.warning("Select the Test Phase format/language/category first.")
                return
            config = st.session_state.tp_config
            story = {
                "title": title.strip(),
                "topic": topic.strip(),
                "summary": summary.strip(),
                "text": summary.strip(),
                "url": url.strip(),
                "source": "Offline diagnostic input",
            }
            if not (story["title"] or story["topic"] or story["text"]):
                st.error("Provide a title, topic, or source/article text first.")
                return

            def run():
                return script_guard_runtime.source_only_fallback(
                    story,
                    bot.LANGUAGES[config["language"]],
                    config["category"],
                    config["format_mode"],
                )

            st.session_state.tp_script = _run_with_progress("Offline script test", run)
            st.session_state.tp_story = story
            st.success("Offline script test completed with zero API calls.")

        script = st.session_state.get("tp_script")
        if script:
            st.text_area("Narration preview", _script_text(script), height=320, disabled=True, key="tp_script_offline_preview")
            with st.expander("Offline script object", expanded=False):
                st.json(script)


def _visual_test_limited(bot) -> None:
    """Plan terms for every slide, then perform exactly one real image fetch.

    Test-only visual policy: bypass semantic Gemini QA and cache reuse so the
    diagnostic proves the real image-fetch/render path while consuming only the
    single intended visual fetch path.
    """
    with st.expander("4. Visual sourcing — one-image diagnostic", expanded=True):
        script = st.session_state.get("tp_script")
        if not script:
            st.warning("Generate a script first.")
            return
        scenes = script.get("script", []) if isinstance(script, dict) else []
        if not isinstance(scenes, list) or not scenes:
            st.warning("The script contains no scenes to inspect.")
            return

        video_title = str(script.get("title", "") or (script.get("titles") or [""])[0])
        st.markdown("**Planned search terms — every slide**")
        planned: list[list[str]] = []
        used_terms: set[str] = set()
        for index, scene in enumerate(scenes, 1):
            terms = _three_visual_terms(scene if isinstance(scene, dict) else {}, video_title, used_terms)
            planned.append(terms)
            st.write(f"Slide {index}: `1.` {terms[0]}  ·  `2.` {terms[1]}  ·  `3.` {terms[2]}")

        possible = [i for i in (2, 3) if i < len(scenes)]
        target_index = random.choice(possible) if possible else len(scenes) - 1
        target_terms = planned[target_index]
        selected_term = random.choice(target_terms)
        st.info(f"Only **one image** will be sourced: randomly selected slide **{target_index + 1}**, using one of its three planned terms: **{selected_term}**.")

        if st.button("▶ Run one-image visual test", key="tp_run_visuals_one", type="primary", use_container_width=True):
            config = st.session_state.tp_config
            test_scene = dict(scenes[target_index]) if isinstance(scenes[target_index], dict) else {}
            test_scene["primary_entity"] = selected_term
            test_scene["visual_subject"] = selected_term
            test_scene["specific_search_prompt"] = selected_term
            test_scene["query"] = selected_term
            one_scene_script = dict(script)
            one_scene_script["script"] = [test_scene]

            original_gate = visual_runtime._strict_gate
            original_cache = visual_runtime.get_cached_asset

            def local_test_gate(bot_obj, img_bytes, seg, video_title="", source=""):
                if not img_bytes or not visual_runtime._local_visual_sanity(img_bytes):
                    return False, "LOCAL-REJECT", 0, True
                return True, "TEST-LOCAL", 100, False

            try:
                visual_runtime._strict_gate = local_test_gate
                visual_runtime.get_cached_asset = lambda *args, **kwargs: (None, None)

                def run():
                    result = bot.process_visuals_async(
                        one_scene_script,
                        bot.LANGUAGES[config["language"]],
                        config["format_mode"],
                    )
                    return test_phase_runtime._run_async(result) or []

                st.session_state.tp_visuals = _run_with_progress("One-image visual test", run)
                st.success("Visual diagnostic fetched one image only. Gemini semantic QA was skipped for this test.")
            finally:
                visual_runtime._strict_gate = original_gate
                visual_runtime.get_cached_asset = original_cache

        items = _collect_visual_items(st.session_state.get("tp_visuals") or [])
        if items:
            for item in items[:1]:
                st.image(item["path"], caption=f"Diagnostic image · {item['label']}", use_container_width=True)
        elif not st.session_state.get("tp_visuals"):
            st.caption("No image has been sourced yet.")


def _render_test(bot) -> None:
    with st.expander("5. Subs / overlays — real compile/render test", expanded=True):
        script = st.session_state.get("tp_script")
        visuals = st.session_state.get("tp_visuals") or []
        audio = st.session_state.get("tp_audio")
        if not script or not visuals or audio is None:
            st.warning("This test needs a script, sourced visuals, and reused previous-run audio. Complete steps 2–4 first.")
            return
        st.caption(
            "This calls the same compile_video() function used by the factory. "
            "The render path owns subtitles, overlays, branding and final frame composition."
        )
        if st.button("▶ Run real subtitle/overlay render", key="tp_run_render", type="primary", use_container_width=True):
            def run():
                audio_paths, word_timings = audio
                config = st.session_state.tp_config
                return bot.compile_video(
                    visuals,
                    audio_paths,
                    word_timings,
                    bot.LANGUAGES[config["language"]],
                    config["format_mode"],
                )
            st.session_state.tp_rendered = str(_run_with_progress("Subtitle/overlay render test", run) or "")
        path = st.session_state.get("tp_rendered", "")
        if path and os.path.isfile(path):
            st.video(path)
            st.success(f"Render test passed: {path}")
        else:
            st.caption("Nothing has been rendered yet.")


def _local_topic_candidates(bot) -> None:
    """Exercise topic UI/state locally without any network discovery call."""
    with st.expander("1. Topic choosing — offline / no-API test", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            format_options = ["Deep Dive", "Top 5", "Cricket"]
            format_label = st.selectbox("Format", format_options, key="tp_format")
            labels = [cfg["label"] for cfg in bot.LANGUAGES.values()]
            lang_label = st.selectbox("Language", labels, key="tp_language")
        with c2:
            category_options = [
                ("National & Global Affairs", "national_global_affairs"),
                ("Technology", "technology"),
                ("Sports", "sports"),
                ("Business & Finance", "business_finance"),
                ("Entertainment", "entertainment"),
            ]
            category_label = st.selectbox("Category", [label for label, _ in category_options], key="tp_category")
            requested_topic = st.text_input("Local test topic", key="tp_topic", placeholder="Example: India's next big AI breakthrough")

        language_key = next((key for key, cfg in bot.LANGUAGES.items() if cfg["label"] == lang_label), "english")
        format_mode = {"Deep Dive": "regular", "Top 5": "top5", "Cricket": "cricket"}[format_label]
        category_key = dict(category_options)[category_label]
        if format_label == "Cricket":
            category_key = "sports_stories_of_day"

        config = {
            "format_mode": format_mode,
            "display_format": format_label,
            "category": category_key,
            "language": language_key,
            "cricket_pipeline": format_label == "Cricket",
            "cricket_category": "Offline diagnostic",
            "requested_topic": requested_topic.strip(),
            "language_label": lang_label,
        }
        st.session_state.tp_config = config

        topic_seed = requested_topic.strip() or {
            "national_global_affairs": "India's latest major policy change",
            "technology": "The next major AI breakthrough",
            "sports": "A major sports moment everyone is discussing",
            "business_finance": "A major business or market development",
            "entertainment": "The biggest entertainment story today",
            "sports_stories_of_day": "The biggest cricket story today",
        }.get(category_key, "A major story worth knowing")

        candidates = [
            {"title": topic_seed, "source_label": "Offline diagnostic topic", "discovery_reason": "Deterministic local seed — no discovery API called.", "discovery_rank": 1},
            {"title": f"{topic_seed} — what changed", "source_label": "Offline diagnostic variant", "discovery_reason": "Local angle variant — no discovery API called.", "discovery_rank": 2},
            {"title": f"{topic_seed} — what it means", "source_label": "Offline diagnostic variant", "discovery_reason": "Local impact variant — no discovery API called.", "discovery_rank": 3},
        ]
        st.session_state.tp_candidates = candidates

        labels = [f"#{item['discovery_rank']} — {item['title']}" for item in candidates]
        selected = st.radio("Choose a local test story", labels, key="tp_selected_candidate")
        selected_index = labels.index(selected) if selected in labels else 0
        st.session_state.tp_story = candidates[selected_index]

        st.success("Topic diagnostic uses local candidates only — no discovery API call.")


def _audio_test_reuse(bot) -> None:
    """Reuse the newest locally available audio/timing files; never call a voice API."""
    with st.expander("3. Audio — reuse latest local audio", expanded=True):
        candidates = []
        roots = [Path(getattr(bot, "OUTPUT_DIR", "")), Path(getattr(bot, "BASE_DIR", "")) / "output"]
        for root in roots:
            if not root or not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".mp3", ".wav", ".aac", ".m4a"}:
                    candidates.append(path)
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        audio_path = str(candidates[0]) if candidates else ""
        timing = ""
        if audio_path:
            stem = Path(audio_path).with_suffix("")
            for ext in (".json", ".txt"):
                probe = Path(str(stem) + "_timings" + ext)
                if probe.is_file():
                    timing = str(probe)
                    break
        if audio_path:
            st.session_state.tp_audio = ([audio_path], timing)
            st.success(f"Reused local audio: {audio_path}")
        else:
            st.session_state.tp_audio = None
            st.warning("No previous audio file was found locally.")


def _patch_test_runtime(bot) -> None:
    """Install only the Test Phase runtime patches for the current dashboard run."""
    current = getattr(bot, "write_script", None)
    if current and not getattr(current, "_test_phase_patched", False):
        original_write = current

        def offline_write_script(story_data, language_cfg, genre_key, conn, format_mode):
            return script_guard_runtime.source_only_fallback(story_data, language_cfg, genre_key, format_mode)

        offline_write_script._test_phase_patched = True
        bot.write_script = offline_write_script
        bot.run_robot.__globals__["write_script"] = offline_write_script
        bot._test_phase_original_write_script = original_write

    current_visual = getattr(bot, "process_visuals_async", None)
    if current_visual and not getattr(current_visual, "_test_phase_patched", False):
        bot._test_phase_original_visuals = current_visual


def render_test_phase(bot) -> None:
    """Render the complete offline/one-fetch Test Phase UI."""
    _patch_test_runtime(bot)
    _local_topic_candidates(bot)
    _offline_script_test(bot)
    _audio_test_reuse(bot)
    _visual_test_limited(bot)
    _render_test(bot)
