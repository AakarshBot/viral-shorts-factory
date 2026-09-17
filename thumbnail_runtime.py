"""Disable the legacy automatic thumbnail extraction step.

The factory already renders the finished Short, so the operator can choose
any frame from that video as the YouTube thumbnail.  No separate thumbnail
image needs to be generated, saved, or uploaded by the factory.
"""
from __future__ import annotations


def install() -> None:
    import ultimate_bot

    if getattr(ultimate_bot, "_thumbnail_generation_disabled", False):
        return

    def disabled_thumbnail_generation(_video_path):
        return None

    # The legacy validation path calls this global after rendering. Replacing
    # it here keeps the production path unchanged while making that call a
    # zero-cost no-op. No image is created and no API is involved.
    ultimate_bot.generate_thumbnail_frame = disabled_thumbnail_generation
    ultimate_bot._thumbnail_generation_disabled = True
