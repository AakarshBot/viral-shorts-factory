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
    end = source.index("\ndef _validate_selected_story", start)
    block = source[start:end]
    assert 'strftime(\n                    "run-%Y%m%d-%H%M%S-%f"' in block


def test_core_vault_writes_are_pinned_to_the_created_row():
    source = (REPO_ROOT / "ultimate_bot.py").read_text(encoding="utf-8")
    start = source.index("def run_robot(")
    end = source.index("\nif __name__ == \"__main__\":", start)
    block = source[start:end]
    assert '"INSERT OR IGNORE INTO vault "' in block
    assert "insert_cursor = conn.execute(" in block
    assert 'run_row_id = getattr(insert_cursor, "lastrowid", None)' in block
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
    assert 'return getattr(bot, "upload_to_youtube", None)' in block
    assert "videos().insert" not in block
    assert "_creator_comment_wrapped" not in block

def test_core_init_db_delegates_to_canonical_schema():
    source = (REPO_ROOT / "ultimate_bot.py").read_text(encoding="utf-8")
    start = source.index("def init_db(")
    end = source.index("\ndef safe_text(", start)
    block = source[start:end]
    assert "from db_architecture import migrate_vault" in block
    assert "topic TEXT PRIMARY KEY" not in block


def test_manual_pool_defaults_to_ten_total_images():
    source = (REPO_ROOT / "visual_retrieval_runtime.py").read_text(encoding="utf-8")
    assert 'VISUAL_MANUAL_POOL_TARGET", "10"' in source
    start = source.index("def collect_manual_visual_pool(")
    end = source.index("\ndef collect_manual_visual_search(", start)
    block = source[start:end]
    assert "requested_max = min(MANUAL_POOL_TARGET, default_max or MANUAL_POOL_TARGET)" in block


def test_manual_pool_provider_fetches_are_parallelized():
    source = (REPO_ROOT / "visual_retrieval_runtime.py").read_text(encoding="utf-8")
    start = source.index("def collect_manual_visual_pool(")
    end = source.index("\ndef collect_manual_visual_search(", start)
    block = source[start:end]
    assert "ThreadPoolExecutor(" in block
    assert "thread_name_prefix=\"manual-visual-pool\"" in block


def test_first_manual_query_is_preferred_for_first_slide():
    source = (REPO_ROOT / "visual_retrieval_runtime.py").read_text(encoding="utf-8")
    start = source.index("def select_manual_visual_candidate(")
    end = source.index("\ndef materialize_manual_visual_pool(", start)
    block = source[start:end]
    assert '"slide_index") or 0' in block
    assert 'asset.get("manual_query_index")' in block


def test_repeated_manual_searches_advance_provider_pages():
    source = (REPO_ROOT / "visual_retrieval_runtime.py").read_text(encoding="utf-8")
    start = source.index("def collect_manual_visual_search(")
    end = source.index("\ndef collect_manual_visual_options(", start)
    block = source[start:end]
    assert "search_round: int = 1" in block
    assert "effective_page = (max(1, int(search_round)) - 1) * MANUAL_SEARCH_MAX_PAGES + page" in block
    assert "effective_page," in block

def test_global_manual_search_pagination_is_scoped_per_query():
    source = (REPO_ROOT / "dashboard_runtime.py").read_text(encoding="utf-8")
    start = source.index("    def search_visual_pool(")
    end = source.index("\n    def assign_visual_pool_asset(", start)
    block = source[start:end]
    assert 'if str(group.get("query") or "").strip().casefold()' in block
    assert "search_round=(" in block



def test_dashboard_run_id_is_passed_into_exact_identity_bridge():
    workflow = (REPO_ROOT / "workflow_runtime.py").read_text(encoding="utf-8")
    db_runtime = (REPO_ROOT / "db_runtime.py").read_text(encoding="utf-8")
    assert 'config["run_id"] = run_id' in workflow
    assert 'requested_run_id = None' in db_runtime
    assert 'state = _IdentityState(run_id=requested_run_id)' in db_runtime


