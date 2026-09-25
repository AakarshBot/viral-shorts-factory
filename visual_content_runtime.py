"""Live website-first visual acquisition for Shorts.

The automatic production visual pass only discovers and prepares a shared
website-image pool. Slide assignment stays human-controlled in the dashboard;
the established factory provider lane is invoked only from that manual QC
surface.
"""

import io
import os
import re
from PIL import Image

from visual_qa_runtime import reset_visual_qa_video_budget



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
        from visual_retrieval_runtime import materialize_manual_visual_pool
        from visual_safety_runtime import install as install_visual_safety
    except Exception as exc:
        raise RuntimeError(
            f"Content-first visual runtime could not be installed: {type(exc).__name__}: {exc}"
        ) from exc

    if not install_visual_safety():
        raise RuntimeError("Visual safety runtime could not be installed.")

    async def process(script_data, language_cfg, format_mode="regular"):
        scenes = script_data.get("script", [])
        if not isinstance(scenes, list) or not scenes:
            raise RuntimeError("Visual pipeline received an empty script.")

        packages = [None] * len(scenes)
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

        # Initial retrieval is intentionally website-only. The dashboard owns
        # the selection gate; this stage must never auto-assign a slide image
        # or silently fall into Commons/DDG when the web pool is underfilled.
        web_crawler_result = await _load_web_fresh_image_pool(
            bot,
            active_config,
            scenes,
            video_title,
            category,
        )
        web_crawler_assets = list(web_crawler_result.get("assets") or [])
        pool_id = (
            "web_"
            + re.sub(r"[^A-Za-z0-9_-]+", "_", str(active_config.get("run_id") or "run"))
            + "_"
            + str(abs(hash(video_title or "story")) & 0xffffffff)
        )
        from visual_retrieval_runtime import materialize_manual_visual_pool

        web_crawler_materialized = materialize_manual_visual_pool(
            bot,
            web_crawler_assets,
            pool_id=pool_id,
        )
        web_pool = [
            dict(item)
            for item in web_crawler_materialized
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]

        script_data["visual_web_crawler_pool_size"] = len(web_pool)
        script_data["visual_web_crawler_articles"] = int(
            web_crawler_result.get("articles") or 0
        )
        script_data["visual_web_crawler_profile_pages"] = int(
            web_crawler_result.get("profile_pages") or 0
        )
        script_data["visual_web_crawler_high_confidence"] = int(
            web_crawler_result.get("high_confidence") or 0
        )
        script_data["visual_web_crawler_ai_checked"] = int(
            web_crawler_result.get("ai_checked") or 0
        )
        script_data["visual_web_crawler_queries"] = list(
            web_crawler_result.get("queries") or []
        )
        script_data["visual_web_crawler_rejection_counts"] = dict(
            web_crawler_result.get("rejection_counts") or {}
        )
        script_data["visual_web_crawler_failure_state"] = str(
            web_crawler_result.get("failure_state") or ""
        ).strip()
        script_data["visual_web_crawler_manual"] = False

        print(
            f"   [Fresh Web Crawler] pool={len(web_pool)}/15 "
            f"| articles={int(web_crawler_result.get('articles') or 0)} "
            f"| final_pool={len(web_pool)}/15 "
            "| provider fallback=disabled",
            flush=True,
        )

        # The automatic visual stage ends here. Preserve the exact website
        # candidate pool in the script payload so the dashboard can display it
        # and assign individual images without mutating other slides.
        for idx, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                scene = {"voiceover": str(scene or "")}
                scenes[idx] = scene

            visual_type = str(
                scene.get("visual_type")
                or ("PERSON" if scene.get("primary_entity") else "GENERAL_CONTEXT")
            ).upper()
            visual_genre = str(scene.get("visual_genre") or "GENERAL_CONTEXT").upper()

            scene["visual_verified"] = False
            scene["visual_qc_blocked"] = True
            scene["visual_qc_block_reason"] = (
                "No image selected. Choose a visual during manual visual QC."
            )
            scene["visual_rescue_reason"] = ""
            scene["visual_fallback_reason"] = ""
            scene["visual_query_used"] = ""
            scene["visual_provider_query_used"] = ""
            scene["visual_original_path"] = ""
            scene["visual_selected_hash"] = ""
            scene["visual_asset_bank"] = []
            scene["visual_manual_pool_mode"] = True
            scene["visual_manual_pool_size"] = len(web_pool)
            scene["visual_manual_pool_query_stats"] = []
            scene["visual_search_retrieval_method"] = ""
            scene["asset_provenance"] = {}

            packages[idx] = [{
                "image": "",
                "text": "" if format_mode == "top5" else scene.get("voiceover", ""),
                "narration_text": scene.get("voiceover", ""),
                "ai_generated": False,
                "source_type": "",
                "source_name": "",
                "visual_type": visual_type,
                "visual_genre": visual_genre,
                "visual_verified": False,
                "visual_qc_blocked": True,
                "visual_qc_block_reason": scene["visual_qc_block_reason"],
                "visual_rejection_counts": dict(
                    web_crawler_result.get("rejection_counts") or {}
                ),
                "visual_rescue_reason": "",
                "visual_fallback_reason": "",
                "visual_query_used": "",
                "manual_visual_query": str(
                    scene.get("manual_visual_query") or ""
                ).strip(),
                "manual_visual_query_score": float(
                    scene.get("manual_visual_query_score") or 0
                ),
                "source_credit": "",
                "source_page_url": "",
                "source_image_url": "",
                "asset_provenance": {},
                "visual_asset_bank": [],
                "visual_original_path": "",
                "visual_manual_pool_mode": True,
                "visual_manual_pool_size": len(web_pool),
                "visual_manual_pool_query_stats": [],
                "visual_manual_pool": (
                    [dict(item) for item in web_pool]
                    if idx == 0 else []
                ),
                "visual_search_options": [],
                "visual_search_diagnostics": {},
                "visual_pipeline": "website_first_manual_qc",
            }]

        script_data["script"] = scenes
        script_data["visual_manual_pool"] = [dict(item) for item in web_pool]
        script_data["visual_manual_pool_unused_count"] = sum(
            1 for item in web_pool if not bool(item.get("used"))
        )
        # Keep legacy metadata keys populated without implying that a provider
        # fallback actually ran during the automatic website pass.
        script_data["visual_manual_queries"] = []
        script_data["visual_fallback_provider_queries"] = []
        script_data["visual_manual_pool_query_stats"] = []
        script_data["visual_manual_pool_rejection_counts"] = {}
        script_data["visual_provenance"] = [{} for _ in scenes]
        script_data["ai_image_ratio"] = 0.0
        script_data["visual_coverage"] = 0.0
        script_data["visuals_verified"] = False
        script_data["visual_fallback_count"] = 0
        script_data["visual_rescue_count"] = 0

        if not web_pool:
            print(
                "   [Fresh Web Crawler] No usable website images reached the "
                "manual visual QC pool; slide slots remain empty as designed.",
                flush=True,
            )
        else:
            print(
                f"   [+] Website visual pool ready for manual QC: {len(web_pool)}/15. "
                "No slide was auto-assigned.",
                flush=True,
            )

        return packages

    bot.process_visuals_async = process
    bot._content_first_visuals_patch_installed = True
    return bot
