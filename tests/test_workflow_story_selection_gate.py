import pytest

from workflow_runtime import WorkflowController, _validate_selected_story


def test_start_production_rejects_missing_selection_before_runtime_setup():
    controller = object.__new__(WorkflowController)

    with pytest.raises(ValueError, match="explicitly selected discovered story"):
        controller.start_production({}, None)


def test_start_production_rejects_unverified_story_payload_before_runtime_setup():
    controller = object.__new__(WorkflowController)
    story = {"title": "A story without discovery metadata"}

    with pytest.raises(ValueError, match="verified three-candidate discovery set"):
        controller.start_production({}, story)


def test_validate_selected_story_accepts_verified_discovery_candidate():
    story = {
        "title": "Verified discovery candidate",
        "discovery_rank": 2,
        "story_key": "verified discovery candidate https example com/story",
    }

    validated = _validate_selected_story(story)

    assert validated == story
    assert validated is not story
