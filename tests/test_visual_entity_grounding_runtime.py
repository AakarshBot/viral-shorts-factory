from script_runtime import clean_script_data
from visual_entity_grounding_runtime import apply_grounding, ground_scene_entity
from visual_search_intent_runtime import resolve_visual_search_intent

def _story():
    return {
        'title': "India women's team win the T20 World Cup",
        'step_1_headline': "India women's team win the T20 World Cup",
        'research_bundle': "India women's team lifted the trophy. Harmanpreet Kaur captained India.",
        'research_sources': [
            {'title': "India women's team lift trophy", 'snippet': "Harmanpreet Kaur leads the team."}
        ],
    }

def test_unrelated_person_is_repaired_to_story_anchor():
    scene = {
        'primary_entity': 'Rashid Khan',
        'visual_intent': 'person portrait',
        'specific_search_prompt': 'Rashid Khan cricket portrait',
    }
    grounded = apply_grounding(scene, _story())
    assert grounded['primary_entity'] == "India women's team"
    assert grounded['visual_entity_grounded'] is True
    assert grounded['visual_entity_original'] == 'Rashid Khan'

def test_phase2_evidence_pack_supports_valid_person_identity():
    story = {
        "title": "India to wear BCCI jersey against Japan in friendly",
        "research_evidence_pack": {
            "claims": [
                {
                    "text": "Shreyas Iyer is among the players involved in India's squad planning for the Japan friendly.",
                    "status": "corroborated",
                },
                {
                    "text": "The Asian Games kit issue was resolved before the fixture.",
                    "status": "corroborated",
                },
            ],
            "sources": [
                {
                    "title": "India to wear BCCI jersey against Japan",
                    "clean_text_preview": "Shreyas Iyer and the India squad were discussed in the latest reporting.",
                }
            ],
        },
    }
    scene = {
        "primary_entity": "Shreyas Iyer",
        "visual_intent": "person action",
        "specific_search_prompt": "Shreyas Iyer",
    }

    grounded = apply_grounding(scene, story)

    assert grounded["primary_entity"] == "Shreyas Iyer"
    assert grounded["visual_entity_grounded"] is True
    assert grounded["visual_entity_grounding_confidence"] >= 0.80



def test_supported_person_is_preserved():
    scene = {
        'primary_entity': 'Harmanpreet Kaur',
        'visual_intent': 'person portrait',
    }
    grounded = apply_grounding(scene, _story())
    assert grounded['primary_entity'] == 'Harmanpreet Kaur'
    assert grounded['visual_entity_grounded'] is True

def test_manual_visual_query_is_never_rewritten():
    scene = {
        'primary_entity': 'India women team',
        'manual_visual_query': 'Rashid Khan',
        'visual_intent': 'person portrait',
    }
    grounded = apply_grounding(scene, _story())
    assert grounded['primary_entity'] == 'India women team'
    assert grounded['manual_visual_query'] == 'Rashid Khan'
    assert grounded['visual_entity_grounding'] == 'MANUAL_LOCK'

def test_manual_query_remains_the_search_identity():
    intent = resolve_visual_search_intent({
        'primary_entity': 'India women team',
        'manual_visual_query': 'Rashid Khan',
        'visual_intent': 'person portrait',
    })
    assert intent.manual is True
    assert intent.query == 'Rashid Khan'
    assert intent.subject == 'Rashid Khan'

def test_contextual_general_visual_can_remain_ungrounded_by_name():
    scene = {
        'primary_entity': 'batting statistics',
        'visual_intent': 'chart graph',
    }
    grounded = ground_scene_entity(scene, _story())
    assert grounded['grounded'] is True

def test_script_layer_repairs_hallucinated_visual_identity_before_retrieval():
    script_data = {
        'title': "India women's team win the T20 World Cup",
        'script': [{
            'voiceover': "India's women's team lifted the trophy after a memorable campaign and secured the title.",
            'primary_entity': 'Rashid Khan',
            'visual_intent': 'person portrait',
            'specific_search_prompt': 'Rashid Khan bowling celebration',
        }],
    }
    cleaned, diagnostics = clean_script_data(script_data, _story(), 'regular')
    scene = cleaned['script'][0]
    assert scene['primary_entity'] == "India women's team"
    assert scene['specific_search_prompt'] == "India women's team"
    assert diagnostics['visual_entity_grounding_changes'] == 1



def test_person_query_drops_action_clause_from_identity():
    scene = {
        "primary_entity": "Gautam Gambhir Makes Stunning",
        "specific_search_prompt": "Gautam Gambhir Makes Stunning stadium",
        "visual_intent": "person action at a press conference",
    }

    intent = resolve_visual_search_intent(scene, "Gautam Gambhir press conference")

    assert intent.subject == "Gautam Gambhir"
    assert intent.query.startswith("Gautam Gambhir")
    assert "makes stunning" not in intent.query.casefold()





