from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _upload_panel_source() -> str:
    source = APP_PATH.read_text(encoding="utf-8")
    start = source.index("def render_upload_panel(")
    end = source.index("\ndef _perform_upload(", start)
    return source[start:end]


def test_upload_panel_uses_stable_approved_metadata_not_widget_state():
    source = _upload_panel_source()

    # Streamlit may remove a widget-owned key when that widget is no longer
    # rendered. Upload actions therefore must never depend on direct reads of
    # final_title/final_description/final_comment from session_state.
    assert 'st.session_state["final_title"]' not in source[source.index('st.caption("Public asks'):]
    assert 'st.session_state["final_description"]' not in source[source.index('st.caption("Public asks'):]
    assert 'st.session_state["final_comment"]' not in source[source.index('st.caption("Public asks'):]

    assert 'approved_metadata.get("title")' in source
    assert 'approved_metadata.get("description")' in source
    assert 'approved_metadata.get("comment")' in source


def test_metadata_approval_persists_a_non_widget_payload():
    source = _upload_panel_source()
    assert '"approved_metadata"' in source
    assert 'st.session_state["approved_metadata"] = {' in source


def test_edit_metadata_reopens_fields_for_reapproval():
    source = _upload_panel_source()
    marker = 'if st.button("Edit metadata", width="stretch", key="edit_metadata"):'
    start = source.index(marker)
    end = source.index('else:', start)
    edit_block = source[start:end]
    assert 'st.session_state["metadata_approved"] = False' in edit_block
    assert 'st.session_state["metadata_editing"] = True' in edit_block
