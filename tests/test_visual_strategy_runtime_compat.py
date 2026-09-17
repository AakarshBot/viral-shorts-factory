def test_visual_strategy_private_compatibility_helpers():
    import visual_retrieval_planner as planner
    import visual_strategy_runtime as runtime

    for name in ("_clean", "_normalise", "_normalise_query", "_add", "_acceptable", "_add_unique"):
        assert hasattr(runtime, name), f"visual strategy compatibility helper missing: {name}"

    assert runtime._normalise_query("latest Lionel Messi news") == planner._normalise("latest Lionel Messi news")

    queries = []
    assert runtime._add_unique(queries, "Lionel Messi") is True
    assert runtime._add_unique(queries, "  Lionel Messi  ") is False
    assert runtime._add_unique(queries, "World Cup final") is True
    assert queries == ["Lionel Messi", "World Cup final"]
