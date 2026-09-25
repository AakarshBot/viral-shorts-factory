from factory_contract_audit import (
    dashboard_architecture_audit,
    language_surface_audit,
    runtime_surface_audit,
    source_syntax_audit,
)


def test_factory_source_syntax_audit():
    assert source_syntax_audit() == []


def test_factory_runtime_surface_audit():
    assert runtime_surface_audit() == []


def test_multilingual_language_surface_audit():
    assert language_surface_audit() == []


def test_single_dashboard_architecture_audit():
    assert dashboard_architecture_audit() == []


def test_multilingual_script_grounding_and_metadata():
    from unicode_runtime import install
    from script_runtime import validate_content_density
    from quality_runtime import _quality_validate

    install()

    samples = {
        "hindi": {
            "title": "भारत ने नई ऊर्जा नीति की घोषणा की",
            "summary": "भारत सरकार ने नई ऊर्जा नीति की घोषणा की है। नीति में सौर ऊर्जा, भंडारण और ग्रिड निवेश के लिए नए लक्ष्य शामिल हैं। राज्यों के लिए भी नई योजनाएं जारी की गई हैं।",
            "script": [
                {"voiceover": "नई ऊर्जा नीति में सौर लक्ष्य घोषित हुए।", "narrative_role": "hook", "primary_entity": "भारत", "visual_intent": "news_event", "specific_search_prompt": "भारत नई ऊर्जा नीति", "sport_or_topic_category": "Energy policy"},
                {"voiceover": "नीति में भंडारण और ग्रिड निवेश की योजनाएं हैं।", "narrative_role": "development", "primary_entity": "भारत", "visual_intent": "news_event", "specific_search_prompt": "भारत ऊर्जा भंडारण ग्रिड", "sport_or_topic_category": "Energy policy"},
                {"voiceover": "राज्यों के लिए भी नए क्रियान्वयन लक्ष्य तय हुए।", "narrative_role": "context", "primary_entity": "भारत", "visual_intent": "news_event", "specific_search_prompt": "भारत राज्य ऊर्जा योजना", "sport_or_topic_category": "Energy policy"},
                {"voiceover": "इससे ग्रिड क्षमता और राज्य ऊर्जा योजनाओं पर असर पड़ेगा।", "narrative_role": "consequence", "primary_entity": "भारत", "visual_intent": "news_event", "specific_search_prompt": "भारत ऊर्जा नीति प्रभाव", "sport_or_topic_category": "Energy policy"},
            ],
        },
        "telugu": {
            "title": "భారతదేశం కొత్త ఇంధన విధానాన్ని ప్రకటించింది",
            "summary": "భారత ప్రభుత్వం కొత్త ఇంధన విధానాన్ని ప్రకటించింది. ఈ విధానంలో సౌర విద్యుత్, నిల్వ వ్యవస్థలు మరియు గ్రిడ్ పెట్టుబడులకు కొత్త లక్ష్యాలు ఉన్నాయి. రాష్ట్రాల కోసం కూడా కొత్త అమలు ప్రణాళికలను విడుదల చేశారు.",
            "script": [
                {"voiceover": "భారతదేశం సౌర లక్ష్యాలు ప్రకటించింది.", "narrative_role": "hook", "primary_entity": "భారతదేశం", "visual_intent": "news_event", "specific_search_prompt": "భారతదేశం కొత్త ఇంధన విధానం", "sport_or_topic_category": "Energy policy"},
                {"voiceover": "కొత్త విధానంలో నిల్వ, గ్రిడ్ పెట్టుబడుల ప్రణాళికలు ఉన్నాయి.", "narrative_role": "development", "primary_entity": "భారతదేశం", "visual_intent": "news_event", "specific_search_prompt": "భారతదేశం నిల్వ గ్రిడ్ పెట్టుబడులు", "sport_or_topic_category": "Energy policy"},
                {"voiceover": "రాష్ట్రాల కోసం కొత్త అమలు లక్ష్యాలను వివరించారు.", "narrative_role": "context", "primary_entity": "భారతదేశం", "visual_intent": "news_event", "specific_search_prompt": "భారతదేశం రాష్ట్ర ఇంధన ప్రణాళిక", "sport_or_topic_category": "Energy policy"},
                {"voiceover": "ఈ విధానం గ్రిడ్ సామర్థ్యం, నిల్వ సిద్ధతపై ప్రభావం చూపవచ్చు.", "narrative_role": "consequence", "primary_entity": "భారతదేశం", "visual_intent": "news_event", "specific_search_prompt": "భారతదేశం ఇంధన విధానం ప్రభావం", "sport_or_topic_category": "Energy policy"},
            ],
        },
    }

    for sample in samples.values():
        script = {
            "script": sample["script"],
            "creator_insight": "The documented policy change matters because it alters implementation plans and resource priorities.",
            "editorial_angle": "This script adds implementation context and practical consequences beyond the headline itself.",
            "titles": [sample["title"], sample["title"] + " تفاصيل", sample["title"] + " తాజా సమాచారం"],
            "recommended_title_index": 1,
            "seo_description": sample["summary"],
        }
        ok, reason = validate_content_density(script, sample, "regular")
        assert ok, reason
        ok, reason = _quality_validate(lambda *_args: (True, ""), script, sample["summary"], "regular")
        assert ok, reason


