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
from app.bridge.executor import file_action_needs_confirmation
from app.agent.tracing import ainvoke_traced
from app.bridge.tool_registry import ToolRegistry
from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Aura, a desktop agent that controls the machine through bridge tools. "
    "Use the available tools when the user asks to open apps, URLs, search, or run actions. "
    "You may call multiple tools across turns to complete a multi-step request. "
    "Prefer calling tools over guessing. Keep final answers short and clear. "
    "When the user asks to open a new tab, open Chrome, or search in the browser, "
    "call web_search and also browser_control. For browser_control use action "
    "'new_tab' with an https URL: the first result link, or a Google search URL "
    "if there is no result link. action 'search' with a query is also fine. "
    "Do not click, type, or close tabs. "
    "For file questions, use file_controller list or disk_usage on a home shortcut "
    "(desktop, downloads, documents, home). Create new .py, .txt, or .md files "
    "only inside cache/ with create_file. Do not read, overwrite, or delete files, "
    "and do not write outside cache/. "
    "After the tools finish, answer in one or two short sentences. Lead with the "
    "result. Do not paste raw JSON or the full tool payload. "
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


_SEARCH_NOISE = (
    "open a new tab and",
    "open new tab and",
    "open a new tab",
    "open new tab",
    "open chrome and",
    "open chrome then",
    "open chrome",
    "open the browser and",
    "open browser and",
    "open the browser",
    "open browser",
    "in the browser",
    "in a browser",
    "in browser",
    "in chrome",
    "search the web for",
    "search for",
    "look up",
    "search",
    "please",
)


def _wants_browser_open(text: str) -> bool:
    """True when the user asked for a tab or a browser, not just a search."""
    return any(
        phrase in text
        for phrase in (
            "new tab",
            "open tab",
            "open chrome",
            "open browser",
            "open the browser",
            "in browser",
            "in chrome",
            "in a browser",
            "in the browser",
        )
    )


def _extract_search_query(user_text: str) -> str:
    """Pull the search terms out of an open-tab / search sentence."""
    query = re.split(r"\bthen\b|\band then\b", user_text, maxsplit=1, flags=re.IGNORECASE)[0]
    for noise in _SEARCH_NOISE:
        query = re.sub(rf"\b{re.escape(noise)}\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+", " ", query).strip(" .,-")
    return query or "python tutorials"


def _first_result_url(messages: list[BaseMessage]) -> str | None:
    """First http(s) link from an earlier tool result, skipping search pages."""
    skip = ("google.com/search", "duckduckgo.com/", "bing.com/search")
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        for match in re.finditer(r"https?://[^\s\"'\\<>]+", str(msg.content)):
            url = match.group(0).rstrip(".,);]")
            if any(host in url.lower() for host in skip):
                continue
            return url
    return None


def _browser_call_args(step: dict[str, Any], messages: list[BaseMessage]) -> dict[str, Any]:
    """Use the top web_search link when one exists; otherwise the planned URL."""
    args = dict(step.get("args") or {})
    if not step.get("open_top_result"):
        return args
    found = _first_result_url(messages)
    if not found:
        return args
    opened = {"action": "new_tab", "url": found}
    if args.get("browser"):
        opened["browser"] = args["browser"]
    return opened


def _cache_file_step(user_text: str) -> dict[str, Any] | None:
    """Plan a new cache/ file when the user asks to create one."""
    text = user_text.lower()
    wants_create = any(
        phrase in text
        for phrase in ("create", "make a file", "write a file", "save a file")
    ) or (
        "make a" in text
        and any(kind in text for kind in ("python", "script", "file", ".py", ".txt", ".md"))
    )
    if not wants_create:
        return None
    match = re.search(r"\b([A-Za-z0-9_\-]+\.(?:py|txt|md))\b", user_text, flags=re.IGNORECASE)
    if match:
        name = match.group(1)
        ext = name.rsplit(".", 1)[-1].lower()
    elif "python" in text or "script" in text:
        name = "current_time.py" if "time" in text else "hello_world.py"
        ext = "py"
    elif "note" in text:
        name = "notes.txt"
        ext = "txt"
    else:
        return None
    said = re.search(
        r"(?:that says|with content|containing)\s+(.+)$",
        user_text,
        flags=re.IGNORECASE,
    )
    if said:
        content = said.group(1).strip().strip("\"'")
        if not content.endswith("\n"):
            content += "\n"
    elif ext == "py" and "time" in text:
        content = "from datetime import datetime\nprint(datetime.now().isoformat())\n"
    elif ext == "py" and "hello world" in text:
        content = 'print("hello world")\n'
    elif ext == "py":
        content = 'print("hello")\n'
    elif "note" in text:
        content = "Aura Framework project note.\n"
    else:
        content = "hello\n"
    outside = any(folder in text for folder in ("desktop", "download", "document", "picture"))
    path = "desktop" if outside and "cache" not in text else "cache"
    return {
        "name": "file_controller",
        "args": {"action": "create_file", "path": path, "name": name, "content": content},
    }


