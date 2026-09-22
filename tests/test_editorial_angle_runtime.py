from script_runtime import choose_editorial_angle


def test_editorial_angle_prefers_documented_confrontation():
    story = {
        "title": "Former batter criticizes India after rivalry clash",
        "description": "The former batter called India's approach arrogant and the response triggered a fresh dispute.",
        "event_actions": ["comment"],
        "event_entities": ["Former batter", "India"],
    }
    result = choose_editorial_angle(story, "regular")
    assert result["type"] == "confrontation_led"


def test_editorial_angle_prefers_verified_result_or_record():
    story = {
        "title": "India clinches its 200th T20I win after two-run thriller",
        "description": "India became the first side to reach 200 wins in the format after defeating Japan by two runs.",
        "event_actions": ["win"],
        "event_entities": ["India", "Japan"],
    }
    result = choose_editorial_angle(story, "regular")
    assert result["type"] == "result_or_record_led"


def test_editorial_angle_prefers_comparison_when_two_sides_are_explicit():
    story = {
        "title": "India vs Australia: the numbers that separate the two sides",
        "description": "The latest comparison shows India ahead in one metric while Australia leads another.",
        "event_entities": ["India", "Australia"],
    }
    result = choose_editorial_angle(story, "regular")
    assert result["type"] == "comparison_led"


def test_editorial_angle_falls_back_to_consequence_lens_for_concrete_changes():
    story = {
        "title": "Star player ruled out with injury ahead of the semifinal",
        "description": "The injury removes the player from the semifinal squad and changes the available selection options.",
        "event_actions": ["injure"],
        "event_entities": ["Star player"],
    }
    result = choose_editorial_angle(story, "regular")
    assert result["type"] == "why_it_matters_led"
