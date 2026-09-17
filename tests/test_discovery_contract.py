import pytest

from workflow_runtime import discover_three_candidates


class _Rows:
    def fetchall(self):
        return []


class _Conn:
    def execute(self, *_args, **_kwargs):
        return _Rows()


class _Bot:
    CONTENT_CATEGORIES = {
        "national_global_affairs": {"label": "National & Global Affairs"},
        "sports_stories_of_day": {"label": "Sports"},
    }

    def __init__(self, stories):
        self._stories = stories
        self.gather_calls = 0
        self.production_calls = 0

    def gather_and_filter_stories(self, *_args, **_kwargs):
        self.gather_calls += 1
        return [dict(story) for story in self._stories]

    def write_script(self, *_args, **_kwargs):
        self.production_calls += 1
        raise AssertionError("script generation must not run during discovery")

    def generate_audio(self, *_args, **_kwargs):
        self.production_calls += 1
        raise AssertionError("audio generation must not run during discovery")

    def compile_video(self, *_args, **_kwargs):
        self.production_calls += 1
        raise AssertionError("rendering must not run during discovery")

    def upload_to_youtube(self, *_args, **_kwargs):
        self.production_calls += 1
        raise AssertionError("upload must not run during discovery")


def _story(title, score):
    return {
        "title": title,
        "url": f"https://example.com/{score}",
        "source": "Example News",
        "candidate_score": score,
    }


def test_discovery_returns_exactly_three_candidates_without_production_calls():
    bot = _Bot(
        [
            _story("Government announces new renewable energy targets", 9),
            _story("Central bank changes interest rate guidance", 8),
            _story("Major technology company launches new processor", 7),
            _story("National rail network expands high speed service", 6),
        ]
    )

    candidates = discover_three_candidates(
        bot,
        {"format_mode": "regular", "category": "national_global_affairs", "language": "english"},
        _Conn(),
    )

    assert len(candidates) == 3
    assert [item["discovery_rank"] for item in candidates] == [1, 2, 3]
    assert bot.gather_calls == 1
    assert bot.production_calls == 0


def test_discovery_blocks_underfilled_candidate_set():
    bot = _Bot(
        [
            _story("Government announces new renewable energy targets", 9),
            _story("Central bank changes interest rate guidance", 8),
        ]
    )

    with pytest.raises(ValueError, match="exactly 3"):
        discover_three_candidates(
            bot,
            {"format_mode": "regular", "category": "national_global_affairs", "language": "english"},
            _Conn(),
        )

    assert bot.gather_calls == 1
    assert bot.production_calls == 0
