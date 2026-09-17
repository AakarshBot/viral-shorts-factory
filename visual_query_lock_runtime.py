"""Hard production guard for the visual-search contract.

The production contract is now:
    identify -> lock -> exact one-query search -> one returned image -> QA -> use/fail

This module remains as a compatibility guard for older runtime imports, but it
now also hard-locks the retrieval runtime itself so legacy fallback ladders
cannot reintroduce query noise or best-available image selection.
"""
from __future__ import annotations

import hashlib
import html
import io
import os
import re

from PIL import Image

_VERSION = "2026-09-17-v12-single-search-single-image"
_INSTALLED = False
_INVALID = {"none", "unknown", "na", "n/a"}


def _clean(value: object) -> str:
    text = html.unescape(str(value or "")).replace("\u200b", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.-:;|\"'")


def _primary(scene) -> str:
    if not isinstance(scene, dict):
        return ""
    subject = _clean(scene.get("primary_entity", ""))
    return "" if subject.casefold() in _INVALID else subject


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True

    try:
        import visual_strategy_runtime as strategy
        import visual_retrieval_planner as planner
        import visual_runtime as runtime
    except Exception as exc:
        print(f"   [Visual Query Lock] Could not install: {exc}", flush=True)
        return False

    def exact_queries(scene, video_title="", visual_type=None):
        subject = _primary(scene)
        resolved_type = visual_type
        if not resolved_type:
            try:
                resolved_type = planner.classify_scene(scene or {}, str((scene or {}).get("sport_or_topic_category", "")))
            except Exception:
                resolved_type = "GENERAL_CONTEXT"
        return ([subject] if subject else []), resolved_type

    strategy.build_deep_queries = exact_queries
    planner.build_deep_queries = exact_queries
    runtime._build_search_variants = lambda scene, video_title="": exact_queries(scene, video_title)
    runtime.VISUAL_MAX_SEARCH_QUERIES = 1
    runtime.VISUAL_MAX_VERIFICATION_ATTEMPTS = 1

    original_source_plan = getattr(runtime, "_source_plan", None)

    def single_source_plan(bot, visual_type, category):
        """Return only the first deterministic source; never source-fallback."""
        if not callable(original_source_plan):
            return []
        plan = original_source_plan(bot, visual_type, category)
        return plan[:1]

    runtime._source_plan = single_source_plan

    def locked_relevant_asset(bot, seg, category, used_urls, used_hashes, video_title=""):
        """Fetch one image for the locked subject and let QA make the decision."""
        entity = _primary(seg)
        if not entity:
            raise RuntimeError("Visual pipeline requires a locked primary_entity.")

        queries, visual_type = exact_queries(seg, video_title)
        if len(queries) != 1 or queries[0] != entity:
            raise RuntimeError("Visual query lock violation: search query must equal primary_entity exactly.")
        query = queries[0]
        context = runtime._context_fingerprint(
            str(seg.get("visual_intent", "")),
            query,
            str(seg.get("voiceover", "")),
            video_title,
        )

        cached_img, _ = runtime.get_cached_asset(bot, entity, visual_type, context)
        if cached_img is not None:
            buf = io.BytesIO()
            cached_img.save(buf, format="JPEG", quality=95)
            data = buf.getvalue()
            image_hash = bot.get_image_hash(data) if hasattr(bot, "get_image_hash") else hashlib.sha256(data).hexdigest()
            if image_hash not in used_hashes:
                used_hashes.add(image_hash)
                print(f"   [Visual Cache] Verified exact-subject cache hit | '{entity}' | QA=previously passed", flush=True)
                return cached_img, False, "cached"

        plan = single_source_plan(bot, visual_type, category)
        if not plan:
            raise RuntimeError(f"No visual source is available for locked subject '{entity}'.")

        source, fetcher = plan[0]
        args = (entity, used_urls, query, video_title) if source == "Wikipedia" else (query, used_urls, query, video_title)
        print(f"   [Visual Search] 1/1 | exact query='{query}' | source={source}", flush=True)
        data = runtime._call_fetcher_with_timeout(fetcher, args, source, query)
        if not data:
            raise RuntimeError(f"Image search returned no usable image for locked subject '{entity}'.")

        image_hash = bot.get_image_hash(data) if hasattr(bot, "get_image_hash") else hashlib.sha256(data).hexdigest()
        if image_hash in used_hashes:
            raise RuntimeError(f"Image search returned an already-used image for locked subject '{entity}'.")
        if not runtime._local_visual_sanity(data):
            raise RuntimeError(f"Returned image failed local visual sanity checks for '{entity}'.")

        qa_result = runtime._strict_gemini_check(
            data,
            entity,
            str(seg.get("visual_intent", "")),
            query,
            str(seg.get("voiceover", "")),
            video_title,
            os.getenv("GEMINI_API_KEY"),
            tier="IDENTITY",
            visual_type=visual_type,
        )
        if qa_result is not True:
            print(f"   [Visual QA] TERMINAL REJECT | subject='{entity}' | result=NO/uncertain/unavailable", flush=True)
            raise RuntimeError(f"Visual QA rejected or could not verify the returned image for '{entity}'. No fallback query or image is permitted.")

        used_hashes.add(image_hash)
        cache_path = runtime.save_to_cache(bot, data, entity, visual_type, source, context)
        print(f"   [Visual Source] {source} | VERIFIED | exact query='{query}' | QA=YES", flush=True)
        if cache_path:
            print(f"   [Visual Cache] Saved QA-verified exact-subject asset for '{entity}'.", flush=True)
        return Image.open(io.BytesIO(data)).convert("RGB"), False, source

    runtime._relevant_asset = locked_relevant_asset
    runtime._visual_query_lock_version = _VERSION

    strategy.MAX_VISUAL_SEARCH_QUERIES = 1
    planner.MAX_VISUAL_SEARCH_QUERIES = 1

    _INSTALLED = True
    print(f"   [Visual Query Lock] Installed | version={_VERSION} | one exact search + one image + terminal QA", flush=True)
    return True
