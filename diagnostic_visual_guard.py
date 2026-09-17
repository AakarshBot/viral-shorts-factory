from __future__ import annotations


def run_offline_visual_regressions() -> list[str]:
    from visual_strategy_runtime import classify_scene

    checks = []
    cases = [
        ({"primary_entity": "Reserve Bank of India", "visual_type": "GENERAL_CONTEXT"}, "ORGANIZATION"),
        ({"primary_entity": "RBI", "visual_type": "GENERAL_CONTEXT"}, "ORGANIZATION"),
        ({"primary_entity": "BCCI", "visual_type": "GENERAL_CONTEXT"}, "ORGANIZATION"),
        ({"primary_entity": "Delhi", "visual_type": "GENERAL_CONTEXT"}, "LOCATION"),
        ({"primary_entity": "Lionel Messi", "visual_type": "PERSON"}, "PERSON"),
    ]
    for scene, expected in cases:
        actual = classify_scene(scene, "news")
        if actual != expected:
            raise AssertionError(f"{scene['primary_entity']} classified as {actual}, expected {expected}")
        checks.append(f"{scene['primary_entity']} -> {actual}")
    return checks
