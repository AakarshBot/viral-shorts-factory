"""Premium presentation layer for the Viral Shorts Factory dashboard.

This module changes only Streamlit presentation: colour, spacing, hierarchy,
density, responsive behaviour and micro-interactions. Production functions
remain untouched.
"""
from __future__ import annotations

import streamlit as st

def apply_dashboard_theme() -> None:

    st.markdown(
        r"""
<style>
/* VSF EDITORIAL UI — presentation only */
:root{
  --vsf-bg:#f2f5f4;
  --vsf-surface:#ffffff;
  --vsf-surface-2:#f7f9f8;
  --vsf-ink:#182326;
  --vsf-muted:#677278;
  --vsf-faint:#96a1a4;
  --vsf-line:#dfe6e5;
  --vsf-line-2:#cbd7d5;
  --vsf-teal:#256b6d;
  --vsf-teal-dark:#1d5052;
  --vsf-teal-soft:#e8f2f1;
  --vsf-coral:#c95f43;
  --vsf-green:#2f7359;
  --vsf-green-soft:#eaf4ee;
  --vsf-amber:#a96c32;
  --vsf-amber-soft:#fbf0e3;
  --vsf-shadow:0 14px 38px rgba(22,44,48,.065);
  --vsf-shadow-hover:0 22px 50px rgba(22,44,48,.105);
}

/* Calm canvas: fewer competing surfaces, softer contrast. */
html,body,[data-testid="stAppViewContainer"],.stApp{
  background:
    radial-gradient(circle at 82% -5%,rgba(197,106,76,.075),transparent 27%),
    radial-gradient(circle at 4% 16%,rgba(47,98,101,.055),transparent 25%),
    var(--vsf-bg)!important;
  color:var(--vsf-ink)!important;
}
[data-testid="stHeader"]{
  background:rgba(242,245,244,.84)!important;
  border-bottom:1px solid rgba(213,199,184,.55)!important;
  backdrop-filter:blur(14px)!important;
}
.block-container{
  max-width:1440px!important;
  padding-top:1.15rem!important;
  padding-bottom:2.4rem!important;
}
html,body,.stApp{
  font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif!important;
  letter-spacing:-.008em;
}
[data-testid="stIconMaterial"],
[data-testid="stExpanderToggleIcon"],
span[class*="material"]{
  font-family:"Material Symbols Rounded","Material Symbols Outlined","Material Icons"!important;
  font-feature-settings:"liga"!important;
  -webkit-font-feature-settings:"liga"!important;
  letter-spacing:normal!important;
  text-transform:none!important;
}
h1,h2,h3,h4{
  color:var(--vsf-ink)!important;
  letter-spacing:-.045em!important;
}
p{color:var(--vsf-ink)!important}

/* Header becomes a compact editorial masthead. */
.brand-card{
  position:relative!important;
  overflow:hidden!important;
  min-height:78px!important;
  padding:16px 22px!important;
  border-radius:20px!important;
  border:1px solid var(--vsf-line)!important;
  background:linear-gradient(135deg,#ffffff,#eef5f4)!important;
  box-shadow:var(--vsf-shadow)!important;
}
.brand-card:after{
  content:"";
  position:absolute;
  width:190px;height:190px;
  right:-105px;top:-125px;
  border-radius:50%;
  background:radial-gradient(circle,rgba(47,98,101,.12),transparent 68%);
  pointer-events:none;
}
.brand-card:hover:before{
  content:"EDITORIAL MODE  •  READY";
  position:absolute;
  right:18px;bottom:12px;
  color:var(--vsf-teal);
  font-size:9px;font-weight:850;
  letter-spacing:.14em;
  opacity:.72;
}
.brand-pill{
  padding:4px 8px!important;
  margin-bottom:6px!important;
  border-radius:999px!important;
  background:var(--vsf-teal-soft)!important;
  border:1px solid #d0dfdc!important;
  color:var(--vsf-teal-dark)!important;
  font-size:.59rem!important;
  letter-spacing:.12em!important;
}
.brand-title{
  font-size:clamp(1.55rem,2.4vw,2.05rem)!important;
  font-weight:900!important;
  line-height:1!important;
}
.brand-sub{
  margin-top:5px!important;
  color:var(--vsf-muted)!important;
  font-size:.78rem!important;
}
.factory-status{
  min-height:76px!important;
  padding:11px 13px!important;
  border-radius:15px!important;
  border:1px solid var(--vsf-line)!important;
  background:rgba(255,253,249,.88)!important;
  box-shadow:none!important;
}
.factory-status-label{
  color:var(--vsf-faint)!important;
  font-size:.55rem!important;
  letter-spacing:.14em!important;
}
.factory-status-value{
  color:var(--vsf-ink)!important;
  font-size:.88rem!important;
}

/* Section hierarchy: title carries the message; descriptions become whisper text. */
.section-kicker{
  color:var(--vsf-teal)!important;
  font-size:.58rem!important;
  letter-spacing:.16em!important;
  margin-bottom:.22rem!important;
}
.section-title{
  font-size:1.42rem!important;
  font-weight:900!important;
  line-height:1.05!important;
}
.section-subtitle{
  color:var(--vsf-muted)!important;
  font-size:.76rem!important;
  line-height:1.4!important;
  margin:.28rem 0 .8rem!important;
  max-width:860px!important;
}

/* Cards share one visual language. */
.panel,.candidate,.story-card,.output-card,.release-card,
[data-testid="stMetric"],[data-testid="stExpander"]{
  border:1px solid var(--vsf-line)!important;
  background:rgba(255,253,249,.94)!important;
  border-radius:16px!important;
  box-shadow:var(--vsf-shadow)!important;
}
.panel{padding:14px 16px!important}
.story-card{padding:15px!important}
.output-card{padding:13px 14px!important}
.release-card{padding:14px 16px!important}
.story-card:hover,.candidate:hover{
  border-color:#c8d8d5!important;
  box-shadow:var(--vsf-shadow-hover)!important;
  transform:translateY(-1px);
  transition:all .16s ease;
}
.story-rank,.candidate-rank{
  color:var(--vsf-teal)!important;
  font-size:.58rem!important;
  font-weight:900!important;
  letter-spacing:.14em!important;
}
.story-title,.candidate-title{
  color:var(--vsf-ink)!important;
  font-size:.96rem!important;
  font-weight:850!important;
  line-height:1.28!important;
  margin:5px 0 7px!important;
}
.story-meta,.story-reason,.candidate-reason{
  color:var(--vsf-muted)!important;
  font-size:.72rem!important;
  line-height:1.42!important;
}
.story-reason{margin:7px 0 9px!important;max-height:5.5rem!important}

/* Metrics become a quiet information rail instead of five loud boxes. */
[data-testid="stMetric"]{
  padding:9px 11px!important;
  min-height:66px!important;
  box-shadow:none!important;
}
[data-testid="stMetricLabel"]{
  color:var(--vsf-muted)!important;
  font-size:.59rem!important;
  font-weight:750!important;
}
[data-testid="stMetricValue"]{
  color:var(--vsf-ink)!important;
  font-size:1.15rem!important;
  font-weight:900!important;
}

/* Pipeline: clear visual rhythm, less copy. */
.stage-strip{
  gap:6px!important;
  margin:9px 0 12px!important;
}
.stage-card{
  padding:8px 9px!important;
  border-radius:10px!important;
  background:rgba(255,253,249,.72)!important;
  border:1px solid var(--vsf-line)!important;
}
.stage-name{
  font-size:.66rem!important;
  font-weight:850!important;
}
.stage-state{
  color:var(--vsf-muted)!important;
  font-size:.56rem!important;
  margin-top:3px!important;
}
.stage-card.active{
  border-color:#9dbbb8!important;
  background:var(--vsf-teal-soft)!important;
}
.stage-card.done{
  border-color:#bdd9c9!important;
  background:var(--vsf-green-soft)!important;
}
.stage-card.stopped{
  border-color:#e8c8aa!important;
  background:var(--vsf-amber-soft)!important;
}
.live-bar{
  padding:8px 11px!important;
  margin:7px 0 12px!important;
  border-radius:10px!important;
  border:1px solid var(--vsf-line)!important;
  background:rgba(255,253,249,.7)!important;
  box-shadow:none!important;
}
.live-bar-copy{
  color:var(--vsf-muted)!important;
  font-size:.66rem!important;
}

/* Buttons: one restrained primary colour. */
.stButton>button,.stLinkButton>a{
  min-height:38px!important;
  border-radius:10px!important;
  border:1px solid var(--vsf-line-2)!important;
  background:#fffdf9!important;
  color:var(--vsf-ink)!important;
  font-size:.74rem!important;
  font-weight:850!important;
  box-shadow:none!important;
}
.stButton>button:hover,.stLinkButton>a:hover{
  border-color:#9dbbb8!important;
  background:#f8fbfa!important;
  box-shadow:0 7px 18px rgba(60,45,30,.07)!important;
}
.stButton>button[kind="primary"]{
  background:linear-gradient(180deg,var(--vsf-teal),var(--vsf-teal-dark))!important;
  border-color:var(--vsf-teal-dark)!important;
  color:#fff!important;
  box-shadow:0 8px 18px rgba(47,98,101,.16)!important;
}
.stButton>button[kind="primary"] p,.stButton>button[kind="primary"] span{color:#fff!important}

/* Inputs: compact and editorial. */
.stSelectbox label,.stTextInput label,.stTextArea label{
  color:var(--vsf-muted)!important;
  font-size:.65rem!important;
  font-weight:800!important;
}
.stSelectbox [data-baseweb="select"]>div{
  min-height:39px!important;
  border-radius:10px!important;
  border:1px solid var(--vsf-line)!important;
  background:#fffdf9!important;
}
.stTextInput input,.stTextArea textarea{
  color:var(--vsf-ink)!important;
  background:#fffdf9!important;
  border:1px solid var(--vsf-line)!important;
  border-radius:10px!important;
  font-size:.78rem!important;
}
.stTextArea textarea{min-height:96px!important}

/* Popovers remain opaque and readable. */
[data-baseweb="popover"]{
  z-index:1000000!important;
  background:#fffdf9!important;
  border:1px solid var(--vsf-line)!important;
  border-radius:12px!important;
  box-shadow:0 18px 48px rgba(40,35,30,.16)!important;
  opacity:1!important;
}
[data-baseweb="popover"] [data-baseweb="menu"],
[data-baseweb="popover"] [role="listbox"]{
  background:#fffdf9!important;
  color:var(--vsf-ink)!important;
}
[data-baseweb="popover"] [role="option"]{
  color:var(--vsf-ink)!important;
  background:transparent!important;
  border-radius:8px!important;
  min-height:36px!important;
  font-size:.75rem!important;
}
[data-baseweb="popover"] [role="option"]:hover,
[data-baseweb="popover"] [aria-selected="true"]{
  background:var(--vsf-teal-soft)!important;
}

/* Sidebar becomes a warm control rail, not a second dark app. */
section[data-testid="stSidebar"]{
  background:#e7efed!important;
  border-right:1px solid #d2dedc!important;
}
section[data-testid="stSidebar"]>div{padding-top:1rem!important}
section[data-testid="stSidebar"] .stMarkdown p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{
  color:#5f6b70!important;
}
.sidebar-kicker{
  color:var(--vsf-teal)!important;
  font-size:.58rem!important;
  letter-spacing:.15em!important;
}
.sidebar-title{
  color:var(--vsf-ink)!important;
  font-size:.94rem!important;
  font-weight:900!important;
}
.sidebar-status{
  background:rgba(255,255,255,.90)!important;
  border:1px solid #d2dedd!important;
  border-radius:12px!important;
  padding:10px 11px!important;
}
.sidebar-status-title{color:var(--vsf-ink)!important;font-size:.7rem!important}
.sidebar-status-copy{color:var(--vsf-muted)!important;font-size:.62rem!important;line-height:1.35!important}
[data-testid="stSidebar"] .stButton>button{
  background:rgba(255,253,249,.8)!important;
}

/* Diagnostics/release gates: compact, scan-friendly. */
.release-gates,.qc-guide{gap:7px!important}
.release-gate,.qc-guide-step{
  padding:9px 10px!important;
  border-radius:11px!important;
  box-shadow:none!important;
}
.release-gate-name,.qc-guide-step b{font-size:.68rem!important}
.release-gate-detail,.qc-guide-step span{
  color:var(--vsf-muted)!important;
  font-size:.61rem!important;
  line-height:1.35!important;
}
.qc-status{font-size:.56rem!important;padding:4px 7px!important}

/* Tables and expanders stay quiet until needed. */
[data-testid="stDataFrame"]{
  border:1px solid var(--vsf-line)!important;
  border-radius:11px!important;
  overflow:hidden!important;
}
div[data-testid="stExpander"] summary p{
  color:var(--vsf-ink)!important;
  font-size:.7rem!important;
  font-weight:850!important;
}
div[data-testid="stExpander"]{
  box-shadow:none!important;
}

/* Footer is deliberately tiny. */
.dashboard-footer{
  color:var(--vsf-faint)!important;
  font-size:.58rem!important;
  letter-spacing:.02em!important;
  padding:8px 0!important;
}

/* Easter egg: hover the factory masthead for a tiny editorial status mark. */
.brand-card{cursor:default!important}
.brand-card:hover{
  box-shadow:0 15px 38px rgba(47,98,101,.10)!important;
}

/* Responsive: avoid dense desktop cards becoming cramped. */
@media(max-width:1100px){
  .stage-strip{grid-template-columns:repeat(3,minmax(0,1fr))!important}
}
@media(max-width:800px){
  .stage-strip{grid-template-columns:repeat(2,minmax(0,1fr))!important}
  .section-title{font-size:1.25rem!important}
  .brand-title{font-size:1.45rem!important}
}
@media(max-width:560px){
  .stage-strip{grid-template-columns:1fr!important}
  .section-subtitle{font-size:.7rem!important}
}

/* Keep long generated headlines from wrecking card geometry. */
.story-title,.story-reason,.candidate-title,.candidate-reason,.output-card,
.release-gate,.release-gate-detail,.timeline-message,.brand-sub,.sidebar-status-copy{
  min-width:0!important;
  overflow-wrap:anywhere!important;
  word-break:break-word!important;
}

/* VSF WORKFLOW SHELL — dense by default, expressive only at the active step. */
.studio-masthead{
  min-height:58px;
  padding:8px 0 7px;
  display:flex;
  flex-direction:column;
  justify-content:center;
}
.studio-masthead-kicker{
  color:var(--vsf-teal);
  font-size:.56rem;
  font-weight:900;
  letter-spacing:.15em;
  text-transform:uppercase;
  margin-bottom:2px;
}
.studio-masthead-title{
  color:var(--vsf-ink);
  font-size:1.42rem;
  line-height:1;
  font-weight:950;
  letter-spacing:-.045em;
}
.studio-masthead-sub{
  color:var(--vsf-muted);
  font-size:.68rem;
  margin-top:4px;
}
.studio-logo-fallback{
  width:42px;
  height:42px;
  display:grid;
  place-items:center;
  border:1px solid var(--vsf-line);
  border-radius:13px;
  background:rgba(255,255,255,.78);
  box-shadow:var(--vsf-shadow);
  font-size:1.2rem;
}
.studio-status{
  min-height:52px;
  padding:7px 10px;
  display:flex;
  align-items:center;
  justify-content:flex-start;
  gap:8px;
  border:1px solid var(--vsf-line);
  border-radius:13px;
  background:rgba(255,255,255,.78);
  box-shadow:none;
}
.studio-status.live{
  border-color:#b9d7cd;
  background:var(--vsf-green-soft);
}
.studio-status.done{
  border-color:#bdd9c9;
  background:var(--vsf-green-soft);
}
.studio-status-dot{
  width:8px;height:8px;border-radius:50%;
  background:#9aa7aa;
  flex:0 0 auto;
}
.studio-status.live .studio-status-dot{
  background:var(--vsf-teal);
  box-shadow:0 0 0 0 rgba(37,107,109,.30);
  animation:vsf-pulse 1.8s infinite;
}
.studio-status.done .studio-status-dot{
  background:var(--vsf-green);
}
.studio-status-label{
  color:var(--vsf-faint);
  font-size:.49rem;
  font-weight:900;
  letter-spacing:.14em;
}
.studio-status-value{
  color:var(--vsf-ink);
  font-size:.72rem;
  font-weight:900;
  margin-top:1px;
}
@keyframes vsf-pulse{
  0%{box-shadow:0 0 0 0 rgba(37,107,109,.30)}
  70%{box-shadow:0 0 0 7px rgba(37,107,109,0)}
  100%{box-shadow:0 0 0 0 rgba(37,107,109,0)}
}

.workflow-shell{
  margin:9px 0 15px;
  padding:10px 12px 9px;
  border:1px solid var(--vsf-line);
  border-radius:15px;
  background:rgba(255,255,255,.80);
  box-shadow:var(--vsf-shadow);
}
.workflow-topline{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  margin-bottom:8px;
}
.workflow-kicker{
  color:var(--vsf-faint);
  font-size:.52rem;
  font-weight:900;
  letter-spacing:.15em;
  margin-right:7px;
}
.workflow-current{
  color:var(--vsf-ink);
  font-size:.73rem;
  font-weight:900;
}
.workflow-percent{
  color:var(--vsf-teal);
  font-size:.8rem;
  font-weight:950;
}
.workflow-rail{
  display:flex;
  align-items:center;
  width:100%;
  gap:6px;
}
.workflow-node{
  min-width:0;
  display:flex;
  align-items:center;
  gap:6px;
  flex:0 1 auto;
}
.workflow-dot{
  width:22px;
  height:22px;
  border-radius:50%;
  display:grid;
  place-items:center;
  flex:0 0 22px;
  font-size:.57rem;
  line-height:1;
  font-weight:950;
  border:1px solid #ccd5d4;
  background:#f7f9f8;
  color:#829093;
}
.workflow-node-copy{
  min-width:0;
}
.workflow-node-name{
  color:#7b878a;
  font-size:.56rem;
  font-weight:850;
  line-height:1.05;
  white-space:nowrap;
}
.workflow-node-state{
  color:#a0abad;
  font-size:.47rem;
  margin-top:2px;
  white-space:nowrap;
}
.workflow-node.done .workflow-dot{
  background:var(--vsf-green);
  border-color:var(--vsf-green);
  color:#fff;
}
.workflow-node.done .workflow-node-name{
  color:#45685a;
}
.workflow-node.active .workflow-dot{
  background:var(--vsf-teal);
  border-color:var(--vsf-teal);
  color:#fff;
  box-shadow:0 0 0 4px rgba(37,107,109,.10);
  animation:vsf-active-glow 2.2s ease-in-out infinite;
}
.workflow-node.active .workflow-node-name,
.workflow-node.active-error .workflow-node-name{
  color:var(--vsf-ink);
  font-weight:950;
}
.workflow-node.active .workflow-node-state{color:var(--vsf-teal);font-weight:850}
.workflow-node.active-error .workflow-dot{
  background:var(--vsf-coral);
  border-color:var(--vsf-coral);
  color:#fff;
}
.workflow-node.active-error .workflow-node-state{
  color:var(--vsf-coral);
  font-weight:850;
}
@keyframes vsf-active-glow{
  0%,100%{box-shadow:0 0 0 3px rgba(37,107,109,.08)}
  50%{box-shadow:0 0 0 6px rgba(37,107,109,.14)}
}
.workflow-connector{
  height:1px;
  flex:1 1 18px;
  min-width:6px;
  max-width:54px;
  background:#dce4e2;
}
.workflow-connector.done{
  background:#a9c9bc;
}
.workflow-bottomline{
  display:flex;
  align-items:center;
  gap:7px;
  min-width:0;
  margin-top:8px;
}
.workflow-status-pill{
  flex:0 0 auto;
  display:inline-flex;
  align-items:center;
  padding:3px 6px;
  border-radius:999px;
  background:#f1f4f3;
  color:#7c898b;
  font-size:.49rem;
  font-weight:950;
  letter-spacing:.08em;
}
.workflow-status-pill.live{
  color:var(--vsf-teal-dark);
  background:var(--vsf-teal-soft);
}
.workflow-status-pill.error{
  color:var(--vsf-coral);
  background:#faece7;
}
.workflow-message{
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
  color:var(--vsf-muted);
  font-size:.58rem;
}
.workflow-step{
  margin-left:auto;
  color:var(--vsf-faint);
  font-size:.52rem;
  white-space:nowrap;
}

.release-section-head,.release-subhead{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  margin-bottom:8px;
}
.release-subhead{
  justify-content:flex-start;
  color:var(--vsf-ink);
}
.release-section-head b,.release-subhead b{
  font-size:.73rem;
  font-weight:900;
}
.release-step-dot{
  width:20px;height:20px;
  display:inline-grid;
  place-items:center;
  margin-right:6px;
  border-radius:50%;
  background:var(--vsf-teal-soft);
  border:1px solid #c9dddd;
  color:var(--vsf-teal-dark);
  font-size:.55rem;
  font-weight:950;
  vertical-align:middle;
}
.release-state{
  padding:3px 7px;
  border-radius:999px;
  font-size:.51rem;
  font-weight:900;
  letter-spacing:.05em;
}
.release-state.ready{
  color:var(--vsf-green);
  background:var(--vsf-green-soft);
}
.release-state.waiting{
  color:var(--vsf-amber);
  background:var(--vsf-amber-soft);
}
.release-success{
  display:flex;
  align-items:center;
  gap:11px;
  padding:2px 0 12px;
}
.release-success-icon{
  width:40px;height:40px;
  border-radius:50%;
  display:grid;
  place-items:center;
  flex:0 0 40px;
  background:var(--vsf-green);
  color:#fff;
  font-size:1rem;
  font-weight:950;
  box-shadow:0 8px 20px rgba(47,115,89,.15);
}
.release-success-kicker{
  color:var(--vsf-green);
  font-size:.5rem;
  font-weight:950;
  letter-spacing:.14em;
}
.release-success-title{
  color:var(--vsf-ink);
  font-size:.95rem;
  font-weight:950;
  line-height:1.15;
  margin-top:2px;
}
.release-success-detail{
  color:var(--vsf-muted);
  font-size:.62rem;
  line-height:1.4;
  margin-top:3px;
}
.release-id-label{
  color:var(--vsf-faint);
  font-size:.5rem;
  font-weight:950;
  letter-spacing:.14em;
  margin:2px 0 4px;
}
.stCodeBlock{
  border-radius:10px!important;
}

/* Compact production controls: settings are available on demand instead of consuming the main canvas. */
.stPopover{
  max-width:100%!important;
}
[data-testid="stPopover"] > button{
  min-height:34px!important;
  border-radius:999px!important;
  border:1px solid var(--vsf-line-2)!important;
  background:rgba(255,255,255,.82)!important;
  color:var(--vsf-ink)!important;
  font-size:.64rem!important;
  font-weight:900!important;
}
[data-testid="stPopover"] > button:hover{
  border-color:#9dbbb8!important;
  background:#f8fbfa!important;
  box-shadow:0 7px 18px rgba(60,45,30,.07)!important;
}

/* Compact stage labels/readouts. */
.workflow-shell + .section-kicker{
  margin-top:0!important;
}
[data-testid="stExpander"]{
  border-radius:11px!important;
}
div[data-testid="stExpander"] summary{
  min-height:40px!important;
}

/* Keep the live mode selector from becoming the visual center of gravity. */
.st-key-live_format_menu button,
.st-key-live_topic_menu button,
.st-key-live_sports_menu button,
.st-key-live_cricket_scope_menu button,
.st-key-test_menu button{
  min-height:44px!important;
  padding:8px 15px!important;
  border-radius:14px!important;
  font-size:.71rem!important;
}

</style>
""",
        unsafe_allow_html=True,
    )