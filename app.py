def _init_state() -> None:
    if "workflow_controller" not in st.session_state:
        st.session_state.workflow_controller = DashboardWorkflowController(ultimate_bot)
        try:
            if st.session_state.workflow_controller.restore_ready_upload():
                # The worker may have disappeared with the previous Python
                # process, but the database/artifact pair proves the run reached
                # the manual upload gate. Restore the dashboard to that state.
                st.session_state.production_started = True
                st.session_state.metadata_loaded_run_id = ""
                st.session_state.metadata_approved = False
                st.session_state.approved_metadata = {}
                st.session_state.metadata_editing = False
        except Exception as exc:
            print(f"[Dashboard] Ready-for-upload recovery unavailable: {type(exc).__name__}: {exc}", flush=True)

    defaults = {
        "candidates": [],
        "retained_topics": [],
        "web_config": {},
        "production_started": False,
        "upload_result": "",
        "upload_mode": "",
        "upload_notice": "",
        "upload_notice_kind": "",
        "confirm_public_upload": False,
        "candidate_page": 0,
        "selected_channel": _channel_options()[0],
        "last_demo_results": {},
        "offline_diagnostics": {},
        "pending_candidate": None,
        "visual_search_queries": "",
        "visual_query_story_key": "",
        "visual_query_suggestions": [],
        "visual_query_field_count": 0,
        "editorial_mode": "Deep Dive",
        "metadata_approved": False,
        "approved_metadata": {},
        "metadata_editing": False,
        "metadata_loaded_run_id": "",
        "metadata_pending_values": None,
        "workspace_mode": "Live",
        "live_format_selection": "",
        "live_topic_selection": "",
        "live_sports_selection": "",
        "live_cricket_scope": "",
        "language_label": next(iter(ultimate_bot.LANGUAGES.values()))["label"] if ultimate_bot.LANGUAGES else "English",
        "visual_pipeline_label": "Option 1 · Current image sourcing",
        "live_path_ready": False,
        "test_menu_selection": "Offline Diagnostics",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

