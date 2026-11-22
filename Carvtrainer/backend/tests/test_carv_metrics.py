from app.carv_metrics import default_ski_iq_version, describe_metrics, normalise_analysis


def feb_5_run():
    """Values from a real Carv run (5 Feb 2026, Les Houches)."""
    return {
        "session_overview": {"session_datetime": "2026-02-05T10:43:00", "ski_iq_range": {"average": 154}},
        "carv_metrics": {
            "parallel_skis": {"value": 79, "zone": "below"},
            "turn_shape": {"value": "75%", "zone": "in"},
            "turn_closure": {"value": 56, "zone": "below"},
            "early_edging": {"value": 86},
            "edging_smoothness": {"value": 87, "zone": "above"},
            "edging_similarity": {"value": 90},
            "edge_build": {"value": 100},
            "edge_angle": {"value": 65},
            "weight_release": {"value": 73},
            "g_force": {"value": 2.0, "zone": "sideways"},
            "slope_pitch": {"value": 23},
            "start_of_turn": {"value": 50},
        },
        "run_details": {"technique": "carving", "tempo_tpm": 39,
                        "skill_highlights": [{"number": 4, "name": "Smoothly linking turns", "status": "One to work on"}]},
    }


def test_units_and_unknown_keys():
    analysis = normalise_analysis(feb_5_run())
    carv = analysis["carv_metrics"]
    assert carv["turn_shape"] == {"value": 75.0, "unit": "%", "zone": "in"}
    assert carv["edge_angle"]["unit"] == "°"
    assert carv["g_force"] == {"value": 2.0, "unit": "g", "zone": None}  # invalid zone dropped
    assert "start_of_turn" not in carv  # Carv no longer shows it


def test_legacy_fields_filled_and_averages_only_use_percentages():
    overall = normalise_analysis(feb_5_run())["overall_metrics"]
    assert overall["edging"]["progressive_edge_build"] == 100
    assert overall["balance"]["transition_weight_release"] == 73
    assert overall["performance"]["turn_g_force"] == 2.0
    # Edge angle (degrees) is not averaged with percentages
    assert overall["edging"]["category_average"] == round((86 + 87 + 90 + 100) / 4, 1)
    assert overall["performance"]["category_average"] is None


def test_old_sessions_keep_their_fields():
    old = {"overall_metrics": {"balance": {"start_of_turn": 70, "centered_balance": 80, "transition_weight_release": 60}}}
    normalise_analysis(old)
    assert old["overall_metrics"]["balance"]["category_average"] == 70.0
    assert "carv_metrics" not in old


def test_ski_iq_version():
    assert normalise_analysis(feb_5_run())["session_overview"]["ski_iq_version"] == "wasatch"
    assert default_ski_iq_version("2026-11-09T08:00") == "stelvio"
    assert default_ski_iq_version("2026-11-08") == "wasatch"
    assert default_ski_iq_version(None) is None
    explicit = feb_5_run()
    explicit["session_overview"]["ski_iq_version"] = "stelvio"
    assert normalise_analysis(explicit)["session_overview"]["ski_iq_version"] == "stelvio"


def test_describe_metrics_uses_units_and_zones():
    text = describe_metrics(normalise_analysis(feb_5_run()))
    assert "Edge Angle: 65°" in text
    assert "Turn Closure: 56%, below performance zone" in text
    assert "Skill 4 Smoothly linking turns = One to work on" in text
