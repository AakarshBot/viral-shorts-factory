from script_runtime import append_research_sources, check_script_originality
from final_qc_runtime import evaluate_originality_gate


def test_verbatim_overlap_blocks_eight_word_run():
    source = "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda."
    script = {"script": [{"voiceover": "Alpha beta gamma delta epsilon zeta eta theta is copied.", "human_contributed": False}]}
    result = check_script_originality(script, {"research_evidence_pack": {"sources": [{"text": source}]}})
    assert result["passed"] is False
    assert result["failures"][0]["longest_run"] >= 8


def test_sixgram_overlap_blocks_above_fifteen_percent():
    source = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"
    script = {"script": [{"voiceover": "one two three four five six seven eight nine ten eleven twelve seventeen eighteen", "human_contributed": False}]}
    result = check_script_originality(script, {"research_evidence_pack": {"sources": [{"text": source}]}})
    assert result["passed"] is False
    assert result["failures"][0]["sixgram_ratio"] > 0.15


def test_creator_insight_is_required_at_final_qc():
    script = {"script": [{"voiceover": "Generated factual scene.", "human_contributed": False}]}
    gate = evaluate_originality_gate(script)
    assert gate["passed"] is False
    assert "Creator Insight" in gate["detail"]


def test_extract_fallback_is_private_only():
    script = {
        "fallback_mode": "extractive_source_grounded",
        "script": [{"voiceover": "This is a sufficiently long creator insight with original context.", "human_contributed": True}],
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
