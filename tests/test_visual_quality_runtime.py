from PIL import Image, ImageDraw

from visual_quality_runtime import cover_crop, quality_gate


def test_quality_gate_rejects_low_resolution():
    image = Image.new("RGB", (640, 360), "black")
    import io
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    ok, reason, score = quality_gate(buffer.getvalue())
    assert ok is False
    assert reason.startswith("resolution-too-low")
    assert score == 0.0


def test_cover_crop_keeps_off_center_subject_visible():
    image = Image.new("RGB", (1600, 900), "black")
    draw = ImageDraw.Draw(image)
    # Put the only meaningful subject well to the right of centre.
    draw.rectangle((1180, 180, 1560, 720), fill="white")

    cropped = cover_crop(image, (1080, 1920), visual_genre="GENERAL_PHOTO")
    assert cropped.size == (1080, 1920)

    # The right half should contain substantial subject pixels. A blind centre
    # crop would lose almost all of this rectangle.
    right = cropped.crop((540, 0, 1080, 1920))
    mean = sum(right.convert("L").getdata()) / (540 * 1920)
    assert mean > 20.0


def test_cover_crop_never_stretches_the_source():
    image = Image.new("RGB", (900, 1600), "white")
    cropped = cover_crop(image, (1080, 1920), visual_genre="PERSON_PORTRAIT")
    assert cropped.size == (1080, 1920)
