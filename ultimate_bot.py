
        if duration_estimate["seconds"] > 30.0:
            print(
                "   [Script Duration] Over 30s; performing exactly one lightweight compression pass "
                "on the validated draft (no research/provider-chain rerun).",
                flush=True,
            )
            rewritten = tighten_script_for_duration_once(
                script_data,
                story_payload,
                lang_cfg,
                format_mode,
                target_seconds=30.0,
                persona_profile=persona_profile,
            )
            if not rewritten:
                if duration_estimate["seconds"] <= 35.0:
                    print(
                        "   [Script Duration] Compression pass unavailable or rejected; retaining the "
                        "already-validated draft within the 35s soft maximum.",
                        flush=True,
                    )