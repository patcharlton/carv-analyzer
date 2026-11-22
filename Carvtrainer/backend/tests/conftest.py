"""
Test setup: a throwaway SQLite database, test login settings and a dummy API key,
set before the app module is imported. No test calls the real Anthropic API.

Run from Carvtrainer/backend:  python -m pytest
"""

import os
import sys
import tempfile
import types

import pytest

_db_dir = tempfile.mkdtemp(prefix="carv-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_db_dir, 'test.db')}"
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
os.environ["APP_PASSWORD"] = "test-password"
os.environ["SECRET_KEY"] = "test-secret-key-for-signing-tokens"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import main  # noqa: E402


@pytest.fixture
def app_module():
    return main


@pytest.fixture
def client():
    main._failed_logins.clear()
    return main.app.test_client()


@pytest.fixture
def auth_headers(client):
    response = client.post("/login", json={"password": "test-password"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.get_json()['token']}"}


@pytest.fixture(autouse=True)
def clean_database():
    yield
    with main.app.app_context():
        main.Session.query.delete()
        main.Job.query.delete()
        main.db.session.commit()


def fake_reply(text=None, stop_reason="end_turn", tool_input=None):
    """A stand-in for an Anthropic Message: an empty thinking block first, as newer models send."""
    content = [types.SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(types.SimpleNamespace(type="text", text=text))
    if tool_input is not None:
        content.append(types.SimpleNamespace(type="tool_use", input=tool_input))
    usage = types.SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return types.SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)
