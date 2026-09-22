from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_production_lock_blocks_a_second_live_run():
    import workflow_runtime

    workflow_runtime._PROCESS_PRODUCTION_LOCK.acquire()
    try:
        controller = workflow_runtime.WorkflowController(SimpleNamespace())
        story = {
            "title": "Test story",
            "story_key": "test-story",
            "discovery_rank": 1,
        }
        with pytest.raises(RuntimeError, match="Another production run is already active"):
            controller.start_production({}, story)
    finally:
        workflow_runtime._PROCESS_PRODUCTION_LOCK.release()


def test_run_id_includes_microseconds_to_avoid_same_second_collisions():
    source = (REPO_ROOT / "workflow_runtime.py").read_text(encoding="utf-8")
    start = source.index("def start_production(")
    end = source.index("
def _validate_selected_story", start)
    block = source[start:end]
    assert 'strftime(\n                    "run-%Y%m%d-%H%M%S-%f"' in block


def test_core_vault_writes_are_pinned_to_the_created_row():
    source = (REPO_ROOT / "ultimate_bot.py").read_text(encoding="utf-8")
    start = source.index("def run_robot(")
    end = source.index("\nif __name__ == "__main__":", start)
    block = source[start:end]
    assert '"INSERT OR IGNORE INTO vault "' in block
    assert "insert_cursor = conn.execute(" in block
    assert "run_row_id = getattr(insert_cursor, "lastrowid", None)" in block
    assert 'WHERE rowid=?' in block
    assert 'WHERE topic=?' not in block


def test_dashboard_upload_actions_use_stable_approved_metadata():
    source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    start = source.index("def render_upload_panel(")
    end = source.index("\ndef _perform_upload(", start)
    panel = source[start:end]
    upload_start = panel.index('st.caption("Public asks')
    upload_block = panel[upload_start:]
    for key in ("final_title", "final_description", "final_comment"):
        assert f'st.session_state["{key}"]' not in upload_block
    for key in ("title", "description", "comment"):
        assert f'approved_metadata.get("{key}")' in upload_block


def test_dashboard_does_not_mutate_metadata_widget_keys_after_instantiation():
    source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    start = source.index("def render_upload_panel(")
    end = source.index("\ndef _perform_upload(", start)
    panel = source[start:end]
    for key in ("final_title", "final_description", "final_comment"):
        widget_pos = panel.index(f'key="{key}"')
        assert f'st.session_state["{key}"] =' not in panel[widget_pos:]


def test_canonical_runtime_bindings_prevent_wrapper_reintroduction():
    source = (REPO_ROOT / "runtime_bindings.py").read_text(encoding="utf-8")
    assert '_vsf_canonical_runtime_bindings' in source
    assert 'canonical.get(name) or getattr(bot, name, None)' in source


def test_production_wrappers_resolve_canonical_runner_and_callables():
    source = (REPO_ROOT / "production_hardening_runtime.py").read_text(encoding="utf-8")
    assert '_vsf_canonical_run_robot' in source
    assert '_vsf_canonical_runtime_bindings' in source
    assert 'canonical.get("write_script") or globals_dict.get("write_script")' in source
    assert 'canonical.get("process_visuals_async") or globals_dict.get("process_visuals_async")' in source


def test_dashboard_wrappers_resolve_canonical_runner():
    source = (REPO_ROOT / "dashboard_runtime.py").read_text(encoding="utf-8")
    assert '_vsf_canonical_run_robot' in source


def test_exact_identity_wrapper_preserves_canonical_runner():
    source = (REPO_ROOT / "final_qc_runtime.py").read_text(encoding="utf-8")
    assert '_vsf_canonical_run_robot' in source
    assert 'exact_identity_runner._canonical_run_robot' in source

def test_public_publish_block_is_enforced_before_upload():
    source = (REPO_ROOT / "workflow_runtime.py").read_text(encoding="utf-8")
    start = source.index("    def upload_manual(")
    end = source.index("\ndef _validate_selected_story", start)
    block = source[start:end]
    assert 'bool((script_data or {}).get("public_publish_blocked"))' in block
    assert "publish_mode" in block
    assert "marked it private-only" in block


def test_legacy_creator_comment_uploader_is_only_a_compatibility_shim():
    source = (REPO_ROOT / "youtube_comment_runtime.py").read_text(encoding="utf-8")
    start = source.index("def patch_youtube_upload(")
    block = source[start:]
    assert "return getattr(bot, "upload_to_youtube", None)" in block
    assert "videos().insert" not in block
    assert "_creator_comment_wrapped" not in block
