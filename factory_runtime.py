"""Runtime hardening helpers for the Streamlit dashboard."""

import io
import re
import sys
import traceback

from PIL import Image, ImageDraw


def install_safe_exception_hook():
    """Install a headless-safe exception hook that never waits for console input."""
    def safe_hook(exctype, value, tb):
        print("💥 UNCAUGHT EXCEPTION DETECTED:")
        print("!" * 60)
        traceback.print_exception(exctype, value, tb)
        print("!" * 60)

    sys.excepthook = safe_hook


def normalise_publish_mode(value):
    """Return only the two publish modes supported by the dashboard."""
    return "public" if str(value).strip().lower() == "public" else "private"


def patch_dashboard_runtime(bot):
    """Patch deterministic scoring, classification and visual-routing issues for dashboard runs."""
    original_process = bot.process_scored_candidates

    def fixed_process_scored_candidates(scored_data, batch_stories, bonuses, last_genre, format_mode):
        scored_candidates = []
        for idx, scores in enumerate(scored_data):
            if idx >= len(batch_stories) or not isinstance(scores, dict):
                continue

            story = batch_stories[idx]
            try:
                hs = max(1.0, min(10.0, float(scores.get("hook_strength", 5))))
                nc = max(1.0, min(10.0, float(scores.get("narrative_completeness", 5))))
                af = max(1.0, min(10.0, float(scores.get("audience_fit", 5))))
                mr = max(1.0, min(10.0, float(scores.get("monetization_risk", 5))))
                sl = max(1.0, min(10.0, float(scores.get("shelf_life", 5))))
            except (TypeError, ValueError):
                continue

            if scores.get("hard_reject", False) or mr >= 8.0:
                continue

            trend_bonus = bot.get_trend_signal_bonus(story.get("title", ""))
            freshness = story.get("velocity_score", 0.0)
            risk_penalty = (mr - 1.0) * 0.20

            composite = (
                hs * 0.25
                + nc * 0.20
                + af * 0.20
                + (10.0 - mr) * 0.20
                + sl * 0.15
                + (bonuses.get(story.get("genre"), 0) if format_mode == "regular" else 0)
                + (2.0 if format_mode == "regular" and story.get("genre") == last_genre else 0)
                + story.get("corroboration_bonus", 0)
                + trend_bonus
                + freshness
                - story.get("recency_penalty", 1.0)
                - risk_penalty
            )

            story.update({
                "hook_strength": hs,
                "narrative_completeness": nc,
                "audience_fit": af,
                "monetization_risk": mr,
                "shelf_life": sl,
                "composite_score": round(composite, 2),
            })
            scored_candidates.append(story)

        if scored_candidates:
            scored_candidates.sort(key=lambda item: item["composite_score"], reverse=True)
            return scored_candidates

        return original_process([], batch_stories, bonuses, last_genre, format_mode) or batch_stories

    bot.process_scored_candidates = fixed_process_scored_candidates

    def fixed_infer_genre_from_title(title):
        t_lower = str(title or "").lower()
        if any(k in t_lower for k in ["smartphone", "launch", "review", "gadget", "laptop", "processor", "pixel", "iphone"]):
            return "tech_reviews"
        if any(k in t_lower for k in ["cricket", "match", "goal", "isl", "premier league", "tennis", "sport", "squad", "debut", "odi", "test", "formula", "f1"]):
            return "sports_stories_of_day"
        if any(k in t_lower for k in ["movie", "bollywood", "tollywood", "gossip", "box office", "trailer"]):
            return "entertainment"
        if any(k in t_lower for k in ["ai", "artificial intelligence", "tech", "gadgets", "startup", "software"]):
            return "technology"
        if any(k in t_lower for k in ["stock", "finance", "business", "market", "economy", "wealth"]):
            return "business_finance"
        if any(k in t_lower for k in ["health", "fitness", "wellness", "nutrition", "diet"]):
            return "health_lifestyle"
        if any(k in t_lower for k in ["telangana", "hyderabad", "andhra", "amaravati"]):
            return "regional_state_news"
        if any(k in t_lower for k in ["viral", "trend", "phenomenon", "challenge"]):
            return "viral_phenomenon"
        return "national_global_affairs"

    bot.infer_genre_from_title = fixed_infer_genre_from_title

    # ------------------------------------------------------------------
    # Visual quality gate: preserve the existing checks and add a useful
    # OpenCV Haar-cascade face check for person/editorial image searches.
    # Images that are not person-oriented are not rejected merely because
    # they contain no face.
    # ------------------------------------------------------------------
    original_quality_gate = bot.passes_quality_gate

    def fixed_passes_quality_gate(img_data, search_prompt="", video_title=""):
        if not original_quality_gate(img_data, search_prompt, video_title):
            return False

        cv2 = getattr(bot, "cv2", None)
        np = getattr(bot, "np", None)
        if cv2 is None or np is None:
            return True

        prompt_text = f"{search_prompt} {video_title}".lower()
        person_terms = (
            "person", "people", "man", "woman", "player", "actor", "actress",
            "celebrity", "politician", "president", "prime minister", "coach",
            "cricketer", "footballer", "athlete", "singer", "director"
        )
        if not any(term in prompt_text for term in person_terms):
            return True

        try:
            pil_img = Image.open(io.BytesIO(img_data)).convert("RGB")
            cv_img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2GRAY)
            cascade_path = getattr(cv2.data, "haarcascades", "") + "haarcascade_frontalface_default.xml"
            if not cascade_path:
                return True
            cascade = cv2.CascadeClassifier(cascade_path)
            if cascade.empty():
                return True
            faces = cascade.detectMultiScale(
                cv_img,
                scaleFactor=1.1,
                minNeighbors=4,
                minSize=(40, 40),
            )
            if len(faces) == 0:
                return False
        except Exception:
            # Quality checking must never crash the asset pipeline.
            return True

        return True

    bot.passes_quality_gate = fixed_passes_quality_gate

    # ------------------------------------------------------------------
    # Single visual router for dashboard runs. It uses the current script
    # schema: primary_entity, visual_intent and specific_search_prompt.
    # Each successful route reports its real source immediately.
    # ------------------------------------------------------------------
    def fixed_fetch_scene_asset(seg, category, used_urls, used_image_hashes, video_title=""):
        primary_entity = bot.safe_text(seg.get("primary_entity", "none")).strip() or "none"
        visual_intent = bot.safe_text(seg.get("visual_intent", "conceptual")).strip() or "conceptual"
        specific_prompt = bot.safe_text(
            seg.get("specific_search_prompt", f"{video_title} {primary_entity}")
        ).strip()
        if not specific_prompt:
            specific_prompt = f"{video_title} {primary_entity}".strip()

        is_entertainment = any(k in category for k in ["entertainment", "movie", "cinema", "showbiz"])
        intent_text = visual_intent.lower()
        is_editorial = (
            any(k in intent_text for k in ["editorial", "stadium", "news", "trophy", "event", "person"])
            or is_entertainment
            or any(k in category for k in ["sport", "cricket", "football", "news", "politics"])
        )

        if is_entertainment and primary_entity.lower() != "none":
            specific_prompt = f"{specific_prompt} movie still high resolution"

        def accept(img_bytes, source_name):
            if not img_bytes:
                return None
            try:
                image_hash = bot.get_image_hash(img_bytes)
                if image_hash in used_image_hashes:
                    return None
                used_image_hashes.add(image_hash)
                print(f"   [Visual Source] {source_name}")
                return Image.open(io.BytesIO(img_bytes)).convert("RGB"), False, source_name
            except Exception:
                return None

        # People/editorial searches get a person-specific Wikipedia attempt first.
        if is_editorial and primary_entity.lower() != "none":
            img_bytes = bot.fetch_wiki_person_image(
                primary_entity, used_urls, specific_prompt, video_title
            )
            result = accept(img_bytes, "Wikipedia")
            if result:
                return result

            img_bytes = bot.fetch_wikimedia_commons(
                specific_prompt, used_urls, specific_prompt, video_title
            )
            result = accept(img_bytes, "Commons")
            if result:
                return result

            fallback_prompt = (
                f"{primary_entity} movie still"
                if is_entertainment
                else f"{primary_entity} high resolution"
            )
            img_bytes = bot.fetch_pexels(
                fallback_prompt, used_urls, specific_prompt, video_title
            )
            result = accept(img_bytes, "Pexels")
            if result:
                return result

            img_bytes = bot.fetch_unsplash(
                fallback_prompt, used_urls, specific_prompt, video_title
            )
            result = accept(img_bytes, "Unsplash")
            if result:
                return result

            img_bytes = bot.fetch_duckduckgo(
                specific_prompt, used_urls, specific_prompt, video_title
            )
            result = accept(img_bytes, "DDG")
            if result:
                return result

        # General searches use the same ordered public-image fallback chain.
        img_bytes = bot.fetch_pexels(specific_prompt, used_urls, specific_prompt, video_title)
        result = accept(img_bytes, "Pexels")
        if result:
            return result

        img_bytes = bot.fetch_unsplash(specific_prompt, used_urls, specific_prompt, video_title)
        result = accept(img_bytes, "Unsplash")
        if result:
            return result

        img_bytes = bot.fetch_duckduckgo(video_title or specific_prompt, used_urls, specific_prompt, video_title)
        result = accept(img_bytes, "DDG")
        if result:
            return result

        ai_prompt = f"{specific_prompt}, high resolution cinematic photography, detailed"
        bg_img = bot.fetch_hf_ai_image(ai_prompt)
        if bg_img is not None:
            print("   [Visual Source] AI-generated")
            return bg_img, True, "AI-generated"

        # Last-resort deterministic gradient. This is deliberately labelled
        # so a missing-image complaint can be diagnosed from the console.
        bg_img = Image.new("RGB", (1080, 1920), color=bot.PALETTE["bg"])
        draw = ImageDraw.Draw(bg_img)
        for i in range(1920):
            draw.line(
                [(0, i), (1080, i)],
                fill=(15, 20 + int((i / 1920) * 30), 35 + int((i / 1920) * 50)),
            )
        print("   [Visual Source] gradient-fallback")
        return bg_img, True, "gradient-fallback"

    bot.fetch_scene_asset = fixed_fetch_scene_asset
    return bot
