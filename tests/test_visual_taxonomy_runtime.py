from visual_taxonomy_runtime import (
    VISUAL_GENRES,
    classify_visual_genre,
    genre_allows_ai,
    preferred_sources,
)


def test_visual_taxonomy_covers_core_factory_genres():
    assert len(VISUAL_GENRES) >= 30
    for genre in (
        "PERSON_PORTRAIT",
        "ORG_BRANDING",
        "PRODUCT_PHOTO",
        "VEHICLE",
        "ANIMAL",
        "FOOD",
        "LANDMARK",
        "SPORTS_ACTION",
        "SPORTS_MATCH",
        "TROPHY_AWARD",
        "DOCUMENT",
        "SCREENSHOT_UI",
        "CHART_GRAPH",
        "MAP",
        "DIAGRAM",
        "PROCESS",
        "SCIENCE_VISUAL",
        "SPACE_VISUAL",
        "NATURE_LANDSCAPE",
        "HISTORICAL_ARTIFACT",
        "MEDIA_ARTWORK",
        "MONEY_CURRENCY",
        "FLAG_SYMBOL",
    ):
        assert genre in VISUAL_GENRES
        assert preferred_sources(genre)


def test_visual_taxonomy_resolves_specific_assets_before_broad_roles():
    assert classify_visual_genre(
        {"primary_entity": "BCCI logo", "visual_intent": "official logo"},
        "BCCI logo",
        "ORGANIZATION",
    ) == "ORG_BRANDING"

    assert classify_visual_genre(
        {"primary_entity": "Sanju Samson", "visual_intent": "batting action"},
        "Sanju Samson",
        "PERSON",
    ) == "PERSON_ACTION"

    assert classify_visual_genre(
        {"primary_entity": "Virat Kohli statistics", "visual_intent": "batting average chart"},
        "Virat Kohli statistics",
        "GENERAL_CONTEXT",
    ) == "CHART_GRAPH"

    assert classify_visual_genre(
        {"primary_entity": "Mars", "visual_intent": "planet surface image"},
        "Mars",
        "LOCATION",
    ) == "SPACE_VISUAL"


def test_person_press_conference_is_action_while_explicit_portrait_stays_portrait():
    assert classify_visual_genre(
        {
            "primary_entity": "Rishabh Pant",
            "visual_intent": "press conference person",
            "visual_context": "announcement at a media briefing",
        },
        "Rishabh Pant",
        "PERSON",
    ) == "PERSON_ACTION"

    assert classify_visual_genre(
        {
            "primary_entity": "Rishabh Pant",
            "visual_intent": "person portrait",
            "visual_context": "press conference appearance",
        },
        "Rishabh Pant",
        "PERSON",
    ) == "PERSON_PORTRAIT"


def test_visual_taxonomy_distinguishes_real_photo_and_explanatory_visuals():
    assert classify_visual_genre(
        {"primary_entity": "quantum entanglement", "visual_intent": "scientific concept"},
        "quantum entanglement",
        "CONCEPT",
    ) == "SCIENCE_VISUAL"
    assert classify_visual_genre(
        {"primary_entity": "how a battery works", "visual_intent": "process diagram"},
        "how a battery works",
        "PROCESS",
    ) == "DIAGRAM"
    assert genre_allows_ai("SCIENCE_VISUAL")
    assert not genre_allows_ai("PERSON_PORTRAIT")


def test_visual_genre_is_propagated_in_search_intent():
    from visual_search_intent_runtime import resolve_visual_search_intent

    intent = resolve_visual_search_intent(
        {
            "primary_entity": "BCCI logo",
            "visual_intent": "official logo",
        }
    )
    assert intent.visual_genre == "ORG_BRANDING"


def test_team_entities_never_default_to_headquarters():
    assert classify_visual_genre(
        {
            "primary_entity": "India Men's Cricket Team",
            "visual_intent": "team identity",
            "voiceover": "India Men's Cricket Team is preparing for the tournament.",
        },
        "India Men's Cricket Team",
        "ORGANIZATION",
    ) == "GENERAL_PHOTO"

    assert classify_visual_genre(
        {
            "primary_entity": "India Men's Cricket Team",
            "visual_intent": "team jersey",
            "voiceover": "India Men's Cricket Team jersey is the focus.",
        },
        "India Men's Cricket Team",
        "ORGANIZATION",
    ) == "TEAM_BRANDING"


def test_real_organisation_headquarters_still_requires_explicit_location_context():
    assert classify_visual_genre(
        {
            "primary_entity": "BCCI",
            "visual_intent": "headquarters exterior",
            "voiceover": "The BCCI headquarters is shown.",
        },
        "BCCI",
        "ORGANIZATION",
    ) == "ORG_HEADQUARTERS"
