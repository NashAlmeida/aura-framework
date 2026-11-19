"""Basic bridge + agent tests (Phase 1 & 2)."""

import json

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


def test_tracing_disabled_without_keys(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    from app.agent.tracing import tracing_enabled

    assert tracing_enabled() is False


def test_summary_leads_with_the_answer() -> None:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from app.agent.graph import _summarize_run

    weather = [
        HumanMessage(content="what's the weather in Mumbai"),
        AIMessage(
            content="step",
            tool_calls=[
                {
                    "name": "weather_report",
                    "args": {"city": "Mumbai"},
                    "id": "w1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "success": True,
                    "result": {
                        "simulated": False,
                        "output": "Showing the weather for Mumbai, today, sir.",
                    },
                    "message": "real execution",
                }
            ),
            tool_call_id="w1",
            name="weather_report",
        ),
    ]
    weather_text = _summarize_run("what's the weather in Mumbai", weather)
    assert weather_text.startswith("Showing the weather for Mumbai")
    assert "{" not in weather_text

    opened = [
        HumanMessage(content="open new tab and search griezmann"),
        AIMessage(
            content="step",
            tool_calls=[
                {
                    "name": "web_search",
                    "args": {"query": "griezmann", "mode": "search"},
                    "id": "s1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "success": True,
                    "result": {
                        "simulated": False,
                        "output": "1. Antoine Griezmann - Wikipedia\n   Source: https://en.wikipedia.org/wiki/Antoine_Griezmann",
                    },
                }
            ),
            tool_call_id="s1",
            name="web_search",
        ),
        AIMessage(
            content="step",
            tool_calls=[
                {
                    "name": "browser_control",
                    "args": {
                        "action": "new_tab",
                        "url": "https://en.wikipedia.org/wiki/Antoine_Griezmann",
                    },
                    "id": "b1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "success": True,
                    "result": {"simulated": False, "output": "opened"},
                    "message": "real execution",
                }
            ),
            tool_call_id="b1",
            name="browser_control",
        ),
    ]
    opened_text = _summarize_run("open new tab and search griezmann", opened)
    assert opened_text.startswith("Opened the top result for griezmann")
    assert "Antoine Griezmann" in opened_text
    assert "wikipedia.org" in opened_text
    assert "real execution" not in opened_text


def test_cache_create_plan_and_write() -> None:
    import asyncio

    from app.agent.graph import _build_demo_plan
    from app.bridge.executor import (
        CACHE_DIR,
        allows_real_execution,
        execute_tool,
        file_action_needs_confirmation,
    )

    plan = _build_demo_plan(
        "create a simple hello.py in cache",
        {"file_controller", "web_search"},
    )
    assert plan[0]["name"] == "file_controller"
    assert plan[0]["args"]["path"] == "cache"
    assert plan[0]["args"]["name"] == "hello.py"
    assert plan[0]["args"]["action"] == "create_file"
    assert 'print("hello")' in plan[0]["args"]["content"]

    hello_world = _build_demo_plan(
        "create a simple hello world python file in cache",
        {"file_controller"},
    )
    assert hello_world[0]["args"]["name"] == "hello_world.py"
    assert 'print("hello world")' in hello_world[0]["args"]["content"]
    note = _build_demo_plan(
        "create a notes.txt file in cache with a short project note",
        {"file_controller"},
    )
    assert note[0]["args"]["name"] == "notes.txt"
    assert "project note" in note[0]["args"]["content"]
    clock = _build_demo_plan(
        "make a basic python script in cache that prints the current time",
        {"file_controller"},
    )
    assert clock[0]["args"]["name"] == "current_time.py"
    assert "datetime" in clock[0]["args"]["content"]

    outside = {"action": "create_file", "path": "desktop", "name": "hello.py", "content": "x"}
    assert allows_real_execution("file_controller", outside) is False
    assert file_action_needs_confirmation("file_controller", outside) is True
    assert allows_real_execution(
        "file_controller", {"action": "create_file", "path": "cache", "name": "run.exe"}
    ) is False

    name = "hello_test_aura.py"
    target = CACHE_DIR / name
    if target.exists():
        target.unlink()
    fresh = {
        "action": "create_file",
        "path": "cache",
        "name": name,
        "content": 'print("hello")\n',
    }
    assert allows_real_execution("file_controller", fresh) is True
    assert file_action_needs_confirmation("file_controller", fresh) is False

    class _Reg:
        def get_tool(self, tool_name: str):
            if tool_name == "file_controller":
                return {"name": tool_name, "source": "actions/file_controller.py"}
            return None

    outcome = asyncio.run(execute_tool("file_controller", fresh, registry=_Reg()))
    try:
        assert outcome["success"] is True
        assert outcome["result"]["output"] == f"Created cache/{name}"
        assert target.read_text(encoding="utf-8") == 'print("hello")\n'
        again = asyncio.run(execute_tool("file_controller", fresh, registry=_Reg()))
        assert again["success"] is False
        assert "overwrite" in again["message"]
    finally:
        if target.exists():
            target.unlink()


def test_open_tab_plan_uses_search_and_browser() -> None:
    from app.agent.graph import _build_demo_plan

    plan = _build_demo_plan(
        "open new tab and search griezmann",
        {"web_search", "browser_control", "send_message", "delete_file"},
    )
    assert [step["name"] for step in plan] == ["web_search", "browser_control"]
    assert plan[0]["args"]["query"] == "griezmann"
    assert plan[1]["args"]["action"] == "new_tab"
    assert "griezmann" in plan[1]["args"]["url"]
    assert plan[1]["args"]["url"].startswith("https://")


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
