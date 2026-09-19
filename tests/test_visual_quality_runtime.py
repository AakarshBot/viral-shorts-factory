from PIL import Image
import io

from visual_quality_runtime import cover_crop, fit_visual_image, quality_gate


def _jpeg_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_quality_gate_rejects_low_resolution():
    image = Image.new("RGB", (640, 360), "black")
    ok, reason, score = quality_gate(_jpeg_bytes(image))
    assert ok is False
    assert reason.startswith("resolution-too-low")
    assert score == 0.0


def test_cover_crop_centres_standard_source():
    image = Image.new("RGB", (1600, 900), "black")
    pixels = image.load()
    for y in range(180, 720):
        for x in range(620, 980):
            pixels[x, y] = (255, 255, 255)

    cropped = cover_crop(image, (1080, 1920))
    assert cropped.size == (1080, 1920)

    center = cropped.crop((270, 0, 810, 1920)).convert("L")
    assert center.getbbox() is not None


def test_cover_crop_never_stretches_the_source():
    image = Image.new("RGB", (900, 1600), "white")
    cropped = cover_crop(image, (1080, 1920))
    assert cropped.size == (1080, 1920)



def test_brand_asset_fit_keeps_transparent_logo_intact():
    image = Image.new("RGBA", (1200, 600), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(180, 420):
        for x in range(320, 880):
            pixels[x, y] = (230, 30, 30, 255)

    fitted = fit_visual_image(image, (1080, 1920), "ORG_BRANDING")
    assert fitted.size == (1080, 1920)

    red = fitted.convert("RGB")
    assert red.getpixel((540, 960)) == (230, 30, 30)
    assert red.getpixel((10, 10)) != (230, 30, 30)
