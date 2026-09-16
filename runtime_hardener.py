"""Authoritative runtime binding guard for the legacy Shorts factory.

The legacy factory contains several historical implementations of the same
capabilities. This module makes the live bot instance and the run_robot() global
namespace point at the current authoritative implementations every time the
runtime is rebound.
"""
from __future__ import annotations

RUNTIME_HARDENER_VERSION = "2026-09-16-v1"


def reassert_live_bindings(bot) -> None:
    """Reassert authoritative non-editorial bindings after legacy patching.

    This is intentionally idempotent. It does not capture stale callables; it
    resolves the authoritative implementation at rebind time and then writes
    the same callable to both the bot and the legacy run_robot globals.
    """
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)

    try:
        from autopilot_runtime import select_auto_pilot

        def authoritative_auto_pilot(conn):
            return select_auto_pilot(bot, conn)

        authoritative_auto_pilot._authoritative_autopilot = True
        bot.auto_pilot_selection = authoritative_auto_pilot
        if isinstance(namespace, dict):
            namespace["auto_pilot_selection"] = authoritative_auto_pilot
        print(
            "   [Runtime Hardener] Auto-Pilot bound to autopilot_runtime.select_auto_pilot.",
            flush=True,
        )
    except Exception as exc:
        print(
            f"   [Runtime Hardener] Auto-Pilot authoritative binding unavailable: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

    bot._runtime_hardener_version = RUNTIME_HARDENER_VERSION


def assert_authoritative_binding(bot, name: str) -> bool:
    """Return True when the bot and legacy namespace resolve to the same callable."""
    value = getattr(bot, name, None)
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if not callable(value) or not isinstance(namespace, dict):
        return False
    return namespace.get(name) is value
