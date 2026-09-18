from PIL import Image, ImageDraw

from subtitle_runtime import (
    create_glossy_logo_watermark,
    generate_readable_karaoke_clip,
    render_premium_top5_card,
)


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
