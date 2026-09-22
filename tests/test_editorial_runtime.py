import pytest

from editorial_runtime import score_candidates


@pytest.fixture
def strong_story():
    return {
        "title": "Aaryavir Sehwag stars for India U19 in win over Australia",
        "text": "Virender Sehwag's son Aaryavir Sehwag made a notable contribution for India U19 as the team beat Australia U19.",
        "freshfeed_channel_fit_score": 7.5,
    }


def test_high_monetization_risk_hard_reject_is_soft_for_strong_safe_story(strong_story):
    scored = [{
        "hook_strength": 8,
        "narrative_completeness": 8,
        "audience_fit": 8,
        "monetization_risk": 8,
        "shelf_life": 7,
        "hard_reject": True,
    }]

    result = score_candidates(scored, [strong_story], {}, "", "regular")

    assert len(result) == 1
    assert result[0]["monetization_risk"] == 8.0
    assert result[0]["composite_score"] > 0


def test_weak_hard_reject_is_still_rejected():
    story = {"title": "Routine update", "text": "A routine update with little factual substance."}
    scored = [{
        "hook_strength": 3,
        "narrative_completeness": 3,
        "audience_fit": 3,
        "monetization_risk": 8,
        "shelf_life": 3,
        "hard_reject": True,
    }]

    assert score_candidates(scored, [story], {}, "", "regular") == []


def test_deterministic_safety_block_remains_hard_reject():
    story = {
        "title": "Police investigate child sexual abuse case",
        "text": "Authorities said the case involved alleged child sexual abuse.",
    }
    scored = [{
        "hook_strength": 9,
        "narrative_completeness": 9,
        "audience_fit": 9,
        "monetization_risk": 8,
        "shelf_life": 8,
        "hard_reject": True,
    }]

    assert score_candidates(scored, [story], {}, "", "regular") == []
