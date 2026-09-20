"""Visual theme for the Viral Shorts Factory newsroom dashboard."""
from __future__ import annotations

import streamlit as st


_APPLIED = False


def apply_dashboard_theme() -> None:
    """Apply the light, polished newsroom visual system once per Streamlit session."""
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True

    st.markdown(
        r"""
<style>
:root {
  --vsf-ink: #17212b;
  --vsf-muted: #66727e;
  --vsf-line: rgba(23,33,43,.09);
  --vsf-blue: #177fd1;
  --vsf-blue-soft: #eaf5ff;
  --vsf-gold: #d7a247;
  --vsf-shadow: 0 18px 55px rgba(37,55,72,.08);
  --vsf-shadow-small: 0 8px 28px rgba(37,55,72,.06);
}

html, body, [class*="css"] {
  font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.stApp {
  background:
    radial-gradient(circle at 7% 2%, rgba(72,165,232,.12), transparent 26%),
    radial-gradient(circle at 94% 4%, rgba(231,183,91,.12), transparent 24%),
    radial-gradient(circle at 55% 100%, rgba(120,193,169,.07), transparent 25%),
    linear-gradient(180deg, #fbfcfd 0%, #f2f6f8 48%, #eef3f6 100%);
}

.block-container {
  max-width: 1560px !important;
  padding-top: 1.15rem !important;
  padding-bottom: 3rem !important;
}

header[data-testid="stHeader"] {
  background: rgba(251,252,253,.78) !important;
  backdrop-filter: blur(14px);
}

h1, h2, h3, h4, p, label { color: var(--vsf-ink); }
.stCaption, [data-testid="stCaptionContainer"] p { color: var(--vsf-muted) !important; }

.brand-card {
  position: relative;
  overflow: hidden;
  border: 1px solid rgba(23,33,43,.075) !important;
  background: linear-gradient(135deg, rgba(255,255,255,.98), rgba(248,251,253,.90)) !important;
  box-shadow: var(--vsf-shadow) !important;
  border-radius: 28px !important;
  padding: 26px 30px !important;
  margin-bottom: 22px !important;
}

.brand-card::after {
  content: "";
  position: absolute;
  width: 260px;
  height: 260px;
  right: -110px;
  top: -150px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(215,162,71,.15), transparent 68%);
  pointer-events: none;
}

.brand-title {
  font-size: clamp(1.65rem, 2.6vw, 2.2rem) !important;
  line-height: 1.05 !important;
  font-weight: 850 !important;
  letter-spacing: -.045em !important;
}

.brand-sub {
  color: var(--vsf-muted) !important;
  margin-top: 8px !important;
  font-size: .97rem !important;
  max-width: 850px;
}

.panel {
  border: 1px solid var(--vsf-line) !important;
  background: linear-gradient(180deg, rgba(255,255,255,.95), rgba(250,252,253,.90)) !important;
  border-radius: 22px !important;
  padding: 18px 20px !important;
  box-shadow: var(--vsf-shadow-small) !important;
  margin-bottom: 15px !important;
}

.qc-title { font-size: 1.2rem !important; font-weight: 820 !important; letter-spacing: -.02em; }
.small-muted { color: var(--vsf-muted) !important; font-size: .82rem !important; }

/* Elevated dropdown controls */
.stSelectbox { margin-bottom: 8px !important; }
.stSelectbox label {
  font-weight: 760 !important;
  color: #34414d !important;
  font-size: .82rem !important;
  letter-spacing: .01em !important;
  margin-bottom: 5px !important;
}
.stSelectbox [data-baseweb="select"] > div {
  min-height: 46px !important;
  border-radius: 14px !important;
  border: 1px solid rgba(23,33,43,.10) !important;
  background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(247,250,252,.96)) !important;
  box-shadow: 0 5px 16px rgba(30,50,70,.035) !important;
  transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease !important;
}
.stSelectbox [data-baseweb="select"] > div:hover {
  border-color: rgba(23,127,209,.30) !important;
  box-shadow: 0 8px 20px rgba(30,50,70,.07) !important;
  transform: translateY(-1px);
}
.stSelectbox [data-baseweb="select"] input,
.stSelectbox [data-baseweb="select"] div { color: var(--vsf-ink) !important; }
[data-baseweb="popover"] {
  z-index: 1000000 !important;
  border: 1px solid rgba(23,33,43,.10) !important;
  border-radius: 15px !important;
  box-shadow: 0 20px 55px rgba(30,45,60,.18) !important;
  background: #ffffff !important;
  opacity: 1 !important;
  isolation: isolate !important;
}
[data-baseweb="popover"] > div,
[data-baseweb="popover"] [data-baseweb="menu"],
[data-baseweb="popover"] [role="listbox"] {
  background: #ffffff !important;
  opacity: 1 !important;
  color: var(--vsf-ink) !important;
  border: 0 !important;
  box-shadow: none !important;
}
[data-baseweb="popover"] [role="option"] {
  min-height: 42px !important;
  padding: 9px 12px !important;
  border-radius: 9px !important;
  color: var(--vsf-ink) !important;
  background: #ffffff !important;
}
[data-baseweb="popover"] [role="option"] span,
[data-baseweb="popover"] [role="option"] div {
  color: var(--vsf-ink) !important;
  background: transparent !important;
  opacity: 1 !important;
}
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [role="option"][aria-selected="true"] {
  background: var(--vsf-blue-soft) !important;
  color: var(--vsf-blue) !important;
  font-weight: 720 !important;
}

.stTextInput label, .stTextArea label { font-weight: 720 !important; color: #34414d !important; }

/* Keep every interactive control on an opaque surface so text never shows through. */
.stSelectbox [data-baseweb="select"],
.stMultiSelect [data-baseweb="select"],
.stTextInput [data-baseweb="base-input"],
.stTextArea [data-baseweb="base-input"],
.stDateInput [data-baseweb="base-input"],
.stTimeInput [data-baseweb="base-input"] {
  background: #ffffff !important;
  color: var(--vsf-ink) !important;
  border-color: rgba(23,33,43,.10) !important;
}
.stSelectbox [data-baseweb="select"] *,
.stMultiSelect [data-baseweb="select"] *,
.stTextInput [data-baseweb="base-input"] *,
.stTextArea [data-baseweb="base-input"] *,
.stDateInput [data-baseweb="base-input"] *,
.stTimeInput [data-baseweb="base-input"] * {
  color: var(--vsf-ink) !important;
}

.stButton > button, .stLinkButton > a {
  border-radius: 13px !important;
  min-height: 2.65rem !important;
  border: 1px solid rgba(23,33,43,.10) !important;
  box-shadow: 0 5px 15px rgba(30,48,64,.055) !important;
  font-weight: 730 !important;
  transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease !important;
}
.stButton > button:hover, .stLinkButton > a:hover {
  transform: translateY(-1px);
  box-shadow: 0 9px 22px rgba(30,48,64,.09) !important;
  border-color: rgba(23,127,209,.28) !important;
}
.stButton > button[kind="primary"] {
  background: linear-gradient(135deg, #1984d5, #4aa9e7) !important;
  color: #fff !important;
  border: 0 !important;
  box-shadow: 0 11px 24px rgba(23,127,209,.20) !important;
}
.stButton > button[kind="primary"] p, .stButton > button[kind="primary"] span { color: #fff !important; }

.candidate {
  position: relative;
  border: 1px solid rgba(23,33,43,.085) !important;
  background: linear-gradient(180deg, rgba(255,255,255,.99), rgba(249,251,252,.95)) !important;
  border-radius: 20px !important;
  padding: 19px !important;
  min-height: 222px !important;
  box-shadow: var(--vsf-shadow-small) !important;
  transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
}
.candidate:hover {
  transform: translateY(-2px);
  box-shadow: 0 15px 34px rgba(37,55,72,.10) !important;
  border-color: rgba(23,127,209,.20) !important;
}
.candidate-rank { color: var(--vsf-blue) !important; font-weight: 850 !important; font-size: .72rem !important; letter-spacing: .13em !important; }
.candidate-title { color: var(--vsf-ink) !important; font-size: 1.06rem !important; line-height: 1.38 !important; font-weight: 790 !important; margin: 9px 0 11px !important; }
.candidate-reason { color: var(--vsf-muted) !important; font-size: .88rem !important; line-height: 1.5 !important; }

/* Progress bars */
div[data-testid="stProgress"] { padding: 0 !important; margin: 5px 0 11px !important; }
div[data-testid="stProgress"] > div {
  background: rgba(23,33,43,.065) !important;
  border-radius: 999px !important;
  height: 9px !important;
  overflow: hidden !important;
  box-shadow: inset 0 1px 2px rgba(20,30,40,.05) !important;
}
div[data-testid="stProgress"] > div > div {
  background: linear-gradient(90deg, #1984d5, #68b8ec) !important;
  border-radius: 999px !important;
  box-shadow: 0 2px 8px rgba(23,127,209,.20) !important;
}

.stTextInput input, .stTextArea textarea {
  border-radius: 13px !important;
  border: 1px solid rgba(23,33,43,.10) !important;
  background: rgba(255,255,255,.94) !important;
  box-shadow: inset 0 1px 2px rgba(20,30,40,.025) !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
  border-color: rgba(23,127,209,.48) !important;
  box-shadow: 0 0 0 3px rgba(23,127,209,.08) !important;
}

[data-testid="stExpander"] { border: 1px solid var(--vsf-line) !important; border-radius: 17px !important; background: rgba(255,255,255,.84) !important; }
[data-testid="stDialog"] [role="dialog"] {
  border-radius: 24px !important;
  border: 1px solid rgba(23,33,43,.09) !important;
  box-shadow: 0 30px 90px rgba(24,39,54,.18) !important;
  background: rgba(255,255,255,.97) !important;
}
[data-testid="stAlert"] { border-radius: 15px !important; }
video { border-radius: 20px !important; box-shadow: 0 18px 40px rgba(26,43,58,.12) !important; border: 1px solid rgba(23,33,43,.08) !important; }
hr { border-color: rgba(23,33,43,.08) !important; }
[data-testid="column"] { min-width: 0; }
.stMarkdown { margin-bottom: .2rem !important; }
</style>
""",
        unsafe_allow_html=True,
    )
