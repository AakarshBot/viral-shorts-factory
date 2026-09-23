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
                {"voiceover": "भारत सरकार ने नई ऊर्जा नीति में सौर ऊर्जा के नए लक्ष्य बताए।", "narrative_role": "hook", "primary_entity": "भारत", "specific_search_prompt": "भारत नई ऊर्जा नीति"},
                {"voiceover": "नीति में ऊर्जा भंडारण और ग्रिड निवेश के लिए अतिरिक्त योजनाएं हैं।", "narrative_role": "development", "primary_entity": "भारत", "specific_search_prompt": "भारत ऊर्जा भंडारण ग्रिड"},
                {"voiceover": "राज्यों के लिए भी नई कार्यान्वयन योजनाएं और लक्ष्य तय किए गए।", "narrative_role": "context", "primary_entity": "भारत", "specific_search_prompt": "भारत राज्य ऊर्जा योजना"},
                {"voiceover": "इससे ग्रिड क्षमता, भंडारण तैयारी और राज्य ऊर्जा योजनाओं पर असर पड़ेगा।", "narrative_role": "consequence", "primary_entity": "भारत", "specific_search_prompt": "भारत ऊर्जा नीति प्रभाव"},
            ],
        },
        "telugu": {
            "title": "భారతదేశం కొత్త ఇంధన విధానాన్ని ప్రకటించింది",
            "summary": "భారత ప్రభుత్వం కొత్త ఇంధన విధానాన్ని ప్రకటించింది. ఈ విధానంలో సౌర విద్యుత్, నిల్వ వ్యవస్థలు మరియు గ్రిడ్ పెట్టుబడులకు కొత్త లక్ష్యాలు ఉన్నాయి. రాష్ట్రాల కోసం కూడా కొత్త అమలు ప్రణాళికలను విడుదల చేశారు.",
            "script": [
                {"voiceover": "భారత ప్రభుత్వం కొత్త ఇంధన విధానంలో సౌర విద్యుత్ లక్ష్యాలను ప్రకటించింది.", "narrative_role": "hook", "primary_entity": "భారతదేశం", "specific_search_prompt": "భారతదేశం కొత్త ఇంధన విధానం"},
                {"voiceover": "కొత్త విధానంలో నిల్వ వ్యవస్థలు మరియు గ్రిడ్ పెట్టుబడులకు అదనపు ప్రణాళికలు ఉన్నాయి.", "narrative_role": "development", "primary_entity": "భారతదేశం", "specific_search_prompt": "భారతదేశం నిల్వ గ్రిడ్ పెట్టుబడులు"},
                {"voiceover": "రాష్ట్రాల కోసం కూడా అమలు ప్రణాళికలు మరియు కొత్త లక్ష్యాలను ప్రభుత్వం వివరించింది.", "narrative_role": "context", "primary_entity": "భారతదేశం", "specific_search_prompt": "భారతదేశం రాష్ట్ర ఇంధన ప్రణాళిక"},
                {"voiceover": "ఈ విధానం గ్రిడ్ సామర్థ్యం, నిల్వ సిద్ధత మరియు రాష్ట్ర ఇంధన ప్రణాళికలపై ప్రభావం చూపవచ్చు.", "narrative_role": "consequence", "primary_entity": "భారతదేశం", "specific_search_prompt": "భారతదేశం ఇంధన విధానం ప్రభావం"},
            ],
        },
    }

    for sample in samples.values():
        script = {
            "script": sample["script"],
            "editorial_angle": "This script adds implementation context and practical consequences beyond the headline itself.",
            "titles": [sample["title"], sample["title"] + " تفاصيل", sample["title"] + " తాజా సమాచారం"],
            "recommended_title_index": 1,
            "seo_description": sample["summary"],
        }
        ok, reason = validate_content_density(script, sample, "regular")
        assert ok, reason
        ok, reason = _quality_validate(lambda *_args: (True, ""), script, sample["summary"], "regular")
        assert ok, reason


def test_unicode_visual_queries_survive_planner():
    from unicode_runtime import install
    import visual_retrieval_planner as planner

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
        queries, visual_type = planner.build_deep_queries(scene, title)
        assert queries, f"planner lost multilingual entity {entity!r}"
        assert any(entity in query for query in queries), (entity, queries, visual_type)


def test_ultimate_bot_has_no_unused_legacy_top_level_constant_registries():
    from pathlib import Path

    source = Path(__file__).resolve().parents[1].joinpath("ultimate_bot.py").read_text(encoding="utf-8")
    assert "\nBRAND_SAFETY_KEYWORDS =" not in source
    assert "\nHOOK_STYLES_REGISTRY =" not in source
    assert '\nIMAGEMAGICK_BINARY_PATH =' not in source
    assert '\nUNSPLASH_ACCESS_KEY = os.getenv' not in source
    assert '\nPEXELS_API_KEY = os.getenv' not in source
