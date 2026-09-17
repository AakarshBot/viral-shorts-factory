from pathlib import Path

from branding_runtime import _assets, patch_branding_pipeline


class _Bot:
    def __init__(self, root):
        self.BASE_DIR = root

        def run_robot():
            return None

        self.run_robot = run_robot


def test_branding_accepts_primary_logo_filename(tmp_path):
    brand = tmp_path / "brand_assets"
    brand.mkdir()
    primary = brand / "logo.png.jpg"
    primary.write_bytes(b"logo")

    logo, overlay = _assets(_Bot(tmp_path))

    assert logo == primary
    assert overlay == brand / "overlay.png"


def test_branding_falls_back_to_channel_profile_logo(tmp_path):
    brand = tmp_path / "brand_assets"
    brand.mkdir()
    fallback = brand / "channels4_profile.jpg"
    fallback.write_bytes(b"logo")

    logo, _ = _assets(_Bot(tmp_path))

    assert logo == fallback


def test_branding_compile_wrapper_is_installed_once(tmp_path, monkeypatch):
    bot = _Bot(tmp_path)
    calls = []

    def compile_video(*args, **kwargs):
        return "/tmp/rendered.mp4"

    def finish(target_bot, video_path):
        calls.append((target_bot, video_path))
        return video_path

    bot.run_robot.__globals__["compile_video"] = compile_video
    monkeypatch.setattr("branding_runtime.apply_branded_finish", finish)

    patch_branding_pipeline(bot)
    result = bot.run_robot.__globals__["compile_video"]("scene-data")

    assert result == "/tmp/rendered.mp4"
    assert calls == [(bot, "/tmp/rendered.mp4")]
    assert getattr(bot.run_robot.__globals__["compile_video"], "_branding_wrapped", False) is True

    patch_branding_pipeline(bot)
    assert len(calls) == 1
