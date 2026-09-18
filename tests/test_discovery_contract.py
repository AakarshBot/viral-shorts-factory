import pytest

from workflow_runtime import MAX_DISCOVERY_CANDIDATES, discover_three_candidates


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


def test_discovery_returns_stable_pool_up_to_28_without_production_calls():
    stories = [
        _story("Government announces new renewable energy targets", 12),
        _story("Central bank changes interest rate guidance", 11),
        _story("Major technology company launches new processor", 10),
        _story("National rail network expands high speed service", 9),
        _story("Weather service issues regional storm warning", 8),
        _story("University opens new research center", 7),
        _story("Hospital network adds emergency capacity", 6),
        _story("Manufacturers report stronger export orders", 5),
        _story("Cities announce revised public transport plans", 4),
        _story("Energy grid operator publishes demand outlook", 3),
        _story("Agriculture ministry releases seasonal forecast", 2),
        _story("Telecom regulator publishes market update", 1),
        _story("This thirteenth story must not enter the discovery pool", 0),
    ]
    stories.extend(
        _story(f"Additional discovery story {index}", 20 - index)
        for index in range(1, 16)
    )
    bot = _Bot(stories)

    candidates = discover_three_candidates(
        bot,
        {"format_mode": "regular", "category": "national_global_affairs", "language": "english"},
        _Conn(),
    )

    assert len(candidates) == MAX_DISCOVERY_CANDIDATES == 28
    assert [item["discovery_rank"] for item in candidates] == list(range(1, 13))
    assert [item["title"] for item in candidates[:3]] == [
        "Government announces new renewable energy targets",
        "Central bank changes interest rate guidance",
        "Major technology company launches new processor",
    ]
    assert candidates[0]["discovery_rank"] == 1
    assert candidates[-1]["discovery_rank"] == 28
    assert all(item.get("discovery_reason") for item in candidates)
    assert bot.gather_calls == 1
    assert bot.production_calls == 0


def test_discovery_allows_underfilled_candidate_set():
    bot = _Bot(
        [
            _story("Government announces new renewable energy targets", 9),
            _story("Central bank changes interest rate guidance", 8),
        ]
    )

    candidates = discover_three_candidates(
        bot,
        {"format_mode": "regular", "category": "national_global_affairs", "language": "english"},
        _Conn(),
    )

    assert len(candidates) == 2
    assert [item["discovery_rank"] for item in candidates] == [1, 2]
    assert bot.gather_calls == 1
    assert bot.production_calls == 0
