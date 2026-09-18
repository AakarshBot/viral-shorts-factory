from PIL import Image

from branding_runtime import (
    LOGO_BOX_SIZE,
    LOGO_INNER_SIZE,
    build_scene_branding_overlays,
    source_credit_for_type,
)


class _Bot:
    BASE_DIR = "."


def test_source_credit_is_normalized():
    assert source_credit_for_type("commons") == "SOURCE · Wikimedia Commons"
    assert source_credit_for_type("news_source", "Source: Reuters") == "SOURCE · Reuters"


def test_final_branding_has_fixed_shorts_geometry(tmp_path):
    brand = tmp_path / "brand_assets"
    brand.mkdir()
    logo = Image.new("RGB", (800, 500), "white")
    logo.save(brand / "channels4_profile.jpg")

    bot = _Bot()
    bot.BASE_DIR = tmp_path

    layers = build_scene_branding_overlays(bot, 1080, 1920, "SOURCE · Reuters")

    assert len(layers) == 2
    assert layers[0].shape == (1920, 1080, 4)
    assert layers[1].shape == (1920, 1080, 4)

    # The logo badge is fixed-size and positioned inside the 36 px safe margin.
    alpha = layers[0][:, :, 3]
    assert alpha[36:36 + LOGO_BOX_SIZE, 1080 - 36 - LOGO_BOX_SIZE:1080 - 36].max() > 0
    assert LOGO_INNER_SIZE < LOGO_BOX_SIZE


def test_source_badge_is_bottom_right():
    layers = build_scene_branding_overlays(_Bot(), 1080, 1920, "SOURCE · Reuters")
    alpha = layers[1][:, :, 3]
    assert alpha[-100:, -420:].max() > 0
