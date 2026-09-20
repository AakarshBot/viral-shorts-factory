from PIL import Image, ImageDraw

from subtitle_runtime import (
    generate_readable_karaoke_clip,
    render_premium_top5_card,
)


def test_subtitle_clip_is_simple_caption_card(tmp_path):
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
    assert rendered.size == (1080, 220)
    assert alpha.getbbox() is not None
    assert rendered.getpixel((540, 110))[3] > 0
    # The active subtitle renderer no longer draws the old gloss/accent border.
    assert rendered.getpixel((20, 20))[3] == 0


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


def test_array_like_caption_value_is_cleaned():
    class ArrayLike:
        def tolist(self):
            return ["Hello", "world"]

    from subtitle_runtime import _clean_word

    assert _clean_word(ArrayLike()) == "Hello world"

    assert _clean_word("Lead _arrow_right follow") == "Lead follow"
