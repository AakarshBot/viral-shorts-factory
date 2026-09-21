from pathlib import Path


def _live_navigation_source() -> str:
    app_source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    start = app_source.index("def render_live_navigation() -> Dict[str, Any]:")
    end = app_source.index("\ndef render_stage_progress", start)
    return app_source[start:end]


def test_live_navigation_is_incremental_pill_hierarchy():
    source = _live_navigation_source()

    assert "st.pills(" in source
    assert 'key="live_format_menu"' in source
    assert 'key="live_topic_menu"' in source
    assert 'key="live_sports_menu"' in source
    assert 'key="live_cricket_scope_menu"' in source
    assert "st.radio(" not in source


def test_live_navigation_labels_sports_as_a_separate_lane():
    source = _live_navigation_source()

    assert 'lane_label = "Sports lane" if live_format == "Sports" else "Topic lane"' in source
    assert '"Sports lane"' in source
    assert '"Topic lane"' in source


def test_live_navigation_reveals_sports_scope_only_after_sports_selection():
    source = _live_navigation_source()

    sports = source.index('key="live_sports_menu"')
    cricket_scope = source.index('key="live_cricket_scope_menu"')
    production_settings = source.index('with st.expander("Production settings"')

    assert sports < cricket_scope < production_settings
    assert 'if sports_mode == "Cricket":' in source
    assert 'elif sports_mode in {"Niche Sports", "AI"}:' in source


def test_live_navigation_does_not_require_the_large_css_surface():
    source = _live_navigation_source()

    assert "<style>" not in source
    assert "unsafe_allow_html" in source or "_render_section_header" in source

def test_live_navigation_shows_a_compact_three_level_visual_hierarchy():
    source = _live_navigation_source()

    format_step = source.index("01 · Format")
    topic_step = source.index("02 · Topic")
    sports_step = source.index("02 · Sports lane")
    scope_step = source.index("03 · Cricket scope")

    assert format_step < topic_step < scope_step
    assert format_step < sports_step < scope_step

def test_live_navigation_shows_selected_path_only_when_ready():
    source = _live_navigation_source()

    ready = source.index('if live_format and final_path_ready:')
    path_parts = source.index('path_parts = [live_format]')
    production = source.index('with st.expander("Production settings"')

    assert ready < path_parts < production
    assert '"Selected path"' in source
    assert 'path_text = " · ".join(part for part in path_parts if part)' in source

def test_dashboard_theme_uses_the_refreshed_cool_light_palette():
    theme_source = Path(__file__).resolve().parents[1].joinpath("dashboard_theme.py").read_text(encoding="utf-8")

    assert "--vsf-bg:#f2f5f4;" in theme_source
    assert "--vsf-surface:#ffffff;" in theme_source
    assert "--vsf-teal:#256b6d;" in theme_source
    assert "background:#e7efed!important;" in theme_source
    assert "background:linear-gradient(135deg,#ffffff,#eef5f4)!important;" in theme_source
