from script_runtime import append_research_sources, check_script_originality


def test_exact_source_sentence_is_rejected():
    source = (
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda."
    )
    script = {
        "script": [{
            "voiceover": "Alpha beta gamma delta epsilon zeta eta theta.",
            "narrative_role": "hook",
        }]
    }
    result = check_script_originality(script, {"research_evidence_text": source})
    assert result["passed"] is False
    assert result["failures"][0]["scene"] == 1


def test_rephrased_source_sentence_is_allowed():
    source = "The board confirmed the change after a detailed review of the latest information."
    script = {
        "script": [{
            "voiceover": "Officials confirmed the decision following their latest review.",
            "narrative_role": "hook",
        }]
    }
    result = check_script_originality(script, {"research_evidence_text": source})
    assert result["passed"] is True


def test_shared_phrases_do_not_trigger_originality_failure():
    source = "India beat Japan in a major cricket match."
    script = {
        "script": [{
            "voiceover": "India's win over Japan became the latest major cricket result.",
            "narrative_role": "hook",
        }]
    }
    result = check_script_originality(script, {"research_evidence_text": source})
    assert result["passed"] is True


def test_research_sources_append_to_description():
    description = append_research_sources(
        "Base description",
        [{"publisher": "Reuters", "url": "https://example.test/story"}],
    )
    assert "Sources:" in description
    assert "Reuters – https://example.test/story" in description


def test_originality_checker_does_not_expose_legacy_overlap_metrics():
    source = "Alpha beta gamma delta epsilon zeta eta theta."
    script = {"script": [{"voiceover": "Alpha beta gamma delta epsilon zeta."}]}
    result = check_script_originality(script, {"research_evidence_text": source})
    assert "longest_run" not in result["failures"][0]
    assert "sixgram_ratio" not in result["failures"][0]


def test_extractive_fallback_reuses_phase2_evidence_text():
    from script_runtime import _extractive_script_fallback

    result = _extractive_script_fallback(
        {
            "title": "Rinku Singh signing",
            "text": "Rinku Singh joined a new cricket organization.",
            "research_evidence_text": (
                "Rinku Singh became the first cricket signing for EMW Global. "
                "The organization announced its expansion into India through cricket. "
                "The signing was part of the organization's newly published regional expansion plan. "
                "The move gives the organization a named player for its first cricket-focused initiative."
            ),
        },
        {},
        "news",
        "regular",
    )

    narration = " ".join(scene["voiceover"] for scene in result["script"])
    assert "Rinku Singh became the first cricket signing for EMW Global." in narration
    assert "expansion into India through cricket" in narration
