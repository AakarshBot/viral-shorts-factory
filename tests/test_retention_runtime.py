from pathlib import Path

import ultimate_bot
from final_qc_runtime import _validate_metadata, validate_final_upload_metadata
from script_runtime import _extractive_script_fallback, contains_retention_bait, validate_content_density
from youtube_comment_runtime import build_description_hashtags, ensure_shorts_title
from factory_function_coverage import collect_factory_function_coverage


def test_compile_video_owns_the_subtitle_render_handoff():
    import inspect

    source = Path(ultimate_bot.__file__).read_text(encoding="utf-8")
    signature = inspect.signature(ultimate_bot.compile_video)
    assert list(signature.parameters) == [
        "scene_visual_packages",
        "audio_paths",
        "subtitle_plan",
        "format_mode",
    ]
    assert "subtitle_plan = build_subtitle_plan(word_timings)" in source
    assert "generate_karaoke_clip" not in source


def test_visual_cuts_keep_beats_at_or_below_four_seconds():
    assert ultimate_bot._scene_visual_segment_count(3.9) == 1
    assert ultimate_bot._scene_visual_segment_count(4.1) == 2
    assert ultimate_bot._scene_visual_segment_count(8.1) == 3


def test_first_scene_does_not_add_transient_headline_overlay():
    source = Path(ultimate_bot.__file__).read_text(encoding="utf-8")
    assert "_hook_headline_from_scene" not in source
    assert "_render_hook_headline_overlay" not in source
    assert "hook_headline.png" not in source
    assert "FadeIn" not in source
    assert "FadeOut" not in source


