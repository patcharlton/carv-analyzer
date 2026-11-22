import json
import re

import pytest

from app.drills import DRILLS, SELECTION_GUIDE
from app.training_plan import PLAN_TOOL, PlanError, assemble_plan
from conftest import fake_reply


def plan_input(drill_ids=("patience_turns", "crossover_focus", "variable_radius_carving")):
    drill = lambda drill_id, role: {
        "drill_id": drill_id, "role": role, "target_metric": "Turn Closure", "why": "Below its zone",
        "steps": ["a", "b", "c", "d"], "focused_turns": 6, "runs": 2,
        "success_feels_like": "x", "mental_cue": "Finish the arc", "common_mistakes": [],
    }
    return {
        "summary": "s", "level": "High Performance", "biggest_limiter": "Linking turns",
        "immediate_focus": {"issue": "i", "drill_id": drill_ids[0], "what_success_feels_like": "w", "cue": "c"},
        "drills": [drill(d, r) for d, r in zip(drill_ids, ("primary", "secondary", "integration"))],
        "priorities": [{"metric": "Turn Closure", "current": "56%", "target": "into the zone", "drill_id": drill_ids[0]}],
        "strengths": [], "progression": {"weeks_1_2": [], "weeks_3_4": []}, "checkpoints": [],
        "mental_cues": {"primary": "p", "transition": "t", "confidence": "c"}, "traps": [],
    }


def test_library_is_complete():
    assert len(DRILLS) == 36
    for drill_id, drill in DRILLS.items():
        assert drill["name"] and drill["video_url"].startswith("https://"), drill_id
        assert set(drill["techniques"]) <= {"carving", "short turns", "parallel"}


def test_selection_guide_only_names_real_drills():
    named = re.findall(r"\b([a-z0-9]+(?:_[a-z0-9]+)+)\b", SELECTION_GUIDE)
    assert named and all(name in DRILLS for name in named)


def test_schema_only_allows_library_drills():
    enum = PLAN_TOOL["input_schema"]["properties"]["drills"]["items"]["properties"]["drill_id"]["enum"]
    assert enum == list(DRILLS)


def test_assemble_merges_fixed_drill_details():
    plan = assemble_plan(plan_input(), ski_iq=154, days_available=3, runs_per_day=12, generated_at="now")
    assert plan["format"] == "carv-plan-v1"
    assert [d["reference"]["name"] for d in plan["drills"]] == ["Patience Turns", "Crossover Focus", "Variable Radius Carving"]
    assert plan["drills"][0]["reference"]["video_url"] == DRILLS["patience_turns"]["video_url"]
    assert plan["priorities"][0]["drill_name"] == "Patience Turns"
    assert (plan["days_available"], plan["runs_per_day"]) == (3, 12)


def test_assemble_rejects_unknown_drills():
    with pytest.raises(PlanError):
        assemble_plan(plan_input(("patience_turns", "crossover_focus", "made_up")), ski_iq=None,
                      days_available=5, runs_per_day=10, generated_at="now")
    with pytest.raises(PlanError):
        assemble_plan(None, ski_iq=None, days_available=5, runs_per_day=10, generated_at="now")


def test_generate_plan_route(client, auth_headers, app_module, monkeypatch):
    calls = {}

    def fake_create(**kwargs):
        calls.update(kwargs)
        return fake_reply(stop_reason="tool_use", tool_input=plan_input())

    monkeypatch.setattr(app_module.client.messages, "create", fake_create)
    analysis = {"session_overview": {"session_datetime": "2026-02-05T10:43:00", "ski_iq_range": {"average": 154}},
                "carv_metrics": {"turn_closure": {"value": 56, "zone": "below"}}}
    response = client.post("/generate-plan", json={**analysis, "days_available": 99, "runs_per_day": "12"}, headers=auth_headers)
    assert response.status_code == 200
    plan = response.get_json()["training_plan"]
    assert plan["days_available"] == 7  # clamped
    assert plan["runs_per_day"] == 12
    assert calls["tool_choice"] == {"type": "tool", "name": "build_training_plan"}
    assert calls["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_generate_plan_needs_an_analysis(client, auth_headers):
    assert client.post("/generate-plan", json={}, headers=auth_headers).status_code == 400


def test_generate_plan_cut_off(client, auth_headers, app_module, monkeypatch):
    monkeypatch.setattr(app_module.client.messages, "create", lambda **kw: fake_reply(stop_reason="max_tokens"))
    response = client.post("/generate-plan", json={"carv_metrics": {"edge_angle": {"value": 65}}}, headers=auth_headers)
    assert response.status_code == 502


def test_saved_structured_plan_feeds_history(app_module):
    from app.training_plan import history_entry
    plan = assemble_plan(plan_input(), ski_iq=154, days_available=5, runs_per_day=10, generated_at="now")
    session = app_module.Session(session_date=app_module.datetime(2026, 2, 5, 10, 43), ski_iq=154,
                                 metrics={}, training_plan=json.dumps(plan))
    entry = history_entry(session)
    assert [d["drill_id"] for d in entry["previous_drills"]] == ["patience_turns", "crossover_focus", "variable_radius_carving"]
    old = app_module.Session(session_date=app_module.datetime(2026, 2, 2), metrics={}, training_plan="# Old markdown plan")
    assert "previous_drills" not in history_entry(old)


def test_drill_log_reaches_progression(client, auth_headers, app_module, monkeypatch):
    plan = assemble_plan(plan_input(), ski_iq=151, days_available=5, runs_per_day=10, generated_at="now")
    plan["drill_log"] = {"patience_turns": {"done": True, "runs_done": 3, "felt": 3, "note": "Finally finishing turns"}}
    for date, iq, stored_plan in (("2026-02-02T10:23", 151, json.dumps(plan)), ("2026-02-05T10:43", 154, None)):
        client.post("/sessions", headers=auth_headers, json={
            "session_date": date, "training_plan": stored_plan,
            "metrics": {"session_overview": {"ski_iq_range": {"average": iq}}, "carv_metrics": {"turn_closure": {"value": 50}}}})
    sent = {}

    def fake_create(**kwargs):
        sent.update(kwargs)
        return fake_reply('{"summary": "ok"}')

    monkeypatch.setattr(app_module.client.messages, "create", fake_create)
    response = client.post("/progression", headers=auth_headers, json={})
    assert response.status_code == 200
    prompt = sent["messages"][0]["content"]
    assert '"felt": "clicked"' in prompt and "Finally finishing turns" in prompt
