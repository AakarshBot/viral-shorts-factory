import pytest

from editorial_runtime import _normalise_editorial_records, score_candidates


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


def test_model_hard_reject_is_advisory_for_editorial_scoring():
    story = {"title": "Routine update", "text": "A routine update with factual substance for a short."}
    scored = [{
        "hook_strength": 3,
        "narrative_completeness": 3,
        "audience_fit": 3,
        "monetization_risk": 8,
        "shelf_life": 3,
        "hard_reject": True,
    }]

    result = score_candidates(scored, [story], {}, "", "regular")

    assert len(result) == 1
    assert result[0]["composite_score"] < 3.0


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


def test_missing_editorial_records_use_neutral_fallbacks():
    stories = [
        {"title": "Story one", "text": "A factual story with enough context.", "velocity_score": 1.5},
        {"title": "Story two", "text": "Another factual story with enough context.", "velocity_score": 0.0},
    ]
    scored = [{
        "hook_strength": 8,
        "narrative_completeness": 8,
        "audience_fit": 8,
        "monetization_risk": 2,
        "shelf_life": 8,
        "hard_reject": False,
    }]

    normalised = _normalise_editorial_records(scored, stories)
    assert len(normalised) == 2
    assert normalised[0]["hook_strength"] == 8.0
    assert normalised[1]["hook_strength"] < 8.0
    assert normalised[1]["hard_reject"] is False


def test_malformed_editorial_record_fields_are_normalized():
    story = {"title": "Malformed score story", "text": "Enough factual context for scoring."}
    scored = [{
        "hook_strength": "bad",
        "narrative_completeness": None,
        "audience_fit": "7",
        "monetization_risk": "false",
        "shelf_life": "9",
        "hard_reject": "false",
    }]

    normalised = _normalise_editorial_records(scored, [story])
    assert len(normalised) == 1
    assert normalised[0]["hook_strength"] == 5.0
    assert normalised[0]["narrative_completeness"] == 5.0
    assert normalised[0]["audience_fit"] == 7.0
    assert normalised[0]["monetization_risk"] == 5.0
    assert normalised[0]["shelf_life"] == 9.0
    assert normalised[0]["hard_reject"] is False
