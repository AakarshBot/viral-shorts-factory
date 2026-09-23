# Viral Shorts Factory — Production Workflow Specification

## Source of truth
The dashboard is a newsroom-style production system. Discovery and production are separate stages. The user remains the final editor and controls publication.

## Stage 1 — Configuration
- Language
- Format: Deep Dive / Top-5 / Cricket
- Category: format-aware
- Start Factory begins discovery only.

## Stage 2 — Discovery
- Find current, fresh stories with strong same-day opportunity.
- Cheap filtering and deduplication happen first.
- Safety/problematic-content filtering is mandatory.
- Compare against channel history when available.
- Produce exactly 3 strong, diverse candidates.
- Show candidate title/topic, why it was selected, source summary and score dimensions.
- No script/audio/image/render/upload API calls before user selects one candidate.

## Stage 3 — User story selection
- User selects one of the 3 candidates.
- Rejected candidates are retained locally as rejected/seen to reduce repeat selection.

## Stage 4 — Research + script
- Research multiple sources on the chosen topic.
- Build a fact-grounded synthesis.
- Script style: compact news article, high information density, no performative filler, no forced CTA, no forced `#shorts`.
- Target spoken runtime is 20–30 seconds. Duration is measured from the selected narrator profile; a single bounded pre-review compression pass may tighten an otherwise valid overlong draft.
- Final synthesized narration is a hard 30-second ceiling; no post-approval rewrite loop is allowed.
- Self-critique checks factual support, compression, opening clarity, repetition, originality and tone.
- Display script on dashboard while production continues.

## Stage 5 — Audio / visuals / subtitles / render
- Story-specific narration tone and pace.
- Word-level subtitle timing converted into readable 1–2 line phrases.
- Content-first visuals.
- Branded glossy border and top-right logo.
- Border is for branding, not policy/detection evasion.

## Stage 6 — Final QC
- Show video preview.
- Editable title, description and creator comment.
- Final safety/quality checks.
- Publication visibility is selected only here.
- No automatic publication from Start Factory.

## Stage 7 — Learning
- Channel analytics sync is manual/explicit, not part of production.
- Learn from topic, angle, hook, script structure, duration, visual style, title style and performance metrics.