def _build_demo_plan(user_text: str, tool_names: set[str]) -> list[dict[str, Any]]:
    """Turn a natural-language request into an ordered list of tool steps."""
    text = user_text.lower()
    plan: list[dict[str, Any]] = []

    wants_weather = "weather" in text
    if "weather_report" in tool_names and wants_weather:
        city = "Mumbai"
        match = re.search(
            r"(?:weather(?:\s+report)?|forecast)\s+(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]{1,40})",
            user_text,
            flags=re.IGNORECASE,
        )
        if match:
            city = match.group(1).strip(" .")
        plan.append({"name": "weather_report", "args": {"city": city}})

    creating_file = False
    if "file_controller" in tool_names:
        create_step = _cache_file_step(user_text)
        if create_step:
            plan.append(create_step)
            creating_file = True

    wants_desktop_list = "desktop" in text and any(
        key in text for key in ("list", "files", "what", "show")
    )
    if "desktop_control" in tool_names and wants_desktop_list:
        plan.append({"name": "desktop_control", "args": {"action": "list"}})

    folder = "desktop"
    if "download" in text:
        folder = "downloads"
    elif "document" in text:
        folder = "documents"
    elif "picture" in text or "photo" in text:
        folder = "pictures"
    elif "music" in text:
        folder = "music"
    elif re.search(r"\bhome\b", text):
        folder = "home"
    wants_file_list = any(
        phrase in text
        for phrase in (
            "list files",
            "list the files",
            "show files",
            "what files",
            "files in",
            "what's in",
            "whats in",
        )
    ) or ("list" in text and folder != "desktop")
    desktop_already = wants_desktop_list and "desktop_control" in tool_names
    if (
        "file_controller" in tool_names
        and wants_file_list
        and not desktop_already
        and not creating_file
    ):
        plan.append({"name": "file_controller", "args": {"action": "list", "path": folder}})

    wants_system = any(
        phrase in text
        for phrase in (
            "disk",
            "storage",
            "free space",
            "disk space",
            "system info",
            "how much space",
        )
    )
    if "file_controller" in tool_names and wants_system and not wants_file_list and not creating_file:
        plan.append(
            {"name": "file_controller", "args": {"action": "disk_usage", "path": "home"}}
        )

    wants_web_search = "search" in text or "look up" in text
    if "web_search" in tool_names and wants_web_search and not wants_weather:
        query = _extract_search_query(user_text)
        plan.append({"name": "web_search", "args": {"query": query, "mode": "search"}})

    wants_browser = _wants_browser_open(text)
    explicit_url = None
    url_match = re.search(r"https?://\S+", user_text)
    if url_match:
        explicit_url = url_match.group(0).rstrip(".,)")
    if (
        "browser_control" in tool_names
        and wants_browser
        and (wants_web_search or explicit_url)
    ):
        browser = "chrome" if "chrome" in text else None
        if explicit_url and not wants_web_search:
            args = {"action": "new_tab", "url": explicit_url}
            open_top_result = False
        else:
            query = _extract_search_query(user_text)
            args = {
                "action": "new_tab",
                "url": f"https://www.google.com/search?q={quote_plus(query)}",
            }
            open_top_result = True
        if browser:
            args["browser"] = browser
        plan.append(
            {
                "name": "browser_control",
                "args": args,
                "open_top_result": open_top_result,
            }
        )

    planned = {step["name"] for step in plan}
    wants_open_or_search = any(
        key in text for key in ("open", "chrome", "browser", "url", "http")
    ) and "web_search" not in planned and "browser_control" not in planned
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


def _result_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return {"output": result}
    return result if isinstance(result, dict) else {"output": str(result)}


def _output_text(result: Any) -> tuple[str, bool]:
    """Return (readable output, simulated)."""
    payload = _result_payload(result)
    inner = payload.get("result")
    inner = inner if isinstance(inner, dict) else payload
    simulated = bool(inner.get("simulated"))
    text = str(inner.get("output") or inner.get("error") or payload.get("message") or "")
    text = re.sub(r"<<<UNTRUSTED[^>]*>>>", " ", text)
    text = re.sub(r"<<<END UNTRUSTED>>>", " ", text)
    return text.strip(), simulated


def _first_sentence(text: str, limit: int = 180) -> str:
    line = ""
    for raw in text.splitlines():
        if raw.strip():
            line = raw.strip()
            break
    if not line:
        return ""
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def _search_title(text: str) -> str:
    match = re.search(r"(?m)^\s*\d+\.\s+(.{3,80})", text)
    if not match:
        return ""
    title = match.group(1).strip(" .")
    return title.split("   ")[0].strip()


