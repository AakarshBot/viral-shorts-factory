from pathlib import Path

from branding_runtime import _assets, apply_branded_finish, patch_branding_pipeline


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

    assert _assets(_Bot(tmp_path)) == primary


def test_branding_falls_back_to_channel_profile_logo(tmp_path):
    brand = tmp_path / "brand_assets"
    brand.mkdir()
    fallback = brand / "channels4_profile.jpg"
    fallback.write_bytes(b"logo")

    assert _assets(_Bot(tmp_path)) == fallback


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


def test_logo_asset_is_passed_to_final_ffmpeg_finish(tmp_path, monkeypatch):
    brand = tmp_path / "brand_assets"
    brand.mkdir()
    logo = brand / "logo.png.jpg"
    logo.write_bytes(b"logo")

    video = tmp_path / "rendered.mp4"
    video.write_bytes(b"source-video")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        output = Path(command[-1])
        output.write_bytes(b"branded-video")
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr("branding_runtime.subprocess.run", fake_run)
    monkeypatch.setattr("branding_runtime._probe", lambda path: (1080, 1920, 10.0, 1))
    monkeypatch.setattr("branding_runtime._artifact_qc", lambda *args, **kwargs: (True, "ok"))

    result = apply_branded_finish(_Bot(tmp_path), str(video))

    assert result == str(video)
    assert len(commands) == 1
    command = commands[0]
    assert "-i" in command
    assert str(logo) in command
    assert "-map" in command
    assert "0:a?" in command
