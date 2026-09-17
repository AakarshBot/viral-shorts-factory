from __future__ import annotations

import runpy

import streamlit as st
import workflow_runtime

PAGE_SIZE = 3
MAX_CANDIDATES = workflow_runtime.MAX_DISCOVERY_CANDIDATES


class _CandidatePage(list):
    def __init__(self, items, page: int, total: int):
        super().__init__(items)
        self.page = page
        self.total = min(total, MAX_CANDIDATES)

    def __iter__(self):
        yield from super().__iter__()
        start = self.page * PAGE_SIZE
        end = min(start + len(self), self.total)
        st.caption(f"Showing candidates {start + 1}–{end} of {self.total}")

        next_start = start + PAGE_SIZE
        if next_start < self.total:
            remaining = min(PAGE_SIZE, self.total - next_start)
            if st.button(
                f"Show next {remaining} stories ({next_start + 1}–{next_start + remaining})",
                key="candidate_next_page",
                use_container_width=True,
            ):
                st.session_state.candidate_page = self.page + 1
                st.rerun()
        elif start >= PAGE_SIZE:
            if st.button(
                "Show previous 3 stories",
                key="candidate_previous_page",
                use_container_width=True,
            ):
                st.session_state.candidate_page = max(0, self.page - 1)
                st.rerun()


class _CandidatePool(list):
    """Keep the legacy dashboard's three-column layout while paging a 12-story pool."""

    def __getitem__(self, key):
        if isinstance(key, slice) and key.start in (None, 0) and key.stop == PAGE_SIZE and key.step is None:
            total = min(len(self), MAX_CANDIDATES)
            if total <= 0:
                return _CandidatePage([], 0, 0)
            page_count = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
            page = int(st.session_state.get("candidate_page", 0) or 0)
            page = max(0, min(page, page_count - 1))
            start = page * PAGE_SIZE
            end = min(start + PAGE_SIZE, total)
            return _CandidatePage(list.__getitem__(self, slice(start, end)), page, total)
        return super().__getitem__(key)


_original_discover = workflow_runtime.discover_three_candidates


def _paged_discover(*args, **kwargs):
    st.session_state.candidate_page = 0
    result = _original_discover(*args, **kwargs)
    return _CandidatePool(result)


workflow_runtime.discover_three_candidates = _paged_discover

_original_markdown = st.markdown


def _paged_markdown(body, *args, **kwargs):
    text = str(body)
    if "class='candidate'" in text or 'class="candidate"' in text:
        page = int(st.session_state.get("candidate_page", 0) or 0)
        visible_start = page * PAGE_SIZE + 1
        for local_rank in range(1, PAGE_SIZE + 1):
            text = text.replace(
                f"CANDIDATE {local_rank}",
                f"CANDIDATE {visible_start + local_rank - 1}",
            )
    return _original_markdown(text, *args, **kwargs)


st.markdown = _paged_markdown

# Preserve the complete existing dashboard implementation under app_legacy.py.
# The wrapper only adds stable candidate paging and then executes that dashboard.
runpy.run_module("app_legacy", run_name="__main__")
