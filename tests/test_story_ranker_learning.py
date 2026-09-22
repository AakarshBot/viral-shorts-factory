import story_ranker


def _row(value, genre="technology", fmt="regular", language="english", stayed_to_watch=None, hook_style=None):
    return {
        "status": "COMPLETED",
        "video_id": f"video-{value}",
        "avg_view_percentage": value,
        "stayed_to_watch": stayed_to_watch,
        "genre": genre,
        "format_used": fmt,
        "language_used": language,
        "topic": f"topic {value}",
        "hook_style_used": hook_style,
        "hook_type": hook_style,
    }


def test_channel_performance_prior_is_neutral_without_history():
    score, samples = story_ranker._channel_performance_prior(
        [], "technology", "regular", "english"
    )
    assert score == 5.0
    assert samples == 0


def test_channel_performance_prior_learns_context_with_shrinkage():
    rows = [
        _row(80, genre="technology"),
        _row(75, genre="technology"),
        _row(40, genre="entertainment"),
        _row(45, genre="business_finance"),
    ]
    score, samples = story_ranker._channel_performance_prior(
        rows, "technology", "regular", "english"
    )
    assert samples == 2
    assert 5.0 < score < 7.0


def test_channel_performance_prior_does_not_overreact_to_one_outlier():
    rows = [
        _row(95, genre="sports"),
        _row(50, genre="technology"),
        _row(50, genre="entertainment"),
        _row(50, genre="business_finance"),
        _row(50, genre="health_lifestyle"),
    ]
    score, samples = story_ranker._channel_performance_prior(
        rows, "sports", "regular", "english"
    )
    assert samples == 1
    assert score < 7.0


def test_channel_performance_prior_blends_stayed_to_watch_when_available():
    rows = [
        _row(90, genre="sports", stayed_to_watch=40),
        _row(50, genre="technology"),
        _row(50, genre="entertainment"),
        _row(50, genre="business_finance"),
        _row(50, genre="health_lifestyle"),
    ]
    score, samples = story_ranker._channel_performance_prior(
        rows, "sports", "regular", "english"
    )

    assert samples == 1
    assert 5.0 < score < 6.0



def test_channel_performance_prior_can_learn_hook_family():
    rows = [
        _row(88, genre="sports", stayed_to_watch=55, hook_style="Conflict / Accusation"),
        _row(84, genre="sports", stayed_to_watch=52, hook_style="Conflict / Accusation"),
        _row(45, genre="sports", stayed_to_watch=20, hook_style="Direct Factual Headline"),
        _row(50, genre="technology", stayed_to_watch=25, hook_style="Direct Factual Headline"),
        _row(50, genre="entertainment", stayed_to_watch=25, hook_style="Direct Factual Headline"),
    ]

    conflict_score, conflict_samples = story_ranker._channel_performance_prior(
        rows, "sports", "regular", "english", "Conflict / Accusation"
    )
    direct_score, direct_samples = story_ranker._channel_performance_prior(
        rows, "sports", "regular", "english", "Direct Factual Headline"
    )

    assert conflict_samples == 2
    assert direct_samples == 3
    assert conflict_score > direct_score



def test_channel_performance_prior_accepts_stayed_to_watch_without_retention():
    rows = [
        {
            "status": "COMPLETED",
            "video_id": "video-stayed-only",
            "avg_view_percentage": None,
            "stayed_to_watch": 60,
            "genre": "sports",
            "format_used": "regular",
            "language_used": "english",
            "topic": "conflict story",
        },
        _row(40, genre="technology"),
    ]

    score, samples = story_ranker._channel_performance_prior(
        rows, "sports", "regular", "english"
    )

    assert samples == 1
    assert score > 5.0
