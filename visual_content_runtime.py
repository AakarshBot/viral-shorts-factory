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


async def _load_web_fresh_image_pool(bot, active_config, scenes, video_title, category):
    """Run the fresh web crawler before every other image provider."""
    selected_story = active_config.get("selected_story") if isinstance(active_config, dict) else {}
    if not isinstance(selected_story, dict):
        selected_story = {}
    try:
        from web_fresh_image_crawler_runtime import crawl_fresh_web_images
        import asyncio
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            crawl_fresh_web_images,
            selected_story,
            scenes,
            video_title,
            "",
            category,
        )
        return result if isinstance(result, dict) else {"assets": []}
    except Exception as exc:
        print(
            f"   [Fresh Web Crawler] failed safely: {type(exc).__name__}: {exc}",
            flush=True,
        )
        return {
            "assets": [],
            "target": 15,
            "success_threshold": 10,
            "queries": [],
            "articles": 0,
            "high_confidence": 0,
            "ai_checked": 0,
            "rejection_counts": {"crawler_exception": 1},
        }


def patch_content_first_visuals(bot):
    """Install the content-first visual pipeline once per bot instance."""
    if getattr(bot, "_content_first_visuals_patch_installed", False):
        return bot
    try:
        import visual_runtime
        from visual_query_entities_runtime import search_slide_visual
        from visual_quality_runtime import fit_visual_image, install as install_visual_quality
        from visual_safety_runtime import install as install_visual_safety
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
    if not install_visual_safety():
        raise RuntimeError("Visual safety runtime could not be installed.")

    async def process(script_data, language_cfg, format_mode="regular"):
        scenes = script_data.get("script", [])
        if not scenes:
            raise RuntimeError("Visual pipeline received an empty script.")

        target_size = (1080, 1920)
        font_choice = language_cfg.get("font")
        packages = [None] * len(scenes)
        used_urls, used_hashes = set(), set()
        reset_visual_qa_video_budget()

        active_config = getattr(bot, "_active_web_config", {}) or {}
        video_title = str(
            script_data.get("title", "")
            or (script_data.get("titles") or [""])[0]
            or ""
        ).strip()
        category = str(
            scenes[0].get("sport_or_topic_category", "")
            if isinstance(scenes[0], dict) else ""
        ).lower()

        web_crawler_result = await _load_web_fresh_image_pool(
            bot,
            active_config,
            scenes,
            video_title,
            category,
        )
        web_crawler_assets = list(web_crawler_result.get("assets") or [])
        web_crawler_materialized = materialize_manual_visual_pool(
            bot,
            web_crawler_assets,
            pool_id=f"web_{abs(hash(video_title or 'story')) & 0xffffffff}",
        )
        crawler_satisfies_pool = len(web_crawler_materialized) >= int(
            web_crawler_result.get("success_threshold") or 10
        )

        script_data["visual_web_crawler_pool_size"] = len(web_crawler_materialized)
        script_data["visual_web_crawler_articles"] = int(
            web_crawler_result.get("articles") or 0
        )
        script_data["visual_web_crawler_high_confidence"] = int(
            web_crawler_result.get("high_confidence") or 0
        )
        script_data["visual_web_crawler_ai_checked"] = int(
            web_crawler_result.get("ai_checked") or 0
        )
        script_data["visual_web_crawler_rejection_counts"] = dict(
            web_crawler_result.get("rejection_counts") or {}
        )
        script_data["visual_web_crawler_queries"] = list(
            web_crawler_result.get("queries") or []
        )

        print(
            f"   [Fresh Web Crawler] pool={len(web_crawler_materialized)}/15 "
            f"| success=10 | fallback providers="
            f"{'skipped' if crawler_satisfies_pool else 'allowed'}",
            flush=True,
        )

        manual_raw = str(active_config.get("visual_search_queries", "") or "").strip()
        manual_queries = parse_manual_visual_queries(manual_raw)
        if manual_queries:
            print(
                f"   [Manual Visual Queries] {len(manual_queries)} supplied; "
                "used only to fill a sparse fresh-web pool.",
                flush=True,
            )
        else:
            print(
                "   [Manual Visual Queries] none supplied; "
                "provider fallback is used only when the fresh-web pool is sparse.",
                flush=True,
            )

        manual_pool_result = None
        manual_pool_materialized = []
        manual_available_pool = [dict(item) for item in web_crawler_materialized]

        if manual_queries and not crawler_satisfies_pool:
            remaining_pool_target = max(
                1,
                MANUAL_POOL_TARGET - len(manual_available_pool),
            )
            manual_search_hashes = {
                str(item.get("hash") or "").strip()
                for item in manual_available_pool
                if str(item.get("hash") or "").strip()
            }
            manual_pool_result = collect_manual_visual_pool(
                visual_runtime,
                bot,
                scenes,
                manual_queries,
                video_title=video_title,
                used_hashes=manual_search_hashes,
                pool_target=remaining_pool_target,
                verify_with_ai=True,
            )
            manual_pool_materialized = materialize_manual_visual_pool(
                bot,
                manual_pool_result.get("assets") or [],
                pool_id=hash(";".join(manual_queries)) & 0xffffffff,
            )
            manual_available_pool.extend(
                dict(item) for item in manual_pool_materialized
            )

        script_data["visual_manual_queries"] = list(manual_queries)
        script_data["visual_manual_pool_size"] = len(manual_available_pool)
        script_data["visual_manual_pool_query_stats"] = list(
            (manual_pool_result or {}).get("query_stats") or []
        )
        script_data["visual_manual_pool_rejection_counts"] = dict(
            (manual_pool_result or {}).get("rejection_counts") or {}
        )

        if crawler_satisfies_pool:
            print(
                f"   [Fresh Web Crawler] {len(web_crawler_materialized)} images "
                "reached the pool threshold; no other image provider was called.",
                flush=True,
            )

        ai_count = 0
        verified_count = 0
        rescue_count = 0

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
                selected_is_verified = selected_status in {"entity-verified", "factory-rejected-resolution", "new-search-ai-verified", "crawler-high-confidence", "crawler-ai-verified"}
                seg["visual_verified"] = selected_is_verified
                seg["visual_type"] = str(manual_selected.get("visual_type") or "GENERAL_CONTEXT").upper()
                seg["visual_genre"] = str(manual_selected.get("visual_genre") or "GENERAL_CONTEXT").upper()
                seg["visual_selected_hash"] = selected_hash
                seg["visual_query_used"] = str(manual_selected.get("query") or "").strip()
                seg["visual_provider_query_used"] = str(manual_selected.get("query") or "").strip()
                seg["manual_visual_query"] = "; ".join(manual_queries) if manual_queries else ""
                seg["manual_visual_query_mode"] = bool(manual_queries)
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
                    (manual_pool_result or {}).get("rejection_counts")
                    or web_crawler_result.get("rejection_counts")
                    or {}
                )
                if manual_selected.get("status") == "factory-rejected-resolution":
                    seg["visual_qc_blocked"] = True
                    seg["visual_qc_block_reason"] = "Entity verified, but this image is below the normal resolution threshold and requires manual visual QC."
                elif selected_status in {"manual-review-unverified", "crawler-review-unverified"}:
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
                    f"   [Visual Pool] Scene {idx + 1} selected "
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

            if source_type not in {"news_source", "web_crawler"}:
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

            visual_type = str(seg.get("visual_type") or "GENERAL_CONTEXT").upper()

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
                "visual_manual_pool_size": len(manual_available_pool) if seg.get("visual_manual_pool_mode") else 0,
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
            # Bank bytes are already materialized to disk. Do not keep raw image bytes in script_data.
            seg.pop("_verified_subject_assets", None)
            seg.pop("visual_asset_bank", None)

        combined_manual_pool = [
            dict(item)
            for item in manual_available_pool
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]

        script_data["visual_manual_pool"] = combined_manual_pool
        script_data["visual_manual_pool_unused_count"] = sum(
            1 for item in combined_manual_pool if not bool(item.get("used"))
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
        print(
            f"   [+] Content-first visual pass complete: {verified_count}/{total} scenes have verified visuals; "
            f"AI images={ai_count}; renderer rescues={rescue_count}.",
            flush=True,
        )
        return packages

    bot.process_visuals_async = process
    bot._content_first_visuals_patch_installed = True
    return bot