def _summarize_run(user_text: str, messages: list[BaseMessage]) -> str:
    """One or two readable sentences from the tool results. No raw JSON."""
    calls = _extract_tool_calls(messages)
    if not calls:
        return "I could not match that to a tool."

    search_query = ""
    search_text = ""
    opened_url = ""
    sentences: list[str] = []

    for call in calls:
        name = call.get("tool_name") or "tool"
        args = call.get("arguments") or {}
        text, simulated = _output_text(call.get("result"))
        if name == "web_search":
            search_query = str(args.get("query") or _extract_search_query(user_text))
            search_text = text
            continue
        if name == "browser_control":
            opened_url = str(args.get("url") or "")
            if not opened_url:
                found = re.search(r"https?://[^\s]+", text)
                opened_url = found.group(0).rstrip(".,)") if found else ""
            continue
        if name == "weather_report":
            city = str(args.get("city") or "that city")
            if simulated:
                sentences.append(f"The weather lookup for {city} stayed simulated.")
            else:
                sentences.append(_first_sentence(text) or f"Showing the weather for {city}.")
            continue
        if name == "file_controller" and str(args.get("action") or "") in ("create_file", "write"):
            if simulated:
                sentences.append(_first_sentence(text) or "The file was not created.")
            else:
                sentences.append(_first_sentence(text) or "Created the file in cache/.")
            continue
        if name == "file_controller" and str(args.get("action") or "") == "disk_usage":
            if simulated:
                sentences.append("The disk check stayed simulated.")
            else:
                sentences.append(_first_sentence(text) or "Checked disk space.")
            continue
        if name in ("file_controller", "desktop_control"):
            where = str(args.get("path") or "desktop")
            if simulated:
                sentences.append(f"Listing {where} stayed simulated.")
            else:
                sentences.append(_first_sentence(text) or f"Listed {where}.")
            continue
        if simulated:
            sentences.append(f"{name} stayed simulated.")
        elif text:
            sentences.append(_first_sentence(text))

    if search_query and opened_url:
        title = _search_title(search_text)
        lead = (
            f"Opened the top result for {search_query}: {title}."
            if title
            else f"Opened a tab for {search_query}."
        )
        summary = f"{lead} {opened_url}"
    elif search_query:
        title = _search_title(search_text)
        if "no results" in search_text.lower():
            summary = f"No web results for {search_query}."
        elif title:
            summary = f"Top result for {search_query}: {title}."
        else:
            summary = _first_sentence(search_text) or f"Searched the web for {search_query}."
    else:
        summary = " ".join(part for part in sentences if part).strip()

    if search_query and opened_url and sentences:
        summary = f"{summary} {' '.join(sentences)}"
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) > 420:
        summary = summary[:419].rstrip() + "…"
    return summary or "Done."


def _demo_agent_node(tools: list[BaseTool]):
    tool_names = {t.name for t in tools}

    def agent_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
        messages = list(state["messages"])
        user_text = _latest_user_text(messages)
        plan = _build_demo_plan(user_text, tool_names)
        done = _completed_tool_count(messages)

        if plan and done < len(plan):
            step = plan[done]
            args = _browser_call_args(step, messages)
            return {
                "messages": [
                    AIMessage(
                        content=f"Step {done + 1}/{len(plan)}: {step['name']}",
                        tool_calls=[
                            {
                                "name": step["name"],
                                "args": args,
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
                    AIMessage(content=_summarize_run(user_text, messages))
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
            args = tc.get("args") or {}
            outside_cache = file_action_needs_confirmation(name, args)
            if not is_risky_tool(name) and not outside_cache:
                continue

            message = "This action is risky. Confirm to proceed?"
            if outside_cache:
                message = (
                    "This file change is outside cache/ or would overwrite a file. "
                    "Confirming does not allow a write outside cache/."
                )
            decision = interrupt(
                {
                    "status": "needs_confirmation",
                    "tool_name": name,
                    "arguments": args,
                    "message": message,
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
    # thread_id stays on the graph config inside ainvoke_traced.
    config = {"configurable": {"thread_id": session_id}}

    graph = build_agent_graph(registry)

    # --- Resume after human confirmation ---
    if confirmed is not None:
        result = await ainvoke_traced(
            graph,
            Command(resume=bool(confirmed)),
            session_id=session_id,
            run_name="confirm-agent-run",
            trace_input={"confirmed": bool(confirmed), "session_id": session_id},
        )
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

    result = await ainvoke_traced(
        graph,
        {"messages": history_msgs},
        session_id=session_id,
        run_name="run-agent",
        # Trace input is the user message only — not history or API keys.
        trace_input={"message": message},
    )
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
