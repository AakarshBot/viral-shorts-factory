from types import SimpleNamespace

import factory_runtime
import workflow_runtime


class _Bot:
    def __init__(self):
        self.safe_text = lambda value: str(value or "")
        self.get_smart_metrics = lambda *_args, **_kwargs: {}
        self.CONTENT_CATEGORIES = {}
        self.LANGUAGES = {}
        self.gather_and_filter_stories = lambda *_args, **_kwargs: []
        self.editorial_gate_batch = lambda *_args, **_kwargs: []
        self.process_scored_candidates = lambda *_args, **_kwargs: []
        self.passes_quality_gate = lambda *_args, **_kwargs: True
        self.write_script = lambda *_args, **_kwargs: {}
        self.run_robot = lambda *_args, **_kwargs: None


def _story(title, score):
    return {"title": title, "candidate_score": score}


def test_live_dashboard_binding_preserves_three_diverse_first_choices():
    bot = _Bot()
    factory_runtime.patch_dashboard_runtime(bot)

    stories = [
        _story("India announces new renewable energy targets", 10),
        _story("India announces new renewable energy targets for clean power", 9),
        _story("India announces renewable energy target update for clean power", 8),
        _story("Central bank changes interest rate guidance", 7),
    ]

    selected = workflow_runtime._diverse_top_three(stories)

    assert len(selected) == 3
    assert selected[0]["title"].startswith("India announces")
    assert selected[1]["title"] == "Central bank changes interest rate guidance"
    assert selected[2]["title"].startswith("India announces")


def test_live_dashboard_binding_still_returns_three_distinct_candidates():
    bot = _Bot()
    factory_runtime.patch_dashboard_runtime(bot)

    stories = [
        _story("Government approves new national rail investment", 10),
        _story("Central bank changes interest rate guidance", 9),
        _story("Major technology company launches new processor", 8),
        _story("National weather service issues storm warning", 7),
    ]

    selected = workflow_runtime._diverse_top_three(stories)

    assert len(selected) == 3
    assert [item["title"] for item in selected] == [
        "Government approves new national rail investment",
        "Central bank changes interest rate guidance",
        "Major technology company launches new processor",
    ]
