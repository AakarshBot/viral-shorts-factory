"""Bind per-channel branding configuration into the final render stage."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _branding_config(bot) -> dict[str, Any]:
    config = getattr(bot, "_active_web_config", {})
    if not isinstance(config, dict):
        return {}
    value = config.get("channel_branding") or config.get("branding") or {}
    return value if isinstance(value, dict) else {}


def _configured_asset(bot, key: str) -> Path | None:
    config = _branding_config(bot)
    direct = config.get(key)
    if direct:
        path = Path(str(direct)).expanduser()
        if not path.is_absolute():
            root = Path(getattr(bot, "BASE_DIR", Path.cwd()))
            path = root / path
        if path.exists() and path.is_file():
            return path

    assets_dir = config.get("brand_assets_dir") or config.get("assets_dir")
    if assets_dir:
        directory = Path(str(assets_dir)).expanduser()
        if not directory.is_absolute():
            directory = Path(getattr(bot, "BASE_DIR", Path.cwd())) / directory
        names = {
            "logo_path": ("logo.png", "logo.png.jpg", "channels4_profile.jpg", "channels4_profile.jpg.jpg"),
            "overlay_path": ("overlay.png",),
        }.get(key, ())
        for name in names:
            candidate = directory / name
            if candidate.exists() and candidate.is_file():
                return candidate
    return None


def install_channel_branding(bot) -> bool:
    """Make configured channel branding the source of truth for final rendering."""
    try:
        import branding_runtime
    except Exception:
        return False

    if getattr(branding_runtime, "_channel_branding_bound", False):
        return True

    original_assets = branding_runtime._assets

    def channel_assets(target_bot):
        logo = _configured_asset(target_bot, "logo_path")
        overlay = _configured_asset(target_bot, "overlay_path")
        if logo is None and overlay is None:
            return original_assets(target_bot)
        default_logo, default_overlay = original_assets(target_bot)
        return logo or default_logo, overlay or default_overlay

    branding_runtime._assets = channel_assets
    branding_runtime._channel_branding_bound = True
    return True


__all__ = ["install_channel_branding"]
