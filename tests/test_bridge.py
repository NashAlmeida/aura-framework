"""Basic bridge tests (Phase 1)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    # Context manager runs the FastAPI lifespan (loads tool_registry).
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json().get("status") == "ok"


def test_list_tools(client: TestClient) -> None:
    response = client.get("/tools")
    assert response.status_code == 200
    body = response.json()
    assert "tools" in body
    assert isinstance(body["tools"], list)


def test_run_tool_not_found(client: TestClient) -> None:
    response = client.post(
        "/tools/run",
        json={"tool_name": "no_such_tool", "arguments": {}},
    )
    assert response.status_code == 404


def test_run_tool_rejects_path_traversal(client: TestClient) -> None:
    response = client.post(
        "/tools/run",
        json={"tool_name": "../secrets", "arguments": {}},
    )
    assert response.status_code == 400


def _inject_open_url() -> None:
    registry = app.state.tool_registry
    registry._tools = [
        {
            "name": "open_url",
            "description": "Open a URL",
            "source": "actions/browser_control.py",
            "parameters": {},
        }
    ]
    registry._loaded = True


def test_run_tool_simulated_success(client: TestClient) -> None:
    _inject_open_url()

    response = client.post(
        "/tools/run",
        json={
            "tool_name": "open_url",
            "arguments": {"url": "https://example.com"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["tool_name"] == "open_url"
    assert "simulated execution" in body["message"]
    assert body["result"]["arguments"]["url"] == "https://example.com"


def test_websocket_welcome_and_tool_events(client: TestClient) -> None:
    _inject_open_url()

    with client.websocket_connect("/ws") as ws:
        welcome = ws.receive_json()
        assert welcome["type"] == "connected"
        assert "Bridge WebSocket ready" in welcome["message"]

        response = client.post(
            "/tools/run",
            json={"tool_name": "open_url", "arguments": {"url": "https://example.com"}},
        )
        assert response.status_code == 200

        started = ws.receive_json()
        assert started["type"] == "tool_started"
        assert started["tool_name"] == "open_url"
        assert "timestamp" in started

        finished = ws.receive_json()
        assert finished["type"] == "tool_finished"
        assert finished["tool_name"] == "open_url"
        assert finished["success"] is True
        assert "timestamp" in finished

