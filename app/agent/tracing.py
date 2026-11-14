"""Langfuse tracing for the LangGraph agent.

Uses the LangChain ``CallbackHandler`` so LLM generations and tool calls are
nested automatically, then sets session / tags / a stable trace name via
``propagate_attributes``. Tracing is a no-op when Langfuse keys are unset.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def tracing_enabled() -> bool:
    """True only when both Langfuse API keys are present in the environment."""
    public = (os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret = (os.getenv("LANGFUSE_SECRET_KEY") or "").strip()
    return bool(public and secret)


def _sync_host_env() -> None:
    """Langfuse accepts BASE_URL or HOST. Keep both aligned when one is set."""
    base = (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "").strip()
    if not base:
        return
    os.environ.setdefault("LANGFUSE_BASE_URL", base)
    os.environ.setdefault("LANGFUSE_HOST", base)


def _trace_output(result: Any) -> dict[str, Any]:
    """Compact root output: user-facing status, not raw graph state."""
    if not isinstance(result, dict):
        return {"status": "completed"}
    if result.get("__interrupt__"):
        return {"status": "needs_confirmation"}
    messages = result.get("messages") or []
    text = ""
    for msg in reversed(messages):
        content = getattr(msg, "content", "")
        tool_calls = getattr(msg, "tool_calls", None)
        if content and not tool_calls:
            text = content if isinstance(content, str) else str(content)
            break
    return {"status": "completed", "response": text[:2000]}


async def ainvoke_traced(
    graph: Any,
    payload: Any,
    *,
    session_id: str,
    run_name: str,
    trace_input: dict[str, Any],
) -> Any:
    """Invoke the graph, attaching a Langfuse agent trace when configured."""
    config: dict[str, Any] = {
        "configurable": {"thread_id": session_id},
        "run_name": run_name,
        "tags": ["aura-framework", "agent"],
    }

    if not tracing_enabled():
        return await graph.ainvoke(payload, config)

    _sync_host_env()
    try:
        from langfuse import get_client, propagate_attributes
        from langfuse.langchain import CallbackHandler
    except Exception:
        logger.exception("Langfuse import failed — running without tracing")
        return await graph.ainvoke(payload, config)

    langfuse = get_client()
    environment = (os.getenv("LANGFUSE_TRACING_ENVIRONMENT") or "development").strip()

    # One trace per agent turn. session_id groups turns (including HITL resumes).
    # CallbackHandler is created inside propagate_attributes so child
    # generations and tool calls inherit the session, tags, and trace name.
    with langfuse.start_as_current_observation(
        as_type="agent",
        name=run_name,
        input=trace_input,
        metadata={"feature": "agent-run", "framework": "langgraph"},
    ) as agent_obs:
        with propagate_attributes(
            trace_name=run_name,
            session_id=session_id,
            tags=["aura-framework", "agent"],
            metadata={"feature": "agent-run", "framework": "langgraph"},
            environment=environment,
        ):
            config["callbacks"] = [CallbackHandler()]
            result = await graph.ainvoke(payload, config)
        agent_obs.update(output=_trace_output(result))

    try:
        langfuse.flush()
    except Exception:
        logger.exception("Langfuse flush failed")
    return result


def shutdown_tracing() -> None:
    """Flush queued spans when the FastAPI process stops."""
    if not tracing_enabled():
        return
    try:
        from langfuse import get_client

        get_client().shutdown()
    except Exception:
        logger.exception("Langfuse shutdown failed")