def test_audio_loudnorm_command_targets_minus_fourteen_lufs(monkeypatch, tmp_path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return None

    monkeypatch.setattr(ultimate_bot.subprocess, "run", fake_run)
    output = ultimate_bot._normalize_audio_loudness(
        str(tmp_path / "input.mp4"),
        str(tmp_path / "output.mp4"),
    )
    assert output.endswith("output.mp4")
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in captured["command"]
    assert captured["command"][captured["command"].index("-c:v") + 1] == "copy"


def test_legacy_shorts_titles_are_cleaned_without_suffix():
    assert ensure_shorts_title("A useful story #shorts") == "A useful story"
    assert "#shorts" not in ensure_shorts_title("A useful story #shorts").lower()
    ok, _ = _validate_metadata("A useful story", "A sufficiently long description with enough words for metadata validation.")
    assert ok is True
    cleaned, _, _ = validate_final_upload_metadata(
        "A useful story #shorts",
        "A sufficiently long description with enough words for metadata validation.",
    )
    assert cleaned == "A useful story"


def test_description_has_at_most_three_relevant_hashtags():
    tags = build_description_hashtags(
        {"hashtags": ["#Technology", "#AI", "#Gadgets", "#Trending"]},
        "AI",
    )
    assert 0 <= len(tags) <= 3
    assert tags[0] == "#AI"


def test_fallback_title_variants_have_no_shorts_suffix():
    story = {
        "title": "Example story",
        "text": (
            "The organizer confirmed the latest development. "
            "Officials are working through the immediate issue and reviewing the current position. "
            "The background matters because the change affects the next scheduled stage. "
            "The practical consequence is that the response now focuses on resolving the issue without disrupting the wider plan."
        ),
    }

    titles = _extractive_script_fallback(story, {}, "news", "regular")["titles"]
    assert len(titles) == 3
    assert all("#shorts" not in title.lower() for title in titles)


def test_retention_bait_phrase_is_rejected_by_script_qc():
    assert contains_retention_bait("Wait till the end to find out what happened.")
    script = {
        "editorial_angle": "This explains the development, background, and practical consequence for viewers.",
        "titles": ["Headline", "Context", "Question"],
        "recommended_title_index": 1,
        "seo_description": "A factual explanation of the development, context, and practical consequence.",
        "script": [
            {
                "voiceover": "The organizer confirmed the latest development today.",
                "narrative_role": "hook",
                "primary_entity": "Organizer",
                "specific_search_prompt": "Organizer latest development",
            },
            {
                "voiceover": "Officials are working through the immediate issue and reviewing the current position.",
                "narrative_role": "development",
                "primary_entity": "Officials",
                "specific_search_prompt": "Officials current position",
            },
            {
                "voiceover": "The background matters because the change affects the next scheduled stage.",
                "narrative_role": "context",
                "primary_entity": "Organization",
                "specific_search_prompt": "Organization scheduled stage",
            },
            {
                "voiceover": "Wait till the end to find out why this matters, while officials prepare the response.",
                "narrative_role": "consequence",
                "primary_entity": "Officials",
                "specific_search_prompt": "Officials response",
            },
        ],
    }
    valid, reason = validate_content_density(script, {}, "regular")
    assert valid is False
    assert "retention" in reason.lower()

def test_retention_helpers_are_accounted_for_in_coverage():
    report = collect_factory_function_coverage()
    assert report["complete"] is True
    assert report["stale_map"] == []


def test_hook_quality_prefers_immediate_conflict_over_generic_setup():
    from script_runtime import _hook_quality_score

    story = {
        "title": "Former Pakistan batter calls India arrogant after clash",
        "summary": "The former Pakistan batter called India arrogant after the latest clash.",
        "hook_potential_score": 8.0,
    }
    strong = {
        "script": [{
            "voiceover": "A former Pakistan batter just called India arrogant after the clash.",
            "narrative_role": "hook",
        }]
    }
    weak = {
        "script": [{
            "voiceover": "Here is the latest update on India cricket and what happened today.",
            "narrative_role": "hook",
        }]
    }

    strong_score = _hook_quality_score(strong, story)
    weak_score = _hook_quality_score(weak, story)

    assert strong_score["score"] > weak_score["score"]
    assert "tension stated immediately" in strong_score["reasons"]
    assert "generic setup" in weak_score["reasons"]


def test_high_potential_story_warns_on_weak_opening_hook():
    from script_runtime import validate_content_density

    script = {
        "editorial_angle": "This explains the development, the relevant context, and its consequence.",
        "script": [
            {
                "voiceover": "Here is the latest update on India cricket and what happened today.",
                "narrative_role": "hook",
                "primary_entity": "India",
                "specific_search_prompt": "India cricket latest development",
            },
            {
                "voiceover": "The former batter criticised India's approach after the clash.",
                "narrative_role": "development",
                "primary_entity": "Former Pakistan batter",
                "specific_search_prompt": "former Pakistan batter India criticism",
            },
            {
                "voiceover": "The comments matter because the rivalry has generated repeated debate.",
                "narrative_role": "context",
                "primary_entity": "India Pakistan cricket",
                "specific_search_prompt": "India Pakistan cricket rivalry debate",
            },
            {
                "voiceover": "The immediate consequence is renewed attention on the rivalry and the comments.",
                "narrative_role": "consequence",
                "primary_entity": "India Pakistan cricket",
                "specific_search_prompt": "India Pakistan cricket comments reaction",
            },
        ],
    }
    valid, reason = validate_content_density(
        script,
        {
            "title": "Former Pakistan batter calls India arrogant after clash",
            "summary": "The former Pakistan batter called India arrogant after the latest clash.",
            "hook_potential_score": 8.0,
        },
        "regular",
    )

    assert valid is True
    assert "hook_quality_warning" in script



def test_title_packaging_rewards_compact_hook_aligned_title():
    from script_runtime import rank_title_candidates
    script = {
        "titles": [
            "Former batter calls India arrogant",
            "India vs Pakistan clash, 1st match, schedule, venue, timings, league 2026",
        ],
        "script": [{
            "voiceover": "Former batter calls India arrogant after the rivalry clash.",
            "primary_entity": "Former batter",
            "narrative_role": "hook",
        }],
    }
    result = rank_title_candidates(
        script,
        {
            "title": "Former batter calls India arrogant after rivalry clash",
            "description": "The former batter criticised India's approach after the clash.",
        },
    )
    assert result["recommended_title_index"] == 1
    assert result["scores"][0]["score"] > result["scores"][1]["score"]
