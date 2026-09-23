"""Content-first visual rendering for Shorts.

Every scene is sent through the multi-source retrieval engine. A provider/query
failure only means the next source or phrase is tried; the factory continues
through the complete visual package. AI is deliberately limited, and a tiny
renderer rescue exists only to prevent an empty frame after every real source
has been exhausted.
"""

import io
import os
import re
from PIL import Image, ImageDraw, ImageFont

from branding_runtime import source_credit_for_type
from manual_visual_query_runtime import parse_manual_visual_queries
from visual_licensing_runtime import allow_unlicensed_visuals, provenance, rescue_provenance
from visual_qa_runtime import reset_visual_qa_video_budget, start_visual_qa_scene


def _load_brand_font(bot, size, custom_font_name=None):
    try:
        resolver = getattr(bot, "get_bold_font", None)
        if callable(resolver):
            return resolver(int(size), custom_font_name)
    except Exception:
        pass

    paths = []
    if custom_font_name:
        paths.extend([str(custom_font_name), os.path.join("/usr/share/fonts/truetype", str(custom_font_name))])
    paths.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        r"C:\Windows\Fonts\segoeprb.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ])
    for path in paths:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, int(size))
            except Exception:
                continue
    return ImageFont.load_default()


def _text_size(draw, text, font):
    try:
        left, top, right, bottom = draw.textbbox((0, 0), str(text), font=font)
        return max(1, right - left), max(1, bottom - top)
    except Exception:
        try:
            return max(1, int(draw.textlength(str(text), font=font))), max(1, getattr(font, "size", 20))
        except Exception:
            return max(1, len(str(text)) * 10), max(1, getattr(font, "size", 20))


def _render_hook_card(bot, image, hook_text, font_name=None):
    canvas = image.convert("RGBA")
    width, height = canvas.size
    accent = tuple(getattr(bot, "PALETTE", {}).get("accent_primary", (0, 191, 255)))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([24, 24, width - 24, 30], fill=accent + (180,))

    font_body, wrapped_lines = _fit_hook_text(bot, hook_text, font_name, width - 140)
    line_heights = []
    for line in wrapped_lines:
        bbox = draw.textbbox((0, 0), line, font=font_body)
        line_heights.append(max(1, bbox[3] - bbox[1]))

    line_gap = 18
    total_h = sum(line_heights) + line_gap * max(0, len(wrapped_lines) - 1)
    y = max(180, (height - total_h) / 2)

    for line, line_h in zip(wrapped_lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font_body)
        text_w = bbox[2] - bbox[0]
        x = (width - text_w) / 2
        draw.text((x + 8, y + 8), line, font=font_body, fill=(0, 0, 0, 210), stroke_width=8, stroke_fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=font_body, fill=accent + (250,), stroke_width=5, stroke_fill=(0, 0, 0, 245))
        y += line_h + line_gap

    return Image.alpha_composite(canvas, overlay)


def _fit_hook_text(bot, text, font_name, max_width):
    text = str(text or "").strip() or "THIS STORY MATTERS"
    for size in range(92, 44, -4):
        font = _load_brand_font(bot, size, font_name)
        lines = []
        current = ""
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if not current or draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if lines and len(lines) <= 5:
            return font, lines
    font = _load_brand_font(bot, 44, font_name)
    return font, [text]


_SOURCE_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for",
    "with", "from", "by", "is", "are", "was", "were", "be", "has", "have",
    "had", "this", "that", "these", "those", "news", "latest", "today",
    "report", "reports", "says", "said", "story",
}


def _source_tokens(value):
    words = re.findall(r"[\w-]+", str(value or "").lower(), flags=re.UNICODE)
    return {word for word in words if len(word) > 2 and word not in _SOURCE_STOPWORDS}


