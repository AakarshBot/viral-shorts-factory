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
