from pathlib import Path

import ultimate_bot
from final_qc_runtime import _validate_metadata, validate_final_upload_metadata
from script_guard_runtime import source_only_fallback
from script_runtime import _extractive_script_fallback
from subtitle_runtime import generate_readable_karaoke_clip
from youtube_comment_runtime import build_description_hashtags, ensure_shorts_title


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


def test_scene_one_hook_is_at_most_eight_words():
    text = "India announces a major policy change that could reshape the market today."
    headline = ultimate_bot._hook_headline_from_scene(text)
    assert len(headline.split()) <= 8
    assert headline == "India announces a major policy change that could"


def test_hook_overlay_is_written_inside_its_small_safe_card(tmp_path):
    path = tmp_path / "hook.png"
    result = ultimate_bot._render_hook_headline_overlay(
        "India announces a major policy change today",
        None,
        str(path),
    )
    assert result == str(path)
    assert path.is_file()


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
        "AI launch",
    )
    assert 0 <= len(tags) <= 3
    assert tags[0] == "#AIlaunch"


def test_fallback_title_variants_have_no_shorts_suffix():
    story = {
        "title": "Example story",
        "text": "This is a sufficiently detailed source sentence with many useful words. "
                "Another source sentence provides additional factual context for testing.",
    }
    for builder in (
        lambda: source_only_fallback(story, {}, "news", "regular"),
        lambda: _extractive_script_fallback(story, {}, "news", "regular"),
    ):
        titles = builder()["titles"]
        assert len(titles) == 3
        assert all("#shorts" not in title.lower() for title in titles)


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
