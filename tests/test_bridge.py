"""Basic bridge + agent tests (Phase 1 & 2)."""

import pytest
from fastapi.testclient import TestClient

from app.agent.graph import clear_graph_cache
from app.agent.memory import memory_store
from app.main import app


@pytest.fixture()
def client():
    # Context manager runs the FastAPI lifespan (loads tool_registry).
    clear_graph_cache()
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


def _inject_tools(*specs: dict) -> None:
    clear_graph_cache()
    registry = app.state.tool_registry
    registry._tools = list(specs)
    registry._loaded = True


def _inject_open_url() -> None:
    _inject_tools(
        {
            "name": "open_url",
            "description": "Open a URL",
            "source": "actions/browser_control.py",
            "parameters": {
                "type": "OBJECT",
                "properties": {"url": {"type": "STRING"}},
            },
        }
    )


def _inject_open_and_delete() -> None:
    _inject_tools(
        {
            "name": "open_url",
            "description": "Open a URL",
            "source": "actions/browser_control.py",
            "parameters": {
                "type": "OBJECT",
                "properties": {"url": {"type": "STRING"}},
            },
        },
        {
            "name": "delete_file",
            "description": "Delete a file from disk",
            "source": "actions/file_controller.py",
            "parameters": {
                "type": "OBJECT",
                "properties": {"path": {"type": "STRING"}},
            },
        },
    )


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


def test_agent_run_demo(client: TestClient) -> None:
    _inject_open_url()
    response = client.post(
        "/agent/run",
        json={"message": "open chrome and search for python tutorials"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["session_id"]
    assert isinstance(body["tool_calls"], list)
    assert any(tc["tool_name"] == "open_url" for tc in body["tool_calls"])


def test_agent_multi_step_tool_use(client: TestClient) -> None:
    """Safe multi-step plan: open_url then (after confirm) delete_file — first step alone also counts."""
    _inject_open_and_delete()
    # Message with only the safe first intent should complete open_url as step 1 of a longer plan
    # when both intents are present — pause on delete.
    response = client.post(
        "/agent/run",
        json={
            "message": "open chrome and search for python tutorials then delete file temp.txt",
        },
    )
    assert response.status_code == 200
    body = response.json()
    # After safe open_url, risky delete_file should pause for confirmation.
    assert body["status"] == "needs_confirmation"
    assert body["tool_name"] == "delete_file"
    assert body["arguments"].get("path") == "temp.txt"
    assert any(tc["tool_name"] == "open_url" for tc in body["tool_calls"])


def test_agent_risky_tool_confirmation_and_continue(client: TestClient) -> None:
    _inject_open_and_delete()
    session = "test-confirm-session"
    memory_store.clear(session)

    first = client.post(
        "/agent/run",
        json={
            "message": "delete file secrets.txt",
            "session_id": session,
        },
    )
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "needs_confirmation"
    assert body["tool_name"] == "delete_file"
    assert body["arguments"].get("path") == "secrets.txt"
    assert "risky" in (body.get("message") or "").lower()
    assert body["session_id"] == session

    # Short-term memory kept the user turn across the pause.
    history = memory_store.get_history(session)
    assert any(t["role"] == "user" for t in history)

    second = client.post(
        "/agent/run",
        json={"session_id": session, "confirmed": True},
    )
    assert second.status_code == 200
    done = second.json()
    assert done["status"] == "completed"
    assert done["session_id"] == session
    assert any(tc["tool_name"] == "delete_file" for tc in done["tool_calls"])
    delete_results = [
        tc for tc in done["tool_calls"] if tc["tool_name"] == "delete_file"
    ]
    assert delete_results
    assert delete_results[0]["result"].get("success") is True


def test_agent_risky_tool_rejection(client: TestClient) -> None:
    _inject_open_and_delete()
    session = "test-reject-session"
    memory_store.clear(session)

    first = client.post(
        "/agent/run",
        json={"message": "delete file temp.txt", "session_id": session},
    )
    assert first.json()["status"] == "needs_confirmation"

    second = client.post(
        "/agent/run",
        json={"session_id": session, "confirmed": False},
    )
    assert second.status_code == 200
    done = second.json()
    assert done["status"] == "completed"
    delete_results = [
        tc for tc in done["tool_calls"] if tc["tool_name"] == "delete_file"
    ]
    assert delete_results
    assert delete_results[0]["result"].get("cancelled") is True


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