def test_visual_qa_budget_and_failure_state_are_execution_local():
    source = (REPO_ROOT / "visual_qa_runtime.py").read_text(encoding="utf-8")
    assert '_QA_STATE = threading.local()' in source
    assert 'def get_last_visual_qa_failure()' in source
    assert 'global _VIDEO_CALLS' not in source
    assert 'global _SCENE_CALLS' not in source
    assert 'LAST_VISUAL_QA_FAILURE' not in source


def test_production_uses_a_run_scoped_workspace_instead_of_wiping_shared_output():
    source = (REPO_ROOT / "ultimate_bot.py").read_text(encoding="utf-8")
    start = source.index("def run_robot(")
    end = source.index("\nif __name__ == \"__main__\":", start)
    block = source[start:end]
    assert 'global ASSETS_DIR' in block
    assert 'ASSETS_DIR = os.path.join(BASE_DIR, "output", workspace_id)' in block
    assert 'safe_cleanup(ASSETS_DIR)' not in block


def test_new_production_is_blocked_while_previous_run_awaits_upload():
    source = (REPO_ROOT / "workflow_runtime.py").read_text(encoding="utf-8")
    start = source.index("    def start_production(")
    end = source.index("\n    def upload_manual(", start)
    block = source[start:end]
    assert 'self.state.stage == "qc"' in block
    assert "not self.state.uploaded_video_id" in block
    assert "ready for upload" in block.lower()


def test_fresh_vault_contains_visual_rights_ledger_column():
    source = (REPO_ROOT / "db_architecture.py").read_text(encoding="utf-8")
    assert '"asset_credits_json", "TEXT"' in source
    assert "asset_credits_json TEXT" in source
    assert 'def _add_column(conn, "asset_credits_json", "TEXT")' in source


def test_script_is_persisted_before_expensive_media_pipeline():
    source = (REPO_ROOT / "ultimate_bot.py").read_text(encoding="utf-8")
    start = source.index("def run_robot(")
    end = source.index("\nif __name__ == \"__main__\":", start)
    block = source[start:end]
    persist_pos = block.index("UPDATE vault SET script_json")
    pipeline_pos = block.index("Starting Asset Generation & Rendering Pipeline")
    assert persist_pos < pipeline_pos


def test_ready_upload_recovery_reconstructs_upload_state():
    source = (REPO_ROOT / "workflow_runtime.py").read_text(encoding="utf-8")
    start = source.index("    def restore_ready_upload(")
    end = source.index("    def _worker_started", start)
    block = source[start:end]
    for needle in (
        "status = 'READY_FOR_UPLOAD'",
        "script_json IS NOT NULL",
        "final_video_output.mp4",
        "self.state.script_data = dict(script_data)",
        "self.bot._last_run_row_id = int(row_id)",
    ):
        assert needle in block


def test_dashboard_initialization_restores_ready_upload_state():
    source = (REPO_ROOT / "app.py").read_text(encoding="utf-8")
    start = source.index("def _init_state()")
    end = source.index("\ndef _topic_identity", start)
    block = source[start:end]
    assert "restore_ready_upload()" in block
    assert "st.session_state.production_started = True" in block
    assert "metadata_approved = False" in block


def test_locked_dashboard_story_skips_redundant_analytics_sweep():
    source = (REPO_ROOT / "ultimate_bot.py").read_text(encoding="utf-8")
    start = source.index("def run_robot(")
    end = source.index("\nif __name__ == \"__main__\":", start)
    block = source[start:end]
    marker = 'locked_story = bool('
    skip = 'should_sync = not locked_story'
    assert marker in block
    assert skip in block
    assert "Analytics sync skipped: dashboard story is already locked" in block


def test_active_production_stages_are_distinct_from_stale_qc():
    source = (REPO_ROOT / "ultimate_bot.py").read_text(encoding="utf-8")
    start = source.index("def run_robot(")
    end = source.index("\nif __name__ == \"__main__\":", start)
    block = source[start:end]
    assert "status='RUNNING'" in block
    assert "status='WAITING_SCRIPT_REVIEW'" in block
    assert "status='WAITING_VISUAL_REVIEW'" in block
    assert "WHERE status = 'RUNNING' AND date_used < ?" in block
    assert "WHERE video_id = 'PENDING_QC' AND date_used < ?" not in block
