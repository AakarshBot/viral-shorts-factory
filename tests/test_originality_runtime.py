from script_runtime import append_research_sources, check_script_originality, _run_real_critique
from final_qc_runtime import evaluate_originality_gate


def test_verbatim_overlap_blocks_eight_word_run():
    source = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda."
    script = {"script": [{"voiceover": "Alpha beta gamma delta epsilon zeta eta theta is copied."}]}
    result = check_script_originality(script, {"research_evidence_pack": {"sources": [{"text": source}]}})
    assert result["passed"] is False
    assert result["failures"][0]["longest_run"] >= 8


def test_sixgram_overlap_blocks_above_fifteen_percent():
    source = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"
    script = {"script": [{"voiceover": "one two three four five six seven eight nine ten eleven twelve seventeen eighteen"}]}
    result = check_script_originality(script, {"research_evidence_pack": {"sources": [{"text": source}]}})
    assert result["passed"] is False
    assert result["failures"][0]["sixgram_ratio"] > 0.15


def test_final_qc_does_not_require_extra_narration():
    script = {"script": [{"voiceover": "Generated factual scene."}]}
    gate = evaluate_originality_gate(script)
    assert gate["passed"] is True
    assert gate["public_blocked"] is False


def test_extract_fallback_is_private_only():
    script = {
        "fallback_mode": "extractive_source_grounded",
        "script": [{"voiceover": "This is a sufficiently long source-grounded fallback scene."}],
    }
    gate = evaluate_originality_gate(script)
    assert gate["passed"] is True
    assert gate["public_blocked"] is True


def test_research_sources_append_to_description():
    description = append_research_sources(
        "Base description",
        [{"publisher": "Reuters", "url": "https://example.test/story"}],
    )
    assert "Sources:" in description
    assert "Reuters – https://example.test/story" in description


def test_real_critique_normalizes_required_json(monkeypatch):
    import script_runtime
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(
        script_runtime,
        "_originality_llm",
        lambda *_args, **_kwargs: {
            "score": 8,
            "unsupported_claims": [],
            "exaggerations": ["too strong"],
            "fixes": ["soften wording"],
        },
    )
    result = _run_real_critique(
        {"script": [{"voiceover": "The team announced the change."}]},
        {"research_evidence_text": "The team announced the change."},
    )
    assert result["score"] == 8
    assert result["unsupported_claims"] == []
    assert result["provider"] == "groq"

def test_originality_rewrite_falls_through_to_openrouter_free(monkeypatch):
    import script_runtime

    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    calls = []

    def fake_llm(url, payload, headers):
        calls.append(url)
        if "groq.com" in url:
            return None
        return {"script": [{"index": 1, "voiceover": "A genuinely rewritten factual scene with the same supported information."}]}

    monkeypatch.setattr(script_runtime, "_originality_llm", fake_llm)
    result = script_runtime._rewrite_for_originality_once(
        {"script": [{"voiceover": "A source-derived scene with overlapping wording."}]},
        {"research_evidence_text": "A source-derived scene with overlapping wording and supported facts."},
        {"passed": False, "failures": [{"scene": 1}]},
    )

    assert result["script"][0]["voiceover"].startswith("A genuinely rewritten")
    assert calls[0].startswith("https://api.groq.com/")
    assert calls[1].startswith("https://openrouter.ai/")


def test_extractive_fallback_reuses_phase2_evidence_text():
    from script_runtime import _extractive_script_fallback

    result = _extractive_script_fallback(
        {
            "title": "Rinku Singh signing",
            "text": "Rinku Singh joined a new cricket organization.",
            "research_evidence_text": (
                "Rinku Singh became the first cricket signing for EMW Global. "
                "The organization announced its expansion into India through cricket."
            ),
        },
        {},
        "news",
        "regular",
    )

    narration = " ".join(scene["voiceover"] for scene in result["script"])
    assert "Rinku Singh became the first cricket signing for EMW Global." in narration
    assert "expansion into India through cricket" in narration

