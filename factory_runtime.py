"""Runtime hardening helpers for the Streamlit dashboard."""

import sys
import traceback


def install_safe_exception_hook():
    """Install a headless-safe exception hook that never waits for input."""
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
    """Patch deterministic scoring/classification bugs for dashboard runs."""
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

            # The field is explicitly "monetization_risk": higher risk must
            # lower the score, not increase it.
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
    return bot
