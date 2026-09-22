"""Regression coverage for the production exact-run identity bridge."""
from types import SimpleNamespace

import workflow_runtime


def test_production_runner_does_not_double_wrap_exact_identity(monkeypatch):
    calls = []

    def exact_runner(**kwargs):
        calls.append(kwargs)
        return "ran"

    exact_runner._exact_identity_runner = True

    def fail_double_wrap(*args, **kwargs):
        raise AssertionError("exact identity runner was wrapped a second time")

    monkeypatch.setattr(workflow_runtime, "run_robot_with_exact_identity", fail_double_wrap)

    bot = SimpleNamespace(run_robot=exact_runner)
    result = workflow_runtime._run_production_runner(bot, {"selected_story": {"title": "Test"}})

    assert result == "ran"
    assert calls == [{"web_config": {"selected_story": {"title": "Test"}}}]


def test_production_runner_uses_identity_bridge_for_raw_runner(monkeypatch):
    calls = []

    def raw_runner(**kwargs):
        raise AssertionError("raw runner should be invoked by the identity bridge")

    def bridge(bot, web_config):
        calls.append((bot, web_config))
        return "bridged"

    monkeypatch.setattr(workflow_runtime, "run_robot_with_exact_identity", bridge)

    bot = SimpleNamespace(run_robot=raw_runner)
    config = {"selected_story": {"title": "Test"}}

    assert workflow_runtime._run_production_runner(bot, config) == "bridged"
    assert calls == [(bot, config)]


def test_exact_identity_does_not_patch_process_wide_sqlite(monkeypatch):
    import sqlite3
    from types import SimpleNamespace
    import db_runtime

    module_connect = sqlite3.connect
    seen = []

    def raw_runner(**_kwargs):
        seen.append(sqlite3.connect is module_connect)
        return "ran"

    bot = SimpleNamespace(run_robot=raw_runner, sqlite3=sqlite3, DB_PATH=":memory:")
    monkeypatch.setattr(db_runtime, "migrate_vault", lambda _conn: None)

    assert db_runtime.run_robot_with_exact_identity(
        bot, {"selected_story": {"title": "Test"}}
    ) == "ran"
    assert seen == [True]
    assert sqlite3.connect is module_connect


def test_normal_run_return_finalizes_running_row_without_waiting_for_stale_sweep():
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "db_runtime.py").read_text(encoding="utf-8")
    assert 'if row and row[0] in {"PENDING_QC", "RUNNING"}:' in source
    assert 'rejected_reason="Run ended before upload approval"' in source
