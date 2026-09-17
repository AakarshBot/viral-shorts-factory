"""Authoritative runtime binding guard for the legacy Shorts factory.

The legacy factory contains several historical implementations of the same
capabilities. This module makes the live bot instance and the run_robot() global
namespace point at the current authoritative implementations every time the
runtime is rebound.
"""
from __future__ import annotations

import inspect

RUNTIME_HARDENER_VERSION = "2026-09-17-v4"


def _install_unicode_runtime() -> None:
    """Install multilingual text contracts before any production validation runs."""
    try:
        from unicode_runtime import install
        install()
    except Exception as exc:
        print(
            f"   [Runtime Hardener] Unicode runtime unavailable: {type(exc).__name__}: {exc}",
            flush=True,
        )


def reassert_live_bindings(bot) -> None:
    """Reassert authoritative non-editorial bindings after legacy patching.

    This is intentionally idempotent. It does not capture stale callables; it
    resolves the authoritative implementation at rebind time and then writes
    the same callable to both the bot and the legacy run_robot globals.
    """
    _install_unicode_runtime()
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


def _count_positional_parameters(value) -> tuple[int, bool]:
    """Return (positional_parameter_count, accepts_varargs) for a callable."""
    signature = inspect.signature(value)
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    variadic = any(
        parameter.kind is parameter.VAR_POSITIONAL
        for parameter in signature.parameters.values()
    )
    return len(positional), variadic


def _validate_signature(errors, name: str, value, minimum: int, label: str) -> None:
    if not callable(value):
        errors.append(f"{label}: callable is missing")
        return
    try:
        count, variadic = _count_positional_parameters(value)
        if not variadic and count < minimum:
            errors.append(
                f"{label}: accepts {count} positional args; expected at least {minimum}"
            )
    except (TypeError, ValueError) as exc:
        errors.append(f"{label}: signature unavailable ({type(exc).__name__})")


def validate_runtime_contracts(bot) -> list[str]:
    """Return signature/binding errors that commonly break patched legacy calls.

    There are two deliberate renderer layers in this factory:

    * ``factory_runtime`` contains the full authoritative renderer contracts.
    * ``run_robot.__globals__`` may contain compatibility wrappers that inject
      ``bot`` or other context before calling those renderers.

    The previous validator incorrectly required wrapper functions to expose the
    full implementation signatures, making valid wrappers look broken. We now
    validate each layer against its own contract.
    """
    _install_unicode_runtime()
    errors = []
    run_robot = getattr(bot, "run_robot", None)
    namespace = getattr(run_robot, "__globals__", None)
    if not isinstance(namespace, dict):
        return ["run_robot.__globals__ is unavailable"]

    # Compatibility wrappers used by the live run_robot namespace. These are
    # intentionally smaller because they inject context before delegating.
    wrapper_contracts = {
        "render_hook_card": 5,
        "create_branded_slide": 6,
        "render_top5_card": 7,
    }
    for name, minimum in wrapper_contracts.items():
        _validate_signature(errors, name, namespace.get(name), minimum, name)

    # Full implementation contracts are what must remain stable across patches.
    try:
        import factory_runtime

        authoritative_contracts = {
            "render_hook_card": 7,
            "create_branded_slide": 8,
            "render_top5_card": 9,
        }
        for name, minimum in authoritative_contracts.items():
            _validate_signature(
                errors,
                name,
                getattr(factory_runtime, name, None),
                minimum,
                f"factory_runtime.{name}",
            )
    except Exception as exc:
        errors.append(f"factory_runtime renderer contracts unavailable ({type(exc).__name__})")

    for name in ("write_script", "process_visuals_async", "generate_voiceover_and_timestamps"):
        value = getattr(bot, name, None)
        if not callable(value) or namespace.get(name) is not value:
            errors.append(f"{name}: bot/global binding is not authoritative")

    return errors
