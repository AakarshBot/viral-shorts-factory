    st.sidebar.markdown("<div class='sidebar-kicker'>Workspace</div>", unsafe_allow_html=True)
    selected = st.sidebar.pills(
        "Workspace",
        options,
        selection_mode="single",
        default=current,
        key="workspace_mode",
        label_visibility="collapsed",
        width="stretch",
    )
    return selected or current


def render_live_navigation() -> Dict[str, Any]:
    """Render the deliberate Live hierarchy and return the production config."""
    st.session_state["live_path_ready"] = False
    _render_section_header(
        "Live",
        "Build a Short",
        "Choose a format, then a topic lane.",
    )

    st.markdown(
        "<div style='color:var(--muted-2);font-size:.62rem;font-weight:900;letter-spacing:.13em;text-transform:uppercase;margin:2px 0 7px'>01 · Format</div>",
        unsafe_allow_html=True,
    )
    format_choice = st.pills(
        "Live format",
        ["Deep Dive", "Top 5", "Sports"],
        selection_mode="single",
        default=st.session_state.get("live_format_selection") or None,
        key="live_format_menu",
        required=False,
        label_visibility="collapsed",
        width="stretch",
    )
    previous_format = st.session_state.get("live_format_selection") or ""
    if format_choice and format_choice != previous_format:
        st.session_state.live_format_selection = format_choice
        _clear_live_downstream()
        _clear_live_run_selection()

    live_format = st.session_state.get("live_format_selection") or ""
    if not live_format:
        st.caption("Choose a format to continue.")
        return build_config()

    lane_label = "Sports lane" if live_format == "Sports" else "Topic lane"
    st.markdown(
        f"<div style='color:var(--muted-2);font-size:.66rem;font-weight:850;letter-spacing:.12em;text-transform:uppercase;margin:14px 0 7px'>{lane_label}</div>",
        unsafe_allow_html=True,
    )

    final_path_ready = False
    if live_format in {"Deep Dive", "Top 5"}:
        format_mode = "top5" if live_format == "Top 5" else "regular"
        options = category_options(format_mode, live_format)
        st.markdown(
            "<div style='color:var(--muted-2);font-size:.62rem;font-weight:900;letter-spacing:.13em;text-transform:uppercase;margin:14px 0 7px'>02 · Topic</div>",
            unsafe_allow_html=True,
        )
        topic_choice = st.pills(
            "Topic",
            list(options.keys()),
            selection_mode="single",
            default=st.session_state.get("live_topic_selection") or None,
            key="live_topic_menu",
            label_visibility="collapsed",
            width="stretch",
            wrap=True,
        )
        previous_topic = st.session_state.get("live_topic_selection") or ""
        if topic_choice and topic_choice != previous_topic:
            st.session_state.live_topic_selection = topic_choice
            _clear_live_run_selection()
        final_path_ready = bool(st.session_state.get("live_topic_selection"))
        st.session_state["live_path_ready"] = final_path_ready

    elif live_format == "Sports":
        st.markdown(
            "<div style='color:var(--muted-2);font-size:.62rem;font-weight:900;letter-spacing:.13em;text-transform:uppercase;margin:14px 0 7px'>02 · Sports lane</div>",
            unsafe_allow_html=True,
        )
        sports_choice = st.pills(
            "Sports mode",
            ["Cricket", "Niche Sports", "AI"],
            selection_mode="single",
            default=st.session_state.get("live_sports_selection") or None,
            key="live_sports_menu",
            label_visibility="collapsed",
            width="stretch",
        )
        previous_sports = st.session_state.get("live_sports_selection") or ""
        if sports_choice and sports_choice != previous_sports:
            st.session_state.live_sports_selection = sports_choice
            st.session_state.live_cricket_scope = ""
            st.session_state.live_cricket_scope_menu = None
            _clear_live_run_selection()

        sports_mode = st.session_state.get("live_sports_selection") or ""
        if sports_mode == "Cricket":
            st.markdown(
                "<div style='color:var(--muted-2);font-size:.62rem;font-weight:900;letter-spacing:.13em;text-transform:uppercase;margin:14px 0 7px'>03 · Cricket scope</div>",
                unsafe_allow_html=True,
            )
            cricket_choice = st.pills(
                "Cricket scope",
                ["India / Asia", "Global"],
                selection_mode="single",
                default=st.session_state.get("live_cricket_scope") or None,
                key="live_cricket_scope_menu",
                label_visibility="collapsed",
                width="stretch",
            )
            if cricket_choice and cricket_choice != (st.session_state.get("live_cricket_scope") or ""):
                st.session_state.live_cricket_scope = cricket_choice
                _clear_live_run_selection()
            final_path_ready = bool(st.session_state.get("live_cricket_scope"))
            st.session_state["live_path_ready"] = final_path_ready
        elif sports_mode in {"Niche Sports", "AI"}:
            final_path_ready = True
            st.session_state["live_path_ready"] = True

    if not final_path_ready:
        st.caption("Choose a topic lane to continue.")
        return build_config()