def test_unicode_visual_queries_survive_canonical_search_intent():
    from unicode_runtime import install
    from visual_search_intent_runtime import resolve_visual_search_intent

    install()
    for entity, title in (
        ("भारत", "भारत नई ऊर्जा नीति"),
        ("భారతదేశం", "భారతదేశం కొత్త ఇంధన విధానం"),
    ):
        scene = {
            "primary_entity": entity,
            "visual_intent": "news event",
            "specific_search_prompt": title,
            "voiceover": title,
            "sport_or_topic_category": "regional_state_news",
        }
        intent = resolve_visual_search_intent(scene, title)
        queries = list(intent.queries)
        assert queries, f"canonical search lost multilingual entity {entity!r}"
        assert any(entity in query for query in queries), (entity, queries, intent.visual_type)


def test_ultimate_bot_has_no_unused_legacy_top_level_constant_registries():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "\nBRAND_SAFETY_KEYWORDS =" not in source
    assert "\nHOOK_STYLES_REGISTRY =" not in source
    assert '\nIMAGEMAGICK_BINARY_PATH =' not in source
    assert '\nUNSPLASH_ACCESS_KEY = os.getenv' not in source
    assert '\nPEXELS_API_KEY = os.getenv' not in source

def test_runtime_bindings_do_not_restore_legacy_pipeline_fallbacks():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("runtime_bindings.py").read_text(encoding="utf-8")
    assert "_wrap_editorial_provider_usage" not in source
    assert 'return getattr(bot, "write_script", None)' not in source
    assert 'return getattr(bot, "process_visuals_async", None)' not in source
    assert 'return getattr(bot, "generate_voiceover_and_timestamps", None)' not in source
    assert 'return getattr(bot, "generate_karaoke_clip", None)' not in source
    assert "Final QC runtime unavailable" not in source


def test_production_lifecycle_records_failures_and_completed_uploads():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert 'reason = "Script generation returned no usable script."' in source
    assert 'reason = "Voiceover generation failed to produce audio files."' in source
    assert 'reason = "Post-render validation failed: final video is missing."' in source
    assert '"READY_FOR_UPLOAD"' in source
    assert '"UPLOADED_PRIVATE" if str(pub_mode or "").strip().lower() == "private" else "UPLOADED"' in source

def test_ultimate_bot_function_coverage_is_complete():
    from factory_function_coverage import collect_factory_function_coverage

    report = collect_factory_function_coverage()
    assert report["complete"], report
    assert report["unmapped"] == []
    assert report["stale_map"] == []
