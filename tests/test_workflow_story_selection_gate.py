import pytest

from workflow_runtime import WorkflowController, _validate_selected_story


def test_start_production_rejects_missing_selection_before_runtime_setup():
    controller = object.__new__(WorkflowController)

    with pytest.raises(ValueError, match="explicitly selected discovered story"):
        controller.start_production({}, None)


def test_start_production_rejects_unverified_story_payload_before_runtime_setup():
    controller = object.__new__(WorkflowController)
    story = {"title": "A story without discovery metadata"}

    with pytest.raises(ValueError, match="verified 28-candidate discovery pool"):
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


def test_validate_selected_story_accepts_later_candidate_from_expanded_pool():
    story = {
        "title": "Later discovery candidate",
        "discovery_rank": 11,
        "story_key": "later discovery candidate https example com/later",
    }

    validated = _validate_selected_story(story)

    assert validated == story
    assert validated is not story

def test_validate_selected_story_accepts_dashboard_candidate_from_expanded_pool():
    story = {
        "title": "Dashboard candidate beyond production pool",
        "discovery_rank": 20,
        "story_key": "dashboard candidate beyond production pool https example com/dashboard",
        "dashboard_discovery_pool": True,
    }

    validated = _validate_selected_story(story)

    assert validated == story
    assert validated is not story


def test_legacy_discovery_path_is_removed():
    import workflow_runtime

    assert not hasattr(workflow_runtime, "discover_three_candidates")
    assert not hasattr(workflow_runtime, "_remove_near_duplicates")
    assert not hasattr(workflow_runtime, "_diverse_top_three")
