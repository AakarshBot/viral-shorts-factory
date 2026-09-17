"""Authoritative runtime binding guard for the legacy Shorts factory.

The legacy factory contains several historical implementations of the same
capabilities. This module makes the live bot instance and the run_robot() global
namespace point at the current authoritative implementations every time the
runtime is rebound.
"""
from __future__ import annotations

import inspect

RUNTIME_HARDENER_VERSION = "2026-09-17-v2"


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


def validate_runtime_contracts(bot) -> list[str]:
    """Return signature/binding errors that commonly break patched legacy calls.

    This is intentionally a local contract check rather than another runtime
    patch. It catches incompatible monkey-patched signatures before a production
    run reaches the renderer.
    """
    errors = []
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if not isinstance(namespace, dict):
        return ["run_robot.__globals__ is unavailable"]

    def accepts_positionals(name, count):
        value = namespace.get(name)
        if not callable(value):
            errors.append(f"{name}: callable is missing")
            return
        try:
            signature = inspect.signature(value)
            positional = [
                parameter for parameter in signature.parameters.values()
                if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
            ]
            variadic = any(
                parameter.kind is parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )
            if not variadic and len(positional) < count:
                errors.append(f"{name}: accepts {len(positional)} positional args; expected at least {count}")
        except (TypeError, ValueError) as exc:
            errors.append(f"{name}: signature unavailable ({type(exc).__name__})")

    # These are the legacy call contracts used by factory_runtime. The hook
    # contract is especially important because it is patched by visual policy.
    accepts_positionals("render_hook_card", 7)
    accepts_positionals("create_branded_slide", 8)
    accepts_positionals("render_top5_card", 9)

    for name in ("write_script", "process_visuals_async", "generate_voiceover_and_timestamps"):
        value = getattr(bot, name, None)
        if not callable(value) or namespace.get(name) is not value:
            errors.append(f"{name}: bot/global binding is not authoritative")

    return errors
