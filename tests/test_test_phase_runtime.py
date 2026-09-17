def test_test_phase_module_imports():
    import test_phase_runtime

    assert test_phase_runtime.TEST_STEPS == (
        ("topic", "1. Topic choosing"),
        ("script", "2. Script writing"),
        ("audio", "3. Audio"),
        ("visuals", "4. Visual sourcing"),
        ("render", "5. Subs / overlays"),
        ("metadata", "6. Title / description"),
        ("upload", "7. Upload"),
    )
