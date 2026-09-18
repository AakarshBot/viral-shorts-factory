from PIL import Image
import io

from visual_quality_runtime import cover_crop, quality_gate


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


def test_cover_crop_keeps_off_center_subject_visible():
    image = Image.new("RGB", (1600, 900), "black")
    pixels = image.load()
    for y in range(180, 720):
        for x in range(1180, 1560):
            pixels[x, y] = (255, 255, 255)

    cropped = cover_crop(image, (1080, 1920))
    assert cropped.size == (1080, 1920)

    right_half = cropped.crop((540, 0, 1080, 1920)).convert("L")
    assert right_half.getbbox() is not None


def test_cover_crop_never_stretches_the_source():
    image = Image.new("RGB", (900, 1600), "white")
    cropped = cover_crop(image, (1080, 1920))
    assert cropped.size == (1080, 1920)