def test_publisher_domain_does_not_ground_visual_identity_or_enter_retrieval():
    story = {
        "title": "BCCI confirms India selection",
        "research_sources": [
            {
                "title": "BCCI confirms India selection",
                "snippet": "India selection confirmed.",
                "source": "India.com",
            }
        ],
    }
    scene = {
        "primary_entity": "India India.com",
        "visual_type": "PERSON",
        "visual_intent": "person portrait",
        "specific_search_prompt": "India India.com portrait",
    }

    grounded = ground_scene_entity(scene, story)
    assert grounded["grounded"] is True
    assert grounded["changed"] is True
    assert grounded["entity"] == "BCCI"

    prepared = apply_grounding(scene, story)
    assert prepared["visual_entity_grounded"] is True
    assert prepared["primary_entity"] == "BCCI"
    assert prepared["factual_primary_entity"] == "BCCI"
    assert prepared["visual_search_subject"] == "BCCI"
    assert prepared["specific_search_prompt"] == "BCCI"
    assert "India.com" not in prepared["primary_entity"]


def test_publisher_domain_contamination_is_repaired_before_visual_querying():
    from visual_search_intent_runtime import resolve_visual_search_intent

    story = {
        "title": "BCCI confirms India selection",
        "research_bundle": "Read more about the selection at India.com.",
        "research_sources": [
            {
                "title": "BCCI confirms India selection",
                "snippet": "India selection confirmed.",
                "source": "India.com",
            }
        ],
    }
    scene = {
        "primary_entity": "India India.com",
        "visual_intent": "person portrait",
        "specific_search_prompt": "India India.com portrait",
    }

    grounded = apply_grounding(scene, story)

    assert grounded["primary_entity"] == "BCCI"
    assert grounded["factual_primary_entity"] == "BCCI"
    assert grounded["visual_search_subject"] == "BCCI"
    assert grounded["specific_search_prompt"] == "BCCI"

    intent = resolve_visual_search_intent(grounded, story["title"])
    assert intent.subject == "BCCI"
    assert "India.com" not in " ".join(intent.queries)


def test_publisher_name_does_not_become_automatic_visual_subject():
    story = {
        "title": "India selection confirmed",
        "research_sources": [
            {
                "title": "India selection confirmed",
                "source": "The Indian Express",
            }
        ],
    }
    scene = {
        "primary_entity": "The Indian Express",
        "visual_intent": "India selection update",
        "specific_search_prompt": "The Indian Express",
    }

    grounded = apply_grounding(scene, story)

    assert grounded["visual_entity_grounded"] is False
    assert grounded["primary_entity"] == ""
    assert grounded["visual_search_subject"] == ""


def test_match_query_uses_concrete_format_anchor():
    scene = {
        "primary_entity": "India vs England Cricket",
        "visual_intent": "India vs England ODI match",
        "specific_search_prompt": "India vs England Cricket",
        "voiceover": "India and England face each other in an ODI.",
    }
    intent = resolve_visual_search_intent(scene, "India vs England ODI")

    assert intent.queries
    assert any("odi" in query.casefold() for query in intent.queries)
    assert all("scene" not in query.casefold() for query in intent.queries)


def test_headline_action_fragment_is_never_used_as_an_entity_anchor():
    from visual_entity_grounding_runtime import _anchors

    story = {
        "title": "India To Play Historic Friendly Against Japan",
        "research_bundle": "India's men's cricket team will play Japan in a friendly.",
    }

    anchors = _anchors(story)

    assert "India To Play Historic" not in anchors


def test_scene_voiceover_can_support_a_valid_visual_entity():
    story = {
        "title": "India To Play Historic Friendly Against Japan",
        "research_bundle": "The teams are preparing for the fixture.",
    }
    scene = {
        "primary_entity": "Ministry of Youth Affairs and Sports",
        "visual_intent": "organisation building",
        "voiceover": "The Ministry of Youth Affairs and Sports confirmed the team's participation.",
    }

    grounded = apply_grounding(scene, story)

    assert grounded["primary_entity"] == "Ministry of Youth Affairs and Sports"
    assert grounded["visual_entity_grounded"] is True
    assert grounded["visual_entity_grounding_confidence"] >= 0.80


def test_headline_title_alone_cannot_ground_a_fake_visual_entity():
    story = {
        "title": "India To Play Historic Friendly Against Japan",
        "research_bundle": "India's men's cricket team will play Japan in a friendly.",
    }
    scene = {
        "primary_entity": "India To Play Historic",
        "visual_intent": "person portrait",
        "voiceover": "The match will bring the two teams together.",
    }

    grounded = ground_scene_entity(scene, story)

    assert grounded["grounded"] is False
    assert grounded["changed"] is False
