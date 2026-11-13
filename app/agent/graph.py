"""LangGraph ReAct agent over the Phase 1 bridge tools.

Flow:
1. User message enters as a HumanMessage (plus optional short-term history).
2. The agent node plans the *next* step — LLM (if OPENAI_API_KEY) or a local
   multi-step demo planner. One tool call is emitted per hop so steps run in sequence.
3. The gated tools node runs safe tools immediately. For risky tools it calls
   LangGraph ``interrupt()`` and pauses until /agent/run resumes with confirmed=true.
4. Tool results return to the agent; the loop continues until the plan is done.
5. Short-term memory stores the user/assistant turn when the run completes
   (confirmation pauses keep the same session_id / thread_id).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any
from urllib.parse import quote_plus

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, interrupt

from app.agent.memory import ShortTermMemory, memory_store
from app.agent.tools import build_langchain_tools, is_risky_tool
from app.bridge.tool_registry import ToolRegistry
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Aura, a desktop agent that controls the machine through bridge tools. "
    "Use the available tools when the user asks to open apps, URLs, search, or run actions. "
    "You may call multiple tools across turns to complete a multi-step request. "
    "Prefer calling tools over guessing. Keep final answers short and clear. "
    "Risky tools (delete/send/shutdown/etc.) require human confirmation before they run."
)

# Shared checkpointer so interrupt() / resume works across /agent/run calls.
_checkpointer = MemorySaver()
# Compiled graphs keyed by (use_llm, tool_names_tuple).
_graph_cache: dict[tuple[Any, ...], Any] = {}


def _latest_user_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""


def _completed_tool_count(messages: list[BaseMessage]) -> int:
    return sum(1 for msg in messages if isinstance(msg, ToolMessage))


def _build_demo_plan(user_text: str, tool_names: set[str]) -> list[dict[str, Any]]:
    """Turn a natural-language request into an ordered list of tool steps."""
    text = user_text.lower()
    plan: list[dict[str, Any]] = []

    wants_open_or_search = any(
        key in text for key in ("open", "chrome", "browser", "url", "search", "http")
    )
    if "open_url" in tool_names and wants_open_or_search:
        url = "https://www.google.com"
        if "search" in text:
            query = user_text
            for noise in (
                "open chrome and",
                "open chrome then",
                "open chrome",
                "then delete",
                "and delete",
                "search for",
                "search",
                "please",
                "delete the file",
                "delete file",
            ):
                query = re.sub(re.escape(noise), "", query, flags=re.IGNORECASE)
            # Strip trailing delete-file clauses for the search query.
            query = re.split(r"\bthen\b|\band then\b", query, maxsplit=1, flags=re.IGNORECASE)[0]
            query = query.strip(" .") or "python tutorials"
            url = f"https://www.google.com/search?q={quote_plus(query)}"
        elif "http://" in text or "https://" in text:
            match = re.search(r"https?://\S+", user_text)
            if match:
                url = match.group(0).rstrip(".,)")
        plan.append({"name": "open_url", "args": {"url": url}})

    wants_delete = any(key in text for key in ("delete", "remove file", "erase"))
    if "delete_file" in tool_names and wants_delete:
        path = "temp.txt"
        match = re.search(
            r"(?:delete|remove)\s+(?:the\s+)?file\s+([^\s,]+)",
            user_text,
            flags=re.IGNORECASE,
        )
        if match:
            path = match.group(1).strip(" .,\"'")
        plan.append({"name": "delete_file", "args": {"path": path}})

    # Generic "send message" demo step when that tool exists.
    if "send_message" in tool_names and "send" in text and "message" in text:
        plan.append(
            {
                "name": "send_message",
                "args": {"text": "Hello from Aura (simulated)"},
            }
        )

    return plan


def _demo_agent_node(tools: list[BaseTool]):
    tool_names = {t.name for t in tools}

    def agent_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
        messages = list(state["messages"])
        user_text = _latest_user_text(messages)
        plan = _build_demo_plan(user_text, tool_names)
        done = _completed_tool_count(messages)

        if plan and done < len(plan):
            step = plan[done]
            return {
                "messages": [
                    AIMessage(
                        content=f"Step {done + 1}/{len(plan)}: {step['name']}",
                        tool_calls=[
                            {
                                "name": step["name"],
                                "args": step.get("args") or {},
                                "id": f"demo_{step['name']}_{uuid.uuid4().hex[:8]}",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            }

        if plan and done >= len(plan):
            return {
                "messages": [
                    AIMessage(
                        content=f"Done. Completed {done} tool step(s) for: {user_text}"
                    )
                ]
            }

        available = ", ".join(sorted(tool_names)) or "(none)"
        return {
            "messages": [
                AIMessage(
                    content=(
                        "I am running in demo mode (no OPENAI_API_KEY). "
                        f"Available bridge tools: {available}. "
                        "Try a multi-step request like "
                        "'open chrome and search for python tutorials then delete file temp.txt'."
                    )
                )
            ]
        }

    return agent_node


def _llm_agent_node(tools: list[BaseTool]):
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
        temperature=0,
    ).bind_tools(tools)

    def agent_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
        messages = list(state["messages"])
        # Ensure a system prompt is present once at the front.
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
        response = model.invoke(messages)
        return {"messages": [response]}

    return agent_node


def _gated_tools_node(tools: list[BaseTool]):
    """Run tools, but ``interrupt()`` before any risky tool executes."""
    tool_node = ToolNode(tools)

    async def tools_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
        messages = list(state["messages"])
        last_ai: AIMessage | None = None
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                last_ai = msg
                break
        if last_ai is None:
            return {"messages": []}

        for tc in last_ai.tool_calls:
            name = tc.get("name") or ""
            if not is_risky_tool(name):
                continue

            decision = interrupt(
                {
                    "status": "needs_confirmation",
                    "tool_name": name,
                    "arguments": tc.get("args") or {},
                    "message": "This action is risky. Confirm to proceed?",
                }
            )
            confirmed = (
                decision is True
                or decision == "confirmed"
                or (isinstance(decision, dict) and bool(decision.get("confirmed")))
            )
            if not confirmed:
                cancelled = {
                    "success": False,
                    "tool_name": name,
                    "cancelled": True,
                    "message": "User rejected the risky action.",
                }
                return {
                    "messages": [
                        ToolMessage(
                            content=json.dumps(cancelled),
                            tool_call_id=tc["id"],
                            name=name,
                        )
                    ]
                }

        # Safe tools, or risky tools that were confirmed — execute via ToolNode.
        # Use ainvoke: bridge StructuredTools are async-only.
        return await tool_node.ainvoke(state)

    return tools_node


def build_agent_graph(registry: ToolRegistry):
    """Compile a multi-step ReAct graph with risky-tool interrupts."""
    tools = build_langchain_tools(registry)
    if not tools:
        logger.warning("No bridge tools discovered — agent will have an empty toolset")

    use_llm = bool(settings.openai_api_key)
    cache_key = (use_llm, tuple(sorted(t.name for t in tools)))
    if cache_key in _graph_cache:
        return _graph_cache[cache_key]

    agent_node = _llm_agent_node(tools) if use_llm else _demo_agent_node(tools)
    if use_llm:
        logger.info("Building LLM ReAct agent (model=%s)", settings.llm_model)
    else:
        logger.info("Building demo multi-step ReAct agent (no OPENAI_API_KEY)")

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", _gated_tools_node(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    # Return to the agent so further plan steps can run.
    graph.add_edge("tools", "agent")

    compiled = graph.compile(checkpointer=_checkpointer)
    _graph_cache[cache_key] = compiled
    return compiled


def clear_graph_cache() -> None:
    """Test helper: drop compiled graphs (checkpointer threads remain)."""
    _graph_cache.clear()


def _extract_tool_calls(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    """Pair AI tool_calls with ToolMessage results for the API response."""
    pending: dict[str, dict[str, Any]] = {}
    completed: list[dict[str, Any]] = []

    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                pending[tc["id"]] = {
                    "tool_name": tc["name"],
                    "arguments": tc.get("args") or {},
                    "result": None,
                }
        elif isinstance(msg, ToolMessage):
            entry = pending.get(msg.tool_call_id)
            if entry is None:
                entry = {
                    "tool_name": getattr(msg, "name", None) or "unknown",
                    "arguments": {},
                    "result": None,
                }
            try:
                entry["result"] = json.loads(str(msg.content))
            except (json.JSONDecodeError, TypeError):
                entry["result"] = msg.content
            completed.append(entry)

    return completed


def _final_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = msg.content
            if isinstance(content, str) and content.strip():
                return content
            if content:
                return str(content)
    return "Agent finished with no text response."


def _interrupt_payload(result: dict[str, Any] | Any, graph, config: dict) -> dict[str, Any] | None:
    """Pull the needs_confirmation payload out of an interrupted run."""
    interrupts = None
    if isinstance(result, dict):
        interrupts = result.get("__interrupt__")
    if not interrupts:
        snapshot = graph.get_state(config)
        interrupts = getattr(snapshot, "interrupts", None) or ()
    if not interrupts:
        return None

    first = interrupts[0]
    value = getattr(first, "value", first)
    if isinstance(value, dict):
        return value
    return {
        "status": "needs_confirmation",
        "tool_name": "unknown",
        "arguments": {},
        "message": str(value),
    }


async def run_agent(
    message: str | None,
    registry: ToolRegistry,
    *,
    session_id: str | None = None,
    confirmed: bool | None = None,
    memory: ShortTermMemory | None = None,
) -> dict[str, Any]:
    """Run or resume one agent turn.

    - New request: pass ``message`` (and optional ``session_id``).
    - After a risky pause: pass the same ``session_id`` with ``confirmed=True/False``.
    """
    memory = memory or memory_store
    session_id = session_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": session_id}}

    graph = build_agent_graph(registry)

    # --- Resume after human confirmation ---
    if confirmed is not None:
        result = await graph.ainvoke(Command(resume=bool(confirmed)), config)
        payload = _interrupt_payload(result, graph, config)
        if payload is not None:
            # Another risky step may be waiting (unlikely with one-at-a-time plans).
            tool_calls = _extract_tool_calls(
                list((graph.get_state(config).values or {}).get("messages") or [])
            )
            return {
                "status": "needs_confirmation",
                "response": "",
                "tool_calls": tool_calls,
                "session_id": session_id,
                "tool_name": payload.get("tool_name", ""),
                "arguments": payload.get("arguments") or {},
                "message": payload.get(
                    "message", "This action is risky. Confirm to proceed?"
                ),
            }

        messages = list((result or {}).get("messages") or [])
        # If still paused without __interrupt__ in result, check state.next
        snapshot = graph.get_state(config)
        if snapshot.next:
            payload = _interrupt_payload(result, graph, config)
            if payload:
                return {
                    "status": "needs_confirmation",
                    "response": "",
                    "tool_calls": _extract_tool_calls(messages),
                    "session_id": session_id,
                    "tool_name": payload.get("tool_name", ""),
                    "arguments": payload.get("arguments") or {},
                    "message": payload.get(
                        "message", "This action is risky. Confirm to proceed?"
                    ),
                }

        response_text = _final_text(messages)
        tool_calls = _extract_tool_calls(messages)
        memory.append(session_id, "assistant", response_text)
        return {
            "status": "completed",
            "response": response_text,
            "tool_calls": tool_calls,
            "session_id": session_id,
            "tool_name": None,
            "arguments": None,
            "message": None,
        }

    # --- Fresh user message ---
    if not (message or "").strip():
        raise ValueError("message must be a non-empty string unless confirming")

    message = message.strip()
    history_msgs: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    for turn in memory.get_history(session_id):
        if turn["role"] == "user":
            history_msgs.append(HumanMessage(content=turn["content"]))
        elif turn["role"] == "assistant":
            history_msgs.append(AIMessage(content=turn["content"]))
    history_msgs.append(HumanMessage(content=message))

    # Remember the user turn immediately so confirmation pauses keep context.
    memory.append(session_id, "user", message)

    result = await graph.ainvoke({"messages": history_msgs}, config)
    payload = _interrupt_payload(result, graph, config)
    messages = list((result or {}).get("messages") or [])

    if payload is not None:
        return {
            "status": "needs_confirmation",
            "response": "",
            "tool_calls": _extract_tool_calls(messages),
            "session_id": session_id,
            "tool_name": payload.get("tool_name", ""),
            "arguments": payload.get("arguments") or {},
            "message": payload.get(
                "message", "This action is risky. Confirm to proceed?"
            ),
        }

    response_text = _final_text(messages)
    tool_calls = _extract_tool_calls(messages)
    memory.append(session_id, "assistant", response_text)

    return {
        "status": "completed",
        "response": response_text,
        "tool_calls": tool_calls,
        "session_id": session_id,
        "tool_name": None,
        "arguments": None,
        "message": None,
    }
