from subtitle_runtime import build_subtitle_plan


def test_build_subtitle_plan_is_data_only_and_render_ready():
    plan = build_subtitle_plan([
        [
            {"word": "India", "start": 0.0, "end": 0.35},
            {"word": "won", "start": 0.36, "end": 0.70},
            {"word": "the", "start": 0.71, "end": 0.90},
            {"word": "match", "start": 0.91, "end": 1.20},
            {"word": "today", "start": 1.30, "end": 1.60},
        ]
    ])
    assert len(plan) == 1
    assert len(plan[0]) == 2
    assert plan[0][0]["text"] == "India won the match"
    assert plan[0][0]["start"] == 0.0
    assert plan[0][0]["end"] == 1.2
    assert plan[0][0]["words"][2] == {"text": "the", "start": 0.71, "end": 0.9}


def test_subtitle_plan_breaks_on_pause_and_cleans_artifacts():
    plan = build_subtitle_plan([
        [
            {"word": "Lead", "start": 0.0, "end": 0.3},
            {"word": "_arrow_right", "start": 0.31, "end": 0.35},
            {"word": "score", "start": 1.1, "end": 1.4},
        ]
    ])
    assert len(plan[0]) == 2
    assert plan[0][0]["text"] == "Lead"
    assert plan[0][1]["text"] == "score"


def test_subtitle_plan_keeps_scenes_separate():
    plan = build_subtitle_plan([
        [{"word": "One", "start": 0.0, "end": 0.3}],
        [{"word": "Two", "start": 0.0, "end": 0.3}],
    ])
    assert [cue["text"] for cue in plan[0]] == ["One"]
    assert [cue["text"] for cue in plan[1]] == ["Two"]
