    current = st.session_state.get("workspace_mode", "Live")
    if current not in options:
        current = "Live"

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


def _reset_live_navigation() -> None:
    """Return the Live selector to its first decision without touching production state."""
    for key in (
        "live_format_selection",
        "live_topic_selection",
        "live_sports_selection",
        "live_cricket_scope",
        "live_format_menu",
        "live_topic_menu",
        "live_sports_menu",
        "live_cricket_scope_menu",
    ):
        st.session_state[key] = None if key.endswith("_menu") else ""
    st.session_state["live_path_ready"] = False
    _clear_live_run_selection()


def render_live_navigation() -> Dict[str, Any]:
    """Render Live choices with staged disclosure: only the next decision stays expanded."""
    st.session_state["live_path_ready"] = False
    _render_section_header(
        "Live",
        "Build a Short",
        "Choose one decision at a time. Completed choices collapse into the path.",
    )

    live_format = str(st.session_state.get("live_format_selection") or "").strip()
    topic_label = str(st.session_state.get("live_topic_selection") or "").strip()
    sports_mode = str(st.session_state.get("live_sports_selection") or "").strip()
    cricket_scope = str(st.session_state.get("live_cricket_scope") or "").strip()

    if not live_format:
        st.markdown(
            "<div class='choice-kicker'>01 · Format</div>",
            unsafe_allow_html=True,
        )
        format_choice = st.pills(
            "Live format",
            ["Deep Dive", "Top 5", "Sports"],
            selection_mode="single",
            default=None,
            key="live_format_menu",
            required=False,
            label_visibility="collapsed",
            width="stretch",
        )
        if format_choice:
            st.session_state.live_format_selection = format_choice
            _clear_live_downstream()
            _clear_live_run_selection()
            st.rerun()
        st.caption("Start with the format.")
        return build_config()

    path_parts = [live_format]

    if live_format in {"Deep Dive", "Top 5"}:
        path_parts.append(topic_label)
        format_mode = "top5" if live_format == "Top 5" else "regular"
        options = category_options(format_mode, live_format)
        if not topic_label:
            path_choice_cols = st.columns([6, 1], gap="small")
            with path_choice_cols[0]:
                st.markdown(
                    "<div class='path-summary'><span class='path-check'>✓</span><span class='path-label'>" + _ui_html(live_format) + "</span><span class='path-summary-copy'>Format selected</span></div>",
                    unsafe_allow_html=True,
                )
            with path_choice_cols[1]:
                if st.button("Change", key="change_live_format_topic", width="stretch"):
                    _reset_live_navigation()
                    st.rerun()
            st.markdown(
                "<div class='choice-kicker'>02 · Topic</div>",
                unsafe_allow_html=True,
            )
            topic_choice = st.pills(
                "Topic",
                list(options.keys()),
                selection_mode="single",
                default=None,
                key="live_topic_menu",
                label_visibility="collapsed",
                width="stretch",
                wrap=True,
            )
            if topic_choice:
                st.session_state.live_topic_selection = topic_choice
                _clear_live_run_selection()
                st.rerun()

    elif live_format == "Sports":
        if not sports_mode:
            st.markdown(
                "<div class='choice-kicker'>01 · Format</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<div class='path-summary'><div><span class='path-check'>✓</span><span class='path-label'>{_ui_html(live_format)}</span></div></div>",
                unsafe_allow_html=True,
            )
            path_controls = st.columns([6, 1], gap="small")
            with path_controls[0]:
                st.caption("Sports selected · choose the sports lane below.")
            with path_controls[1]:
                if st.button("Change", key="change_live_format_sports", width="stretch"):
                    _reset_live_navigation()
                    st.rerun()
            st.markdown(
                "<div class='choice-kicker'>02 · Sports lane</div>",
                unsafe_allow_html=True,
            )
            sports_choice = st.pills(
                "Sports mode",
                ["Cricket", "Niche Sports", "AI"],
                selection_mode="single",
                default=None,
                key="live_sports_menu",
                label_visibility="collapsed",
                width="stretch",
            )
            if sports_choice:
                st.session_state.live_sports_selection = sports_choice
                st.session_state.live_cricket_scope = ""
                st.session_state.live_cricket_scope_menu = None
                _clear_live_run_selection()
                st.rerun()
        else:
            path_parts.append(sports_mode)
            if sports_mode == "Cricket":
                if not cricket_scope:
                    scope_controls = st.columns([6, 1], gap="small")
                    with scope_controls[0]:
                        st.markdown(
                            "<div class='choice-kicker'>03 · Cricket scope</div>",
                            unsafe_allow_html=True,
                        )
                    with scope_controls[1]:
                        if st.button("Change", key="change_live_cricket", width="stretch"):
                            st.session_state.live_sports_selection = ""
                            st.session_state.live_cricket_scope = ""
                            st.session_state.live_sports_menu = None
                            st.session_state.live_cricket_scope_menu = None
                            _clear_live_run_selection()
                            st.rerun()
                    cricket_choice = st.pills(
                        "Cricket scope",
                        ["India / Asia", "Global"],
                        selection_mode="single",
                        default=None,
                        key="live_cricket_scope_menu",
                        label_visibility="collapsed",
                        width="stretch",
                    )
                    if cricket_choice:
                        st.session_state.live_cricket_scope = cricket_choice
                        _clear_live_run_selection()
                        st.rerun()
                else:
                    path_parts.append(cricket_scope)

    final_path_ready = (
        bool(topic_label) if live_format in {"Deep Dive", "Top 5"} else
        bool(sports_mode) and (sports_mode != "Cricket" or bool(cricket_scope))
    )
    if final_path_ready:
        st.session_state["live_path_ready"] = True
        st.markdown(
            f"<div class='path-ready'>"
            f"<span class='path-ready-dot'>✓</span>"
            f"<span class='path-ready-copy'><b>Path ready</b><span class='path-ready-value'>{' · '.join(_ui_html(part) for part in path_parts if part)}</span></span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button("Change path", key="change_live_path_ready", width="content"):
            _reset_live_navigation()
            st.rerun()

        with st.popover("⚙ Settings", width="stretch"):
            st.caption("Optional production settings")
            columns = st.columns(3, gap="medium")
            language_options = {cfg["label"]: key for key, cfg in ultimate_bot.LANGUAGES.items()}
            language_labels = list(language_options.keys())
            current_language = st.session_state.get("language_label") or (language_labels[0] if language_labels else "English")
            if current_language not in language_labels and language_labels:
                current_language = language_labels[0]