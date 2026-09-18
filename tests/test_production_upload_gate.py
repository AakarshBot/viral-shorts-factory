"""Regression coverage for the production upload safety boundary."""

from types import SimpleNamespace

from production_hardening_runtime import install_production_wrappers


def test_production_wrapper_blocks_run_robot_upload_but_preserves_manual_uploader():
    namespace = {"__builtins__": {}}
    exec(
        "def run_robot():\n"
        "    return upload_to_youtube()\n",
        namespace,
    )
    run_robot = namespace["run_robot"]

    real_uploader = lambda *args, **kwargs: "uploaded"
    controller = SimpleNamespace(
        bot=SimpleNamespace(run_robot=run_robot, upload_to_youtube=real_uploader),
        _patched=False,
        _real_uploader=None,
        _reporter=lambda *args: None,
    )

    install_production_wrappers(controller)

    assert controller._patched is True
    assert controller._real_uploader is real_uploader
    assert controller.bot.upload_to_youtube is real_uploader
    assert run_robot.__globals__["upload_to_youtube"]() == "PENDING_MANUAL_UPLOAD"
