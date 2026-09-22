from channel_strategy_runtime import (
    CHANNEL_IDEAL_MAX_SECONDS,
    CHANNEL_SOFT_MAX_SECONDS,
    CHANNEL_TITLE_MAX_CHARS,
    candidate_gate,
    score_story,
    title_package_score,
)


def test_conflict_and_quote_story_get_strong_channel_signal():
    story = {
        "title": "India called arrogant by ex-Pakistani batter",
        "description": "The former batter criticised India after the latest match.",
        "event_entities": ["India", "Pakistan"],
    }
    signal = score_story(story)
    assert signal["strong_hook"] is True
    assert signal["score"] >= 4.0


def test_routine_service_story_is_suppressed_without_hook():
    story = {
        "title": "India vs Japan T20I match timings and where to watch",
        "description": "Full schedule, venue and streaming details.",
    }
    signal = score_story(story)
    ok, reason = candidate_gate(signal, hook_potential=2.0, importance=4.0)
    assert ok is False
    assert "routine/admin" in reason


def test_major_result_can_survive_even_when_not_controversial():
    story = {
        "title": "India's 7-run shock over England",
        "description": "India completed a seven-run win in a major tournament match.",
        "event_entities": ["India", "England"],
    }
    signal = score_story(story)
    ok, _ = candidate_gate(signal, hook_potential=4.8, importance=7.5)
    assert ok is True


def test_title_package_prefers_channel_length():
    assert CHANNEL_TITLE_MAX_CHARS == 55
    assert title_package_score("India called arrogant by ex-Pakistani batter") > title_package_score(
        "Cricket match squads | AMS vs MHK, 1st Match, Sher-E-Punjab T20 League, 2026"
    )


def test_duration_policy_matches_channel_observation():
    assert CHANNEL_IDEAL_MAX_SECONDS == 30.0
    assert CHANNEL_SOFT_MAX_SECONDS == 35.0


def test_final_editorial_gate_weights_hook_and_channel_fit_together():
    from editorial_runtime import score_candidates

    stories = [
        {"title": "Strong hook", "freshfeed_channel_fit_score": 9.0},
        {"title": "Broad but passive", "freshfeed_channel_fit_score": 1.0},
    ]
    scores = [
        {"hook_strength": 9, "narrative_completeness": 6, "audience_fit": 6, "monetization_risk": 8, "shelf_life": 6},
        {"hook_strength": 6, "narrative_completeness": 9, "audience_fit": 9, "monetization_risk": 8, "shelf_life": 9},
    ]
    ranked = score_candidates(scores, stories, {}, "", "regular")
    assert ranked[0]["title"] == "Strong hook"


def test_freshfeed_pattern_score_separates_winning_hook_combinations():
    conflict = score_story({
        "title": "India called arrogant by ex-Pakistani batter",
        "event_entities": ["India", "Pakistan"],
        "event_actions": ["called"],
    })
    question = score_story({
        "title": "Did Beating India Actually Ruin Pakistan Cricket?",
        "event_entities": ["India", "Pakistan"],
        "event_actions": ["beat", "ruin"],
    })
    quote = score_story({
        "title": "Rashid Khan Calls Indian Sensation God-Gifted",
        "event_entities": ["Rashid Khan", "India"],
        "event_actions": ["calls"],
    })

    assert conflict["freshfeed_pattern_score"] >= 3.0
    assert question["freshfeed_pattern_score"] >= 3.0
    assert quote["freshfeed_pattern_score"] > conflict["freshfeed_pattern_score"]
    assert quote["marquee_person_hits"] >= 1
    assert "marquee + tension synergy" in quote["freshfeed_pattern_reasons"]


def test_routine_squad_listing_is_penalised_but_selection_drama_gets_hook():
    routine = score_story({
        "title": "India announce squad for upcoming series",
        "event_actions": ["announce"],
    })
    drama = score_story({
        "title": "India drop star batter from squad after controversy",
        "event_entities": ["India"],
        "event_actions": ["drop"],
    })

    assert routine["routine_or_admin"] is True
    assert routine["freshfeed_pattern_score"] < drama["freshfeed_pattern_score"]
    assert drama["strong_hook"] is True


def test_compact_story_gets_better_scope_than_long_explanatory_story():
    compact = score_story({
        "title": "India's 7-run shock over England",
        "event_actions": ["won"],
    })
    broad = score_story({
        "title": "Everything you need to know about the complete background and history of India's latest tournament campaign",
        "event_actions": ["explained"],
    })

    assert compact["freshfeed_scope_score"] >= 7.0
    assert broad["freshfeed_scope_score"] < compact["freshfeed_scope_score"]
    assert broad["freshfeed_pattern_score"] < compact["freshfeed_pattern_score"]


def test_quoted_headline_is_an_immediate_hook():
    signal = score_story({
        "title": '"India are arrogant," says ex-Pakistan batter',
    })
    assert signal["strong_hook"] is True
    assert signal["quoted_title"] is True
    assert "bold quote/statement" in signal["freshfeed_pattern_reasons"]


def test_freshfeed_pattern_can_keep_a_strong_story_gate_eligible():
    signal = {
        "score": 1.0,
        "freshfeed_pattern_score": 4.0,
        "strong_hook": True,
        "routine_or_admin": False,
    }
    assert candidate_gate(signal, hook_potential=5.0, importance=4.0)[0] is True
