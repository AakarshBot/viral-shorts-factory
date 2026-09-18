from PIL import Image, ImageDraw

from subtitle_runtime import (
    _patch_deep_dive_subtitle_condition,
    create_glossy_logo_watermark,
    generate_readable_karaoke_clip,
    render_premium_top5_card,
)


class _Bot:
    def __init__(self):
        self.run_robot = self._run_robot

    def _run_robot(self):
        return None


def test_logo_watermark_removes_edge_white_background(tmp_path):
    logo_path = tmp_path / "logo.jpg"
    image = Image.new("RGB", (48, 48), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 10, 38, 38), fill=(0, 120, 200))
    image.save(logo_path, quality=98)

    result = create_glossy_logo_watermark(str(logo_path), size=112)

    assert result is not None
    assert result.size == (112, 112)
    assert result.getpixel((0, 0))[3] < 255
    assert result.getpixel((56, 56))[3] > 0


def test_deep_dive_subtitle_clip_is_simple_glass(tmp_path):
    background = tmp_path / "scene.png"
    Image.new("RGB", (1080, 1920), (30, 50, 75)).save(background)
    output = tmp_path / "subtitle.png"

    path = generate_readable_karaoke_clip(
        [{"word": "This"}, {"word": "is"}, {"word": "the"}, {"word": "story"}],
        -1,
        None,
        1080,
        str(output),
        bg_img_path=str(background),
        source_type="bg",
    )

    rendered = Image.open(path).convert("RGBA")
    alpha = rendered.getchannel("A")
    assert rendered.size == (1080, 300)
    assert alpha.getbbox() is not None
    assert rendered.getpixel((540, 150))[3] > 0


def test_top5_card_uses_same_glass_language():
    background = Image.new("RGB", (1080, 1920), (24, 34, 48))
    rendered = render_premium_top5_card(
        background,
        3,
        5,
        "A concise premium summary of the third story.",
        font_choice=None,
    )

    assert rendered.mode == "RGBA"
    assert rendered.size == (1080, 1920)
    assert rendered.getpixel((540, 600))[3] == 255


def test_compile_patch_keeps_deep_dive_scene_one_clean_and_removes_duplicate_logo():
    def compile_video():
        idx = 0
        is_outro_scene = False
        is_hook_scene = True
        word_timings = [{}]
        subtitle_enabled = False
        if not is_outro_scene and not is_hook_scene and idx < len(word_timings):
            subtitle_enabled = True
        logo_file_path = os.path.join(BRAND_ASSETS_DIR, "logo.png")
        if not os.path.exists(logo_file_path):
            logo_file_path = os.path.join(
                BRAND_ASSETS_DIR, "channels4_profile.jpg"
            )
        glossy_logo_img = create_glossy_logo_watermark(
            logo_file_path, size=110
        )
        if glossy_logo_img and format_mode in [
            "regular", "trending", "tech_reviews"
        ]:
            pass
        print("   [+] Writing video file to disk for Quality Control...")
        return "subtitle-enabled" if subtitle_enabled else "not-enabled"

    import os

    bot = _Bot()
    bot.run_robot.__globals__["compile_video"] = compile_video
    bot.run_robot.__globals__["BRAND_ASSETS_DIR"] = "/does/not/exist"
    bot.run_robot.__globals__["format_mode"] = "regular"

    def fail_if_logo_called(*_args, **_kwargs):
        raise AssertionError("legacy compile-time logo renderer should not be called")

    bot.run_robot.__globals__["create_glossy_logo_watermark"] = fail_if_logo_called

    assert _patch_deep_dive_subtitle_condition(bot) is True
    assert bot.compile_video() == "not-enabled"

    bot.run_robot.__globals__["format_mode"] = "top5"
    assert bot.compile_video() == "not-enabled"

    assert getattr(bot.compile_video, "_premium_compile_logo_bound", False) is True
