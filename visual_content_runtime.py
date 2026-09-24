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
from PIL import Image

from branding_runtime import source_credit_for_type
from manual_visual_query_runtime import parse_manual_visual_queries
from visual_licensing_runtime import provenance, rescue_provenance
from visual_qa_runtime import reset_visual_qa_video_budget, start_visual_qa_scene


async def _load_news_source_image_pool(bot, active_config):
    """Fetch static images from the selected article for manual dashboard QC only."""
    selected_story = active_config.get("selected_story") if isinstance(active_config, dict) else None
    if not isinstance(selected_story, dict):
        return []

    article_url = str(
        selected_story.get("story_url")
        or selected_story.get("url")
        or selected_story.get("link")
        or ""
    ).strip()
    if not article_url:
        return []

    publisher = str(
        selected_story.get("source_label")
        or selected_story.get("source")
        or selected_story.get("publisher")
        or ""
    ).strip()

    try:
        from news_source_image_runtime import extract_news_source_images
        return await __import__("asyncio").get_running_loop().run_in_executor(
            None, extract_news_source_images, article_url, publisher
        )
    except Exception as exc:
        print(f"   [News Source Image Pool] Extraction failed: {type(exc).__name__}: {exc}", flush=True)
        return []


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
            MANUAL_POOL_TARGET,
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
        article_source_assets = await _load_news_source_image_pool(bot, active_config)
    print(
        f"   [News Source Image Pool] final scrape candidates={len(article_source_assets)}.",
        flush=True,
    )
        article_source_materialized = []
        article_source_hashes: set[str] = set()
        if article_source_assets:
            story_url = str((active_config.get('selected_story') or {}).get('story_url') or '').strip()
            article_pool_id = f"article_{abs(hash(story_url or 'story')) & 0xffffffff}"
            article_source_materialized = materialize_manual_visual_pool(
                bot, article_source_assets, pool_id=article_pool_id
            )
            article_subject = next(
                (
                    str(scene.get("factual_primary_entity") or scene.get("primary_entity") or "").strip()
                    for scene in scenes
                    if str(scene.get("factual_primary_entity") or scene.get("primary_entity") or "").strip()
                ),
                "Selected story",
            )
            for asset in article_source_materialized:
                asset.setdefault("subject", article_subject)
                asset["manual_query_index"] = 0
                asset["pool_origin"] = "article-source"
                asset["provenance_status"] = "provenance-review"
                image_hash = str(asset.get("hash") or "").strip()
                if image_hash:
                    article_source_hashes.add(image_hash)
            print(
                f"   [News Source Image Pool] {len(article_source_materialized)} static article image(s) available for manual QC.",
                flush=True,
            )

        ai_count = 0
        verified_count = 0
        rescue_count = 0

        manual_pool_result = None
        manual_pool_materialized = []
        manual_available_pool = [dict(item) for item in article_source_materialized]
        if manual_queries:
            # This is an explicit human-QC workflow. Do not make dashboard entry
            # depend on a remote Gemini identity verdict; candidates stay labelled
            # unverified until the reviewer approves the visual package.
            remaining_pool_target = max(1, MANUAL_POOL_TARGET - len(article_source_materialized))
            manual_search_hashes = set(used_hashes)
            manual_search_hashes.update(article_source_hashes)
            manual_pool_result = collect_manual_visual_pool(
                visual_runtime,
                bot,
                scenes,
                manual_queries,
                video_title=str(script_data.get("title", "") or (script_data.get("titles") or [""])[0]),
                used_hashes=manual_search_hashes,
                pool_target=remaining_pool_target,
                allow_auto_backfill=False,
                verify_with_ai=True,
            )
            for asset in manual_pool_result.get("assets") or []:
                if str(asset.get("provenance_status") or "").strip() != "commercial-verified":
                    # Rights-review images remain available to the human QC pool,
                    # but must never enter the verified automatic cache.
                    continue
                if str(asset.get("status") or "").strip() == "manual-review-unverified":
                    # Gemini outage candidates are human-reviewable, but are not
                    # eligible for the verified automatic cache.
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
            manual_available_pool.extend(dict(item) for item in manual_pool_materialized)
            script_data["visual_manual_queries"] = list(manual_queries)
            script_data["visual_manual_pool_size"] = len(manual_available_pool)
            script_data["visual_manual_pool_query_stats"] = list(
                manual_pool_result.get("query_stats") or []
            )
            script_data["visual_manual_pool_rejection_counts"] = dict(
                manual_pool_result.get("rejection_counts") or {}
            )
            print(
                f"   [Manual Visual Pool] total candidates available for manual QC="
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
                source_credit = str(manual_selected.get("credit") or "").strip() or source_credit_for_type(source_type)
                selected_status = str(manual_selected.get("status") or "").strip()
                selected_is_verified = selected_status in {"entity-verified", "factory-rejected-resolution", "new-search-ai-verified"}
                seg["visual_verified"] = selected_is_verified
                seg["visual_type"] = str(manual_selected.get("visual_type") or "GENERAL_CONTEXT").upper()
                seg["visual_genre"] = str(manual_selected.get("visual_genre") or "GENERAL_CONTEXT").upper()
                seg["visual_selected_hash"] = selected_hash
                seg["visual_query_used"] = str(manual_selected.get("query") or "").strip()
                seg["visual_provider_query_used"] = str(manual_selected.get("query") or "").strip()
                seg["manual_visual_query"] = "; ".join(manual_queries)
                seg["manual_visual_query_mode"] = True
                seg["asset_provenance"] = dict(manual_selected.get("provenance") or {})
                seg["source_image_url"] = str(manual_selected.get("source_image_url") or "").strip()
                seg["visual_original_path"] = selected_path
                seg["visual_asset_bank"] = []
                seg["visual_selected_scene_score"] = 0.0
                manual_selected["used"] = True
                manual_selected["assigned_slide"] = idx + 1
                manual_available_pool = [dict(item) for item in manual_available_pool]
                seg["visual_manual_pool_mode"] = True
                seg["visual_rejection_counts"] = dict(
                    (manual_pool_result or {}).get("rejection_counts") or {}
                )
                if manual_selected.get("status") == "factory-rejected-resolution":
                    seg["visual_qc_blocked"] = True
                    seg["visual_qc_block_reason"] = "Entity verified, but this image is below the normal resolution threshold and requires manual visual QC."
                elif selected_status == "manual-review-unverified":
                    seg["visual_qc_blocked"] = True
                    seg["visual_qc_block_reason"] = "Gemini identity verification was unavailable; this image requires human visual QC before rendering."
                elif str(manual_selected.get("provenance_status") or "").strip() == "provenance-review":
                    seg["visual_qc_blocked"] = True
                    seg["visual_qc_block_reason"] = "Image provenance/license is not automatically verified; human visual and rights QC is required before rendering."
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
                "source_image_url": str(seg.get("source_image_url") or "").strip(),
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

        combined_manual_pool = [
            dict(item) for item in manual_available_pool
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        existing_hashes = {str(item.get("hash") or "").strip() for item in combined_manual_pool if str(item.get("hash") or "").strip()}
        for item in article_source_materialized:
            item_hash = str(item.get("hash") or "").strip()
            if item_hash and (item_hash in existing_hashes or item_hash in used_hashes):
                continue
            combined_manual_pool.append(dict(item))
            if item_hash:
                existing_hashes.add(item_hash)

        script_data["visual_manual_pool"] = combined_manual_pool
        script_data["visual_manual_pool_unused_count"] = sum(
            1 for item in combined_manual_pool if not bool(item.get("used"))
        )

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