def _rank_news_source_scene_indices(scenes, article_title=""):
    """Rank scenes for one article image without assigning a special manual-query slide."""
    article_tokens = _source_tokens(article_title)
    ranked = []
    for index, scene in enumerate(scenes):
        text = " ".join(
            str(scene.get(key, "") or "")
            for key in (
                "primary_entity",
                "voiceover",
                "visual_intent",
                "specific_search_prompt",
                "visual_context",
                "manual_visual_query",
            )
        )
        scene_tokens = _source_tokens(text)
        score = float(len(article_tokens & scene_tokens) * 4)
        entity = str(
            scene.get("factual_primary_entity")
            or scene.get("primary_entity")
            or scene.get("manual_visual_query")
            or ""
        ).strip()
        if entity and _source_tokens(entity) & article_tokens:
            score += 12.0
        if scene.get("manual_visual_query"):
            score += len(_source_tokens(scene.get("manual_visual_query"))) * 2.0
        ranked.append((score, index))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [index for score, index in ranked if score > 0] or [index for _, index in ranked]


async def _load_verified_news_source_candidate(bot, visual_runtime, scenes, active_config):
    """Extract the selected article image only when explicitly opted in."""
    if not allow_unlicensed_visuals():
        return None
    try:
        from news_source_image_runtime import extract_news_source_image, compose_news_source_image
    except Exception as exc:
        print(f"   [News Source Image] Runtime unavailable: {type(exc).__name__}: {exc}", flush=True)
        return None

    selected_story = active_config.get("selected_story") if isinstance(active_config, dict) else None
    if not isinstance(selected_story, dict):
        return None

    article_url = str(
        selected_story.get("story_url")
        or selected_story.get("url")
        or selected_story.get("link")
        or ""
    ).strip()
    if not article_url:
        return None

    publisher = str(
        selected_story.get("source_label")
        or selected_story.get("source")
        or selected_story.get("publisher")
        or ""
    ).strip()
    article_title = str(selected_story.get("title") or "").strip()
    if not article_title:
        return None

    try:
        source_pack = await __import__("asyncio").get_running_loop().run_in_executor(
            None, extract_news_source_image, article_url, publisher
        )
    except Exception as exc:
        print(f"   [News Source Image] Extraction failed: {type(exc).__name__}: {exc}", flush=True)
        return None

    if not isinstance(source_pack, dict) or not source_pack.get("bytes"):
        print("   [News Source Image] No article image was extracted.", flush=True)
        return None

    raw = source_pack["bytes"]
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        print(f"   [News Source Image] Invalid extracted image: {type(exc).__name__}: {exc}", flush=True)
        return None

    ranked_indices = _rank_news_source_scene_indices(scenes, article_title)
    person_scene_indices = {
        index
        for index, scene in enumerate(scenes)
        if str(scene.get("visual_genre") or "").strip().upper()
        in {"PERSON_PORTRAIT", "PERSON_ACTION"}
    }
    for scene_index in ranked_indices:
        scene = scenes[scene_index]
        if scene_index in person_scene_indices:
            # Article lead images frequently contain headlines, cards or page
            # artwork rather than the actual person. Person slides must use the
            # person-specific retrieval path instead.
            print(
                f"   [News Source Image] Skipped person scene {scene_index + 1}; "
                "person-specific retrieval is required.",
                flush=True,
            )
            continue
        scene["news_source_qc_attempted"] = True
        try:
            accepted, tier, score, hard_reject = visual_runtime._strict_gate(
                bot, raw, scene, article_title, source="news_source"
            )
        except Exception as exc:
            print(
                f"   [News Source Image] QC exception for scene {scene_index + 1}: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            continue

        print(
            f"   [News Source Image] QC scene {scene_index + 1} | "
            f"accepted={accepted} tier={tier} score={score} hard_reject={hard_reject}",
            flush=True,
        )
        if not accepted:
            continue

        scene["visual_verified"] = True
        scene["visual_query_used"] = "selected article lead image"
        scene["visual_source"] = "news_source"
        image_url = str(source_pack.get("image_url") or "")
        page_url = str(source_pack.get("page_url") or article_url)
        publisher_name = str(source_pack.get("publisher") or publisher or "News source").strip()
        return {
            "scene_index": scene_index,
            "image": compose_news_source_image(image, (1080, 1920)),
            "source_type": "news_source",
            "credit": str(
                source_pack.get("credit")
                or f"Source: {publisher_name}"
            ).strip(),
            "image_url": image_url,
            "page_url": page_url,
            "provenance": provenance(
                "Article source",
                url=image_url or page_url,
                author=publisher_name,
                license="Unverified article-source license",
                license_url=page_url,
            ),
        }

    print("   [News Source Image] Extracted image failed the normal visual QC for every relevant scene; discarded.", flush=True)
    return None



_RELATED_POOL_LIMIT_PER_SUBJECT = 10
_RELATED_REUSE_LIMIT_PER_SUBJECT = 10


def _related_subject_key(value) -> str:
    text = re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()
    return re.sub(r"[_\s]+", " ", text)


def _register_related_assets(pool: list[dict], assets) -> None:
    """Add only trusted, unused alternatives, capped per subject."""
    for asset in assets or []:
        if not isinstance(asset, dict) or not asset.get("bytes"):
            continue
        subject_key = _related_subject_key(asset.get("subject"))
        if not subject_key:
            continue
        if any(
            str(item.get("hash") or "") == str(asset.get("hash") or "")
            for item in pool
        ):
            continue
        same_subject = sum(
            1
            for item in pool
            if _related_subject_key(item.get("subject")) == subject_key
        )
        if same_subject >= _RELATED_POOL_LIMIT_PER_SUBJECT:
            continue
        pool.append(dict(asset))


def _scene_subject_keys(scene: dict) -> set[str]:
    return {
        item
        for item in (
            _related_subject_key(scene.get("factual_primary_entity")),
            _related_subject_key(scene.get("primary_entity")),
            _related_subject_key(scene.get("visual_search_subject")),
            _related_subject_key(scene.get("manual_visual_query")),
        )
        if item
    }


def _related_asset_score(asset: dict, scene: dict) -> int:
    score = 0
    scene_genre = str(scene.get("visual_genre") or "").strip().upper()
    scene_type = str(scene.get("visual_type") or "").strip().upper()
    asset_genre = str(asset.get("visual_genre") or "").strip().upper()
    asset_type = str(asset.get("visual_type") or "").strip().upper()

    if scene_genre and asset_genre == scene_genre:
        score += 50
    if scene_type and asset_type == scene_type:
        score += 25

    person_genres = {"PERSON_PORTRAIT", "PERSON_ACTION"}
    if scene_genre in person_genres and asset_genre in person_genres:
        score += 12

    sport_genres = {"SPORTS_ACTION", "SPORTS_MATCH", "TEAM_ACTION"}
    if scene_genre in sport_genres and asset_genre in sport_genres:
        score += 10

    return score


def _select_related_asset(
    pool: list[dict],
    scene: dict,
    used_hashes: set[str],
    reuse_counts: dict[str, int],
):
    """Return the strongest same-subject verified alternative without forcing reuse."""
    subject_keys = _scene_subject_keys(scene)
    candidates = []

    for asset in pool:
        if not isinstance(asset, dict) or asset.get("used"):
            continue
        image_hash = str(asset.get("hash") or "").strip()
        if image_hash and image_hash in used_hashes:
            continue
        subject_key = _related_subject_key(asset.get("subject"))
        if not subject_key or subject_key not in subject_keys:
            continue
        if reuse_counts.get(subject_key, 0) >= _RELATED_REUSE_LIMIT_PER_SUBJECT:
            continue

        score = _related_asset_score(asset, scene)
        if score < 25:
            continue
        candidates.append((score, asset))

    candidates.sort(
        key=lambda item: (
            -item[0],
            str(item[1].get("source") or "").casefold(),
            str(item[1].get("query") or "").casefold(),
        )
    )
    return candidates[0][1] if candidates else None

def patch_content_first_visuals(bot):
    """Install the content-first visual pipeline once per bot instance."""
    if getattr(bot, "_content_first_visuals_patch_installed", False):
        return bot
    try:
        import visual_runtime
        from visual_query_entities_runtime import search_slide_visual
        from visual_quality_runtime import fit_visual_image, install as install_visual_quality
        from visual_retrieval_runtime import (
                    collect_manual_visual_pool,
            make_visual_rescue,
            materialize_manual_visual_pool,
            materialize_visual_bank,
            select_manual_visual_candidate,
        )
        from visual_entity_grounding_runtime import apply_grounding
    except Exception as exc:
        raise RuntimeError(
            f"Content-first visual runtime could not be installed: {type(exc).__name__}: {exc}"
        ) from exc

    install_visual_quality(visual_runtime)

    async def process(script_data, language_cfg, format_mode="regular"):
        scenes = script_data.get("script", [])
        if not scenes:
            raise RuntimeError("Visual pipeline received an empty script.")

        target_size = (1080, 1920)
        font_choice = language_cfg.get("font")
        packages = [None] * len(scenes)
        used_urls, used_hashes = set(), set()
        related_pool: list[dict] = []
        related_reuse_counts: dict[str, int] = {}

        active_config = getattr(bot, "_active_web_config", {}) or {}
        if str(active_config.get("visual_pipeline") or "").strip() == "option2_storyboard":
            from visual_storyboard_v2_runtime import build_storyboard_visuals
            print("   [Visual Pipeline] Option 2 selected: building original editorial storyboard graphics.", flush=True)
            return await build_storyboard_visuals(bot, script_data, language_cfg, format_mode)

        setattr(bot, "_visual_source_search_cache", {})
        reset_visual_qa_video_budget()

        # Manual queries form one shared retrieval pool. They are not assigned
        # one-per-slide: scene context is applied only after the pool exists.
        manual_raw = str(active_config.get("visual_search_queries", "") or "").strip()
        manual_queries = parse_manual_visual_queries(manual_raw)
        if manual_queries:
            print(
                f"   [Manual Visual Queries] {len(manual_queries)} supplied; "
                "building one shared entity-verified pool before scene selection.",
                flush=True,
            )
        else:
            print(
                "   [Manual Visual Queries] No global manual queries supplied; "
                "using the automatic per-slide visual flow.",
                flush=True,
            )
        # Ground automatic visual identities against the selected story evidence.
        # Manual queries remain untouched and authoritative.
        for scene_index, scene in enumerate(scenes, 1):
            grounded = apply_grounding(scene, script_data)
            scenes[scene_index - 1] = grounded
            original_entity = str(grounded.get('visual_entity_original') or '').strip()
            current_entity = str(grounded.get('primary_entity') or '').strip()
            reason = str(grounded.get('visual_entity_grounding_reason') or '').strip()
            if grounded.get('visual_entity_grounding') == 'MANUAL_LOCK':
                print(
                    f"   [Visual Grounding] Scene {scene_index} | MANUAL_LOCK | query='{grounded.get('manual_visual_query', '')}'",
                    flush=True,
                )
            elif original_entity and original_entity != current_entity:
                print(
                    f"   [Visual Grounding] Scene {scene_index} | REPAIRED | '{original_entity}' -> '{current_entity}' | {reason}",
                    flush=True,
                )
            elif not grounded.get('visual_entity_grounded', False):
                print(
                    f"   [Visual Grounding] Scene {scene_index} | UNGROUNDED | entity='{current_entity}' | {reason}",
                    flush=True,
                )

        # Only subjects that appear on multiple slides are allowed to build a
        # related-asset rescue pool. This keeps the fallback useful without
        # collecting extra images for every one-off subject.
        subject_counts: dict[str, int] = {}
        for scene in scenes:
            subject_key = _related_subject_key(
                scene.get("factual_primary_entity")
                or scene.get("primary_entity")
                or scene.get("visual_search_subject")
            )
            if subject_key:
                subject_counts[subject_key] = subject_counts.get(subject_key, 0) + 1

        for scene in scenes:
            subject_key = _related_subject_key(
                scene.get("factual_primary_entity")
                or scene.get("primary_entity")
                or scene.get("visual_search_subject")
            )
            scene["_related_asset_rescue_eligible"] = bool(
                subject_key and subject_counts.get(subject_key, 0) >= 2
            )

        active_config = getattr(bot, "_active_web_config", {}) or {}
        news_source_candidate = None
        if not manual_queries:
            news_source_candidate = await _load_verified_news_source_candidate(
                bot, visual_runtime, scenes, active_config
            )
        news_source_scene_index = (
            int(news_source_candidate["scene_index"])
            if isinstance(news_source_candidate, dict)
            else -1
        )

        ai_count = 0
        verified_count = 0
        rescue_count = 0

        manual_pool_result = None
        manual_pool_materialized = []
        manual_available_pool = []
        if manual_queries:
            manual_pool_result = collect_manual_visual_pool(
                visual_runtime,
                bot,
                scenes,
                manual_queries,
                video_title=str(script_data.get("title", "") or (script_data.get("titles") or [""])[0]),
                used_hashes=used_hashes,
                allow_auto_backfill=False,
            )
            for asset in manual_pool_result.get("assets") or []:
                if str(asset.get("provenance_status") or "").strip() != "commercial-verified":
                    # Rights-review images remain available to the human QC pool,
                    # but must never enter the verified automatic cache.
                    continue
                try:
                    visual_runtime.save_to_cache(
                        bot,
                        asset.get("bytes"),
                        str(asset.get("subject") or "").strip(),
                        str(asset.get("visual_type") or "GENERAL_CONTEXT"),
                        str(asset.get("source") or "manual"),
                        context=f"manual:{str(asset.get('query') or '').strip()}:{str(asset.get('hash') or '').strip()}",
                    )
                except Exception as exc:
                    print(
                        f"   [Visual Cache] Manual pool cache write skipped: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
            manual_pool_materialized = materialize_manual_visual_pool(
                bot,
                manual_pool_result.get("assets") or [],
                pool_id=hash(";".join(manual_queries)) & 0xffffffff,
            )
            manual_available_pool = [dict(item) for item in manual_pool_materialized]
            script_data["visual_manual_queries"] = list(manual_queries)
            script_data["visual_manual_pool_size"] = len(manual_pool_materialized)
            script_data["visual_manual_pool_query_stats"] = list(
                manual_pool_result.get("query_stats") or []
            )
            script_data["visual_manual_pool_rejection_counts"] = dict(
                manual_pool_result.get("rejection_counts") or {}
            )
            print(
                f"   [Manual Visual Pool] total entity-verified candidates="
                f"{len(manual_pool_materialized)}; scene selection begins now.",
                flush=True,
            )

        print("\n🎨 Rendering content-first visual package (multi-source retrieval + strict QA)...", flush=True)
        for idx, seg in enumerate(scenes):
            start_visual_qa_scene()
            video_title = script_data.get("title", "") or (script_data.get("titles") or [""])[0]
            category = str(seg.get("sport_or_topic_category", "")).lower()

            manual_selected = None
            if manual_available_pool:
                manual_selected = select_manual_visual_candidate(
                    manual_available_pool,
                    {**seg, "slide_index": idx + 1},
                    used_hashes,
                )

            if manual_selected:
                selected_hash = str(manual_selected.get("hash") or "").strip()
                selected_path = str(manual_selected.get("path") or "").strip()
                bg_img = Image.open(selected_path).convert("RGB")
                used_ai = False
                source_type = str(manual_selected.get("source") or "manual-pool")
                source_credit = source_credit_for_type(source_type)
                seg["visual_verified"] = True
                seg["visual_type"] = str(manual_selected.get("visual_type") or "GENERAL_CONTEXT").upper()
                seg["visual_genre"] = str(manual_selected.get("visual_genre") or "GENERAL_CONTEXT").upper()
                seg["visual_selected_hash"] = selected_hash
                seg["visual_query_used"] = str(manual_selected.get("query") or "").strip()
                seg["visual_provider_query_used"] = str(manual_selected.get("query") or "").strip()
                seg["manual_visual_query"] = "; ".join(manual_queries)
                seg["manual_visual_query_mode"] = True
                seg["asset_provenance"] = dict(manual_selected.get("provenance") or {})
                seg["visual_original_path"] = selected_path
                seg["visual_asset_bank"] = []
                seg["visual_selected_scene_score"] = 0.0
                manual_available_pool = [
                    dict(item)
                    for item in manual_available_pool
                    if str(item.get("hash") or "").strip() != selected_hash
                ]
                seg["visual_manual_pool_mode"] = True
                seg["visual_rejection_counts"] = dict(
                    (manual_pool_result or {}).get("rejection_counts") or {}
                )
                if manual_selected.get("status") == "factory-rejected-resolution":
                    seg["visual_qc_blocked"] = True
                    seg["visual_qc_block_reason"] = "Entity verified, but this image is below the normal resolution threshold and requires manual visual QC."
                else:
                    seg["visual_qc_blocked"] = False
                    seg["visual_qc_block_reason"] = ""
                seg["visual_rescue_reason"] = ""
                seg["visual_fallback_reason"] = ""
                used_hashes.add(selected_hash)
                print(
                    f"   [Manual Visual Pool] Scene {idx + 1} selected "
                    f"query='{manual_selected.get('query', '')}' status={manual_selected.get('status', '')}",
                    flush=True,
                )
            elif manual_queries:
                bg_img = make_visual_rescue(
                    str(seg.get("primary_entity") or "Visual rescue").strip(),
                    str(seg.get("visual_type") or "GENERAL_CONTEXT"),
                )
                used_ai = False
                source_type = "visual-rescue"
                source_credit = source_credit_for_type(source_type)
                seg["visual_verified"] = False
                seg["visual_qc_blocked"] = True
                seg["visual_qc_block_reason"] = "Manual visual pool produced no identity-verified candidate for this slide."
                seg["visual_rescue_reason"] = "manual-pool-exhausted"
                seg["visual_fallback_reason"] = ""
                seg["visual_query_used"] = ""
                seg["visual_original_path"] = ""
                seg["visual_asset_bank"] = []
                seg["visual_manual_pool_mode"] = True
                seg["visual_rejection_counts"] = dict(
                    (manual_pool_result or {}).get("rejection_counts") or {}
                )
            elif idx == news_source_scene_index and isinstance(news_source_candidate, dict):
                bg_img = news_source_candidate["image"]
                used_ai = False
                source_type = news_source_candidate["source_type"]
                source_credit = news_source_candidate["credit"]
                seg["asset_provenance"] = dict(news_source_candidate.get("provenance") or {})
                print(
                    f"   [News Source Image] Accepted for scene {idx + 1} after normal visual QC.",
                    flush=True,
                )
            else:
                try:
                    bg_img, used_ai, source_type = search_slide_visual(
                        visual_runtime,
                        bot,
                        seg,
                        category,
                        used_urls,
                        used_hashes,
                        video_title,
                        manual_query=str(seg.get("manual_visual_query", "") or "").strip(),
                    )
                except Exception as exc:
                    subject = str(seg.get("primary_entity") or "Visual rescue").strip()
                    seg["visual_verified"] = False
                    seg["visual_rescue_reason"] = f"visual-search-exception:{type(exc).__name__}:{exc}"
                    seg["visual_fallback_reason"] = ""
                    print(
                        f"   [Visual Rescue] Scene {idx + 1} retrieval exception; continuing with renderer rescue: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    bg_img, used_ai, source_type = make_visual_rescue(
                        subject, str(seg.get("visual_type", "GENERAL_CONTEXT"))
                    ), False, "visual-rescue"
                    # The source-type branch below records this rescue exactly once.

            if seg.get("_related_asset_rescue_eligible"):
                _register_related_assets(
                    related_pool,
                    seg.get("_verified_subject_assets") or [],
                )

            if source_type != "news_source":
                source_credit = source_credit_for_type(source_type)
            scene_verified = bool(seg.get("visual_verified", False))
            if scene_verified:
                verified_count += 1
            ai_count += int(used_ai)
            if str(source_type).lower() == "visual-rescue":
                rescue_count += 1

            if source_type == "news_source":
                bg_img = bg_img.convert("RGBA")
            else:
                bg_img = fit_visual_image(
                    bg_img,
                    target_size,
                    str(seg.get("visual_genre") or "GENERAL_CONTEXT"),
                ).convert("RGBA")
            img_path = os.path.join(bot.ASSETS_DIR, f"scene_{idx+1}_img.jpg")

            try:
                from visual_strategy_runtime import classify_scene
                visual_type = classify_scene(seg, category)
            except Exception:
                visual_type = str(seg.get("visual_type", "GENERAL_CONTEXT"))

            if format_mode == "top5" and idx == 0:
                rendered = visual_runtime._render_image_slide(
                    bot, bg_img, video_title or seg.get("voiceover", "Top 5"),
                    "TODAY'S TOP 5", font_choice
                )
            elif format_mode == "top5":
                clean = re.sub(r"(number\s*\d+|story\s*#?\d+|#\d+)", "", str(seg.get("voiceover", "")), flags=re.IGNORECASE).strip()
                rendered = bot.render_top5_card(bg_img, max(1, 6 - idx), 5, clean or seg.get("voiceover", ""), font_choice=font_choice)
            else:
                # Deep Dive never receives the legacy opaque hook-card treatment.
                # First scenes use the same content-first visual treatment as all
                # subsequent Deep Dive scenes; Top-5 retains its dedicated cards.
                rendered = bg_img.convert("RGBA")

            rendered = rendered.convert("RGBA")
            rendered.convert("RGB").save(img_path, "JPEG", quality=95)
            packages[idx] = [{
                "image": img_path,
                "text": "" if format_mode == "top5" else seg.get("voiceover", ""),
                "ai_generated": used_ai,
                "source_type": source_type,
                "visual_type": visual_type,
                "visual_genre": seg.get("visual_genre", "GENERAL_CONTEXT"),
                "visual_verified": scene_verified,
                "visual_qc_blocked": bool(seg.get("visual_qc_blocked", False)),
                "visual_qc_block_reason": seg.get("visual_qc_block_reason", ""),
                "visual_rejection_counts": dict(seg.get("visual_rejection_counts") or {}),
                "visual_rescue_reason": seg.get("visual_rescue_reason", ""),
                "visual_fallback_reason": "",
                "visual_query_used": seg.get("visual_query_used", ""),
                "manual_visual_query": seg.get("manual_visual_query", ""),
                "manual_visual_query_score": seg.get("manual_visual_query_score", 0),
                "source_credit": source_credit,
                "source_image_url": news_source_candidate.get("image_url", "") if source_type == "news_source" and isinstance(news_source_candidate, dict) else "",
                "asset_provenance": dict(seg.get("asset_provenance") or {}),
                "visual_asset_bank": (
                    []
                    if seg.get("visual_manual_pool_mode")
                    else materialize_visual_bank(bot, seg, idx + 1)
                ),
                "visual_original_path": str(seg.get("visual_original_path") or "").strip(),
                "visual_manual_pool_mode": bool(seg.get("visual_manual_pool_mode", False)),
                "visual_manual_pool_size": len(manual_pool_materialized) if seg.get("visual_manual_pool_mode") else 0,
                "visual_manual_pool_query_stats": list((manual_pool_result or {}).get("query_stats") or []) if seg.get("visual_manual_pool_mode") else [],
                "visual_manual_pool": (
                    [dict(item) for item in manual_available_pool]
                    if idx == 0 and seg.get("visual_manual_pool_mode")
                    else []
                ),
            }]
            seg["visual_type"] = visual_type
            seg["visual_verified"] = scene_verified
            seg["visual_source"] = source_type
            # Bank bytes are already materialized to disk and/or copied into the
            # repeated-subject rescue pool. Do not keep raw image bytes in script_data.
            seg.pop("_verified_subject_assets", None)
            seg.pop("visual_asset_bank", None)

        if manual_queries:
            script_data["visual_manual_pool"] = [
                dict(item) for item in manual_available_pool
                if isinstance(item, dict) and not bool(item.get("used"))
            ]
            script_data["visual_manual_pool_unused_count"] = len(script_data["visual_manual_pool"])

        # Second pass: only unverified/failed scenes may borrow an already-
        # verified alternative for the same factual subject. Successful
        # contextual retrievals are never replaced merely for variety.
        related_reused = 0
        for idx, seg in enumerate(scenes):
            if bool(seg.get("visual_verified", False)):
                continue

            asset = _select_related_asset(
                related_pool,
                seg,
                used_hashes,
                related_reuse_counts,
            )
            if asset is None:
                continue

            try:
                related_img = Image.open(io.BytesIO(asset["bytes"])).convert("RGBA")
            except Exception as exc:
                asset["used"] = True
                print(
                    f"   [Visual Rescue] Scene {idx + 1} related asset decode failed: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                continue

            video_title = script_data.get("title", "") or (script_data.get("titles") or [""])[0]
            category = str(seg.get("sport_or_topic_category", "")).lower()
            related_img = fit_visual_image(
                related_img,
                target_size,
                str(seg.get("visual_genre") or "GENERAL_CONTEXT"),
            ).convert("RGBA")

            try:
                from visual_strategy_runtime import classify_scene
                visual_type = classify_scene(seg, category)
            except Exception:
                visual_type = str(seg.get("visual_type", "GENERAL_CONTEXT"))

            if format_mode == "top5" and idx == 0:
                rendered = visual_runtime._render_image_slide(
                    bot,
                    related_img,
                    video_title or seg.get("voiceover", "Top 5"),
                    "TODAY'S TOP 5",
                    font_choice,
                )
            elif format_mode == "top5":
                clean = re.sub(
                    r"(number\s*\d+|story\s*#?\d+|#\d+)",
                    "",
                    str(seg.get("voiceover", "")),
                    flags=re.IGNORECASE,
                ).strip()
                rendered = bot.render_top5_card(
                    related_img,
                    max(1, 6 - idx),
                    5,
                    clean or seg.get("voiceover", ""),
                    font_choice=font_choice,
                )
            else:
                rendered = related_img

            img_path = os.path.join(bot.ASSETS_DIR, f"scene_{idx+1}_img.jpg")
            rendered.convert("RGBA").convert("RGB").save(img_path, "JPEG", quality=95)

            old_source = str(packages[idx][0].get("source_type") or "").lower()
            if old_source == "visual-rescue":
                rescue_count = max(0, rescue_count - 1)

            source_type = "related-verified"
            related_source = str(asset.get("source") or "").strip()
            seg["visual_type"] = visual_type
            seg["visual_verified"] = True
            seg["visual_rescue_reason"] = "related-verified-subject-asset"
            seg["visual_fallback_reason"] = ""
            seg["visual_query_used"] = f"related:{str(asset.get('query') or '').strip()}"

            packages[idx][0].update(
                {
                    "source_type": source_type,
                    "visual_type": visual_type,
                    "visual_genre": seg.get("visual_genre", "GENERAL_CONTEXT"),
                    "visual_verified": True,
                    "visual_qc_blocked": False,
                    "visual_qc_block_reason": "",
                    "visual_rejection_counts": dict(seg.get("visual_rejection_counts") or {}),
                    "visual_rescue_reason": seg["visual_rescue_reason"],
                    "visual_fallback_reason": "",
                    "visual_query_used": seg["visual_query_used"],
                    "source_credit": source_credit_for_type(related_source),
                    "source_image_url": "",
                    "asset_provenance": dict(asset.get("provenance") or {}),
                    "related_reuse": True,
                    "related_subject": str(asset.get("subject") or "").strip(),
                    "related_source_type": related_source,
                    "related_query": str(asset.get("query") or "").strip(),
                }
            )
            seg["visual_source"] = source_type

            image_hash = str(asset.get("hash") or "").strip()
            if image_hash:
                used_hashes.add(image_hash)
            asset["used"] = True
            subject_key = _related_subject_key(asset.get("subject"))
            related_reuse_counts[subject_key] = related_reuse_counts.get(subject_key, 0) + 1
            related_reused += 1
            verified_count += 1

            print(
                f"   [Visual Rescue] Scene {idx + 1} reused verified same-subject asset | "
                f"subject='{asset.get('subject', '')}' source={related_source} "
                f"genre={asset.get('visual_genre', '')}",
                flush=True,
            )

        total = len(scenes)
        provenance_records = []
        for scene_index, scene in enumerate(scenes):
            record = dict(scene.get("asset_provenance") or {})
            if not record:
                record = rescue_provenance()
                scene["asset_provenance"] = record
            if packages[scene_index]:
                packages[scene_index][0]["asset_provenance"] = record
            provenance_records.append(record)
        script_data["visual_provenance"] = provenance_records
        script_data["ai_image_ratio"] = round(ai_count / max(1, total), 2)
        script_data["visual_coverage"] = round(verified_count / max(1, total), 2)
        script_data["visuals_verified"] = verified_count == total
        script_data["visual_fallback_count"] = rescue_count
        script_data["visual_rescue_count"] = rescue_count
        script_data["visual_related_reuse_count"] = related_reused
        print(
            f"   [+] Content-first visual pass complete: {verified_count}/{total} scenes have verified visuals; "
            f"AI images={ai_count}; renderer rescues={rescue_count}; "
            f"related verified reuse={related_reused}.",
            flush=True,
        )
        return packages

    bot.process_visuals_async = process
    bot._content_first_visuals_patch_installed = True
    return bot
