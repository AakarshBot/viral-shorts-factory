from pathlib import Path

import ultimate_bot
from final_qc_runtime import _validate_metadata, validate_final_upload_metadata
from script_runtime import _extractive_script_fallback, contains_retention_bait, validate_content_density
from script_runtime import _extractive_script_fallback, contains_retention_bait, validate_content_density
from subtitle_runtime import generate_readable_karaoke_clip
from youtube_comment_runtime import build_description_hashtags, ensure_shorts_title
from factory_function_coverage import collect_factory_function_coverage


def test_every_scene_caption_contract_removed_from_compile():
    source = Path(ultimate_bot.__file__).read_text(encoding="utf-8")
    assert "is_hook_scene" not in source
    assert "is_outro_scene" not in source
    assert "is_top5_scene" not in source
    assert "for active_idx, wt in enumerate(chunk)" in source


def test_top5_caption_position_stays_above_lower_safe_area():
    y = ultimate_bot._caption_y_position(1920, "bg", "top5")
    assert y + 220 <= int(1920 * 0.80)


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


def test_subtitle_renderer_uses_active_word_parameter(tmp_path):
    path = tmp_path / "caption.png"
    generate_readable_karaoke_clip(
        [
            {"word": "First"},
            {"word": "second"},
            {"word": "word"},
        ],
        1,
        None,
        1080,
        str(path),
    )
    assert path.is_file()
