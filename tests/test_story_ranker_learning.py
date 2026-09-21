import story_ranker


def _row(value, genre="technology", fmt="regular", language="english"):
    return {
        "status": "COMPLETED",
        "video_id": f"video-{value}",
        "avg_view_percentage": value,
        "genre": genre,
        "format_used": fmt,
        "language_used": language,
        "topic": f"topic {value}",
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
