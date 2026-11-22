import io
import json
import time
from datetime import datetime, timedelta

import pytest

from conftest import fake_reply


# ---------------------------------------------------------------- login

def test_health_is_public(client):
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("method,path", [("get", "/sessions"), ("delete", "/sessions/x"), ("post", "/chat"),
                                         ("post", "/generate-plan"), ("post", "/progression"), ("get", "/job-status/x")])
def test_routes_need_login(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_login(client):
    assert client.post("/login", json={"password": "wrong"}).status_code == 401
    token = client.post("/login", json={"password": "test-password"}).get_json()["token"]
    assert client.get("/sessions", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert client.get("/sessions", headers={"Authorization": f"Bearer {token}x"}).status_code == 401


def test_login_lockout(client):
    for _ in range(10):
        assert client.post("/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/login", json={"password": "test-password"}).status_code == 429


def test_cors_preflight_is_allowed(client):
    response = client.options("/sessions", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 200


# ---------------------------------------------------------------- sessions

def test_session_crud(client, auth_headers):
    created = client.post("/sessions", headers=auth_headers, json={
        "session_date": "2026-02-05T10:43", "metrics": {"session_overview": {"ski_iq_range": {"average": 0}}}})
    assert created.status_code == 201
    session = created.get_json()
    assert session["ski_iq"] == 0  # a score of 0 is kept, not treated as missing
    assert session["session_date"] == "2026-02-05T10:43:00"  # local time stored as given

    assert client.put(f"/sessions/{session['id']}", headers=auth_headers).status_code == 400  # no body
    updated = client.put(f"/sessions/{session['id']}", headers=auth_headers, json={
        "metrics": {"session_overview": {"ski_iq_range": {"average": 151}}, "carv_metrics": {"edge_angle": {"value": 68}}}})
    assert updated.get_json()["ski_iq"] == 151
    assert updated.get_json()["metrics"]["overall_metrics"]["edging"]["edge_angle"] == 68

    assert client.delete(f"/sessions/{session['id']}", headers=auth_headers).status_code == 200
    assert client.get(f"/sessions/{session['id']}", headers=auth_headers).status_code == 404


# ---------------------------------------------------------------- model replies

def test_json_reply_retries_once(app_module):
    replies = iter([fake_reply("not json"), fake_reply('{"ok": true}')])
    assert app_module.request_json_reply(lambda: next(replies)) == {"ok": True}


def test_json_reply_gives_up_after_two_attempts(app_module):
    with pytest.raises(app_module.ModelReplyError):
        app_module.request_json_reply(lambda: fake_reply("still not json"))


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_json_reply_stops_on_refusal_or_cut_off(app_module, stop_reason):
    calls = []

    def create():
        calls.append(1)
        return fake_reply('{"ok": true}', stop_reason=stop_reason)

    with pytest.raises(app_module.ModelReplyError):
        app_module.request_json_reply(create)
    assert len(calls) == 1


def test_reply_text_skips_thinking_blocks(app_module):
    assert app_module.get_reply_text(fake_reply("hello")) == "hello"


def test_clean_json_response(app_module):
    assert json.loads(app_module.clean_json_response('```json\n{"a": 1}\n```')) == {"a": 1}
    assert json.loads(app_module.clean_json_response('Here you go: {"a": {"b": 2}} done')) == {"a": {"b": 2}}


# ---------------------------------------------------------------- analysis jobs

def png_bytes():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (20, 40), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def wait_for_job(client, headers, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/job-status/{job_id}", headers=headers).get_json()
        if status["status"] in ("completed", "failed"):
            return status
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_analysis_job_completes(client, auth_headers, app_module, monkeypatch):
    reply = json.dumps({"session_overview": {"session_datetime": "2026-02-05T10:43:00", "ski_iq_range": {"average": 154}},
                        "carv_metrics": {"edge_angle": {"value": 65, "zone": "in"}}})
    monkeypatch.setattr(app_module, "create_analysis_message", lambda **kw: fake_reply(reply))
    started = client.post("/analyze-async", headers=auth_headers,
                          data={"images": (io.BytesIO(png_bytes()), "run.png")}, content_type="multipart/form-data")
    assert started.status_code == 200
    result = wait_for_job(client, auth_headers, started.get_json()["job_id"])
    assert result["status"] == "completed"
    assert result["result"]["overall_metrics"]["edging"]["edge_angle"] == 65
    assert result["result"]["session_overview"]["ski_iq_version"] == "wasatch"


def test_analysis_job_reports_refusal(client, auth_headers, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "create_analysis_message", lambda **kw: fake_reply(stop_reason="refusal"))
    started = client.post("/analyze-async", headers=auth_headers,
                          data={"images": (io.BytesIO(png_bytes()), "run.png")}, content_type="multipart/form-data")
    result = wait_for_job(client, auth_headers, started.get_json()["job_id"])
    assert result["status"] == "failed" and "declined" in result["error"]


def test_too_many_files(client, auth_headers):
    files = [(io.BytesIO(b"x"), f"{i}.png") for i in range(11)]
    response = client.post("/analyze-async", headers=auth_headers, data={"images": files}, content_type="multipart/form-data")
    assert response.status_code == 400


def test_lost_and_unknown_jobs(client, auth_headers, app_module):
    with app_module.app.app_context():
        stale = app_module.Job(id="stale-job", status="processing")
        app_module.db.session.add(stale)
        app_module.db.session.commit()
        stale.updated_at = datetime.utcnow() - timedelta(minutes=30)
        app_module.db.session.commit()
    result = client.get("/job-status/stale-job", headers=auth_headers).get_json()
    assert result["status"] == "failed" and "restarted" in result["error"]
    assert client.get("/job-status/nope", headers=auth_headers).status_code == 404
