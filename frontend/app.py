"""
Aura Framework — Phase 3 Streamlit chat UI.

Talks to the FastAPI bridge:
  POST http://127.0.0.1:8000/agent/run
  WS   ws://127.0.0.1:8000/ws

Run (with the backend already up):
  streamlit run frontend/app.py
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any

import httpx
import streamlit as st
import websockets

API_URL = "http://127.0.0.1:8000/agent/run"
WS_URL = "ws://127.0.0.1:8000/ws"
HEALTH_URL = "http://127.0.0.1:8000/health"

# ---------------------------------------------------------------------------
# Page + session defaults
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Aura Framework",
    page_icon="▣",
    layout="centered",
    initial_sidebar_state="expanded",
)

if "theme" not in st.session_state:
    st.session_state.theme = "dark"  # charcoal grey by default
if "messages" not in st.session_state:
    st.session_state.messages = []
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "pending_confirmation" not in st.session_state:
    st.session_state.pending_confirmation = None
if "ws_events" not in st.session_state:
    st.session_state.ws_events: list[dict[str, Any]] = []
if "ws_started" not in st.session_state:
    st.session_state.ws_started = False

# Dot-matrix stack: VT323 first (pixel), then Share Tech Mono / Courier.
DOTMATRIX = '"VT323", "Share Tech Mono", "Courier New", Courier, monospace'


# ---------------------------------------------------------------------------
# Theme CSS — light white / dark charcoal, dot-matrix font, square buttons
# ---------------------------------------------------------------------------

def inject_theme_css(theme: str) -> None:
    """Light = white; dark = charcoal grey. Dot-matrix type, sharp square controls."""
    if theme == "light":
        bg, fg, muted, accent, input_bg = "#FFFFFF", "#1A1A1A", "#555555", "#000000", "#F0F0F0"
        border = "#1A1A1A"
    else:
        # Charcoal grey dark theme (not pure black)
        bg, fg, muted, accent, input_bg = "#2B2B2B", "#E8E8E8", "#A8A8A8", "#F5F5F5", "#3A3A3A"
        border = "#C8C8C8"

    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=VT323&family=Share+Tech+Mono&display=swap');

        html, body, [data-testid="stAppViewContainer"],
        [data-testid="stHeader"], [data-testid="stToolbar"],
        [data-testid="stSidebar"], .stApp {{
            background-color: {bg} !important;
            color: {fg} !important;
            font-family: {DOTMATRIX} !important;
            font-size: 1.15rem !important;
        }}

        h1, h2, h3, h4, h5, h6, p, span, label, li, code, pre,
        [data-testid="stMarkdownContainer"],
        [data-testid="stChatMessage"],
        [data-testid="stSidebar"] * {{
            font-family: {DOTMATRIX} !important;
            color: {fg} !important;
        }}

        /* Square, sharp-cornered buttons — no rounded corners */
        div.stButton > button,
        div.stDownloadButton > button,
        button[kind],
        [data-testid="stBaseButton-secondary"],
        [data-testid="stBaseButton-primary"] {{
            border-radius: 0 !important;
            border: 2px solid {border} !important;
            background-color: {bg} !important;
            color: {fg} !important;
            font-family: {DOTMATRIX} !important;
            font-size: 1.25rem !important;
            font-weight: 400 !important;
            letter-spacing: 0.06em !important;
            box-shadow: none !important;
            opacity: 1 !important;
        }}
        div.stButton > button:hover {{
            background-color: {fg} !important;
            color: {bg} !important;
            border-color: {fg} !important;
        }}

        /* Chat input — square */
        [data-testid="stChatInput"] textarea,
        [data-testid="stChatInput"] > div {{
            border-radius: 0 !important;
            border: 2px solid {border} !important;
            background-color: {input_bg} !important;
            color: {fg} !important;
            font-family: {DOTMATRIX} !important;
            font-size: 1.2rem !important;
        }}

        /* Chat bubbles — sharp, solid */
        [data-testid="stChatMessage"] {{
            border: 1px solid {border} !important;
            border-radius: 0 !important;
            background-color: {input_bg} !important;
        }}

        /* Sidebar status */
        .aura-status {{
            border: 2px solid {border};
            padding: 0.75rem;
            margin-top: 0.5rem;
            font-size: 1.05rem;
            color: {muted};
            background: {input_bg};
            font-family: {DOTMATRIX};
        }}
        .aura-title {{
            font-family: {DOTMATRIX} !important;
            font-size: 3.4rem !important;
            letter-spacing: 0.1em;
            margin-bottom: 0.25rem;
            color: {accent} !important;
        }}
        .aura-sub {{
            font-family: {DOTMATRIX} !important;
            color: {muted} !important;
            font-size: 1.15rem;
            margin-bottom: 1.25rem;
        }}
        hr {{
            border: none;
            border-top: 2px solid {border};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_theme_css(st.session_state.theme)


# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

def backend_healthy() -> bool:
    try:
        r = httpx.get(HEALTH_URL, timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def call_agent(
    *,
    message: str | None = None,
    confirmed: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"session_id": st.session_state.session_id}
    if confirmed is not None:
        payload["confirmed"] = confirmed
    if message is not None:
        payload["message"] = message
    with httpx.Client(timeout=60.0) as client:
        response = client.post(API_URL, json=payload)
        response.raise_for_status()
        return response.json()


def format_tool_calls(tool_calls: list[dict[str, Any]]) -> str:
    if not tool_calls:
        return ""
    lines = ["", "--- tool calls ---"]
    for tc in tool_calls:
        lines.append(f"* {tc.get('tool_name')} {json.dumps(tc.get('arguments') or {})}")
        result = tc.get("result")
        if result is not None:
            lines.append(f"  -> {json.dumps(result) if not isinstance(result, str) else result}")
    return "\n".join(lines)


def append_assistant(content: str) -> None:
    st.session_state.messages.append({"role": "assistant", "content": content})


def handle_agent_result(data: dict[str, Any]) -> None:
    """Update chat / pending confirmation from an /agent/run response."""
    status = data.get("status") or "completed"
    if data.get("session_id"):
        st.session_state.session_id = data["session_id"]

    if status == "needs_confirmation":
        st.session_state.pending_confirmation = {
            "tool_name": data.get("tool_name") or "unknown",
            "arguments": data.get("arguments") or {},
            "message": data.get("message")
            or "This action is risky. Confirm to proceed?",
        }
        tool_bits = format_tool_calls(data.get("tool_calls") or [])
        append_assistant(
            f"[NEEDS CONFIRMATION]\n"
            f"{st.session_state.pending_confirmation['message']}\n"
            f"tool: {st.session_state.pending_confirmation['tool_name']}\n"
            f"args: {json.dumps(st.session_state.pending_confirmation['arguments'])}"
            f"{tool_bits}"
        )
    else:
        st.session_state.pending_confirmation = None
        text = data.get("response") or "(no response)"
        text += format_tool_calls(data.get("tool_calls") or [])
        append_assistant(text)


# ---------------------------------------------------------------------------
# Optional WebSocket status listener (background thread)
# ---------------------------------------------------------------------------

def _ws_listener(events: list[dict[str, Any]], stop_flag: dict[str, bool]) -> None:
    async def _run() -> None:
        while not stop_flag.get("stop"):
            try:
                async with websockets.connect(WS_URL) as ws:
                    while not stop_flag.get("stop"):
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        events.append(json.loads(raw))
                        if len(events) > 40:
                            del events[:-40]
            except Exception as exc:
                events.append({"type": "ws_error", "message": str(exc)})
                if len(events) > 40:
                    del events[:-40]
                await asyncio.sleep(2.0)

    try:
        asyncio.run(_run())
    except Exception as exc:
        events.append({"type": "ws_error", "message": str(exc)})


def ensure_ws_thread() -> None:
    if st.session_state.ws_started:
        return
    stop_flag = {"stop": False}
    st.session_state._ws_stop = stop_flag
    thread = threading.Thread(
        target=_ws_listener,
        args=(st.session_state.ws_events, stop_flag),
        daemon=True,
    )
    thread.start()
    st.session_state.ws_started = True


ensure_ws_thread()


# ---------------------------------------------------------------------------
# Sidebar — theme toggle + live WS status
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### CONTROLS")

    # Theme toggle — square button via CSS above
    label = "LIGHT MODE" if st.session_state.theme == "dark" else "DARK MODE"
    if st.button(label, use_container_width=True, key="theme_toggle"):
        st.session_state.theme = "light" if st.session_state.theme == "dark" else "dark"
        st.rerun()

    st.markdown("---")
    healthy = backend_healthy()
    st.markdown(f"**BACKEND:** `{'ONLINE' if healthy else 'OFFLINE'}`")
    st.caption(f"session `{st.session_state.session_id[:8]}…`")

    st.markdown("---")
    st.markdown("### WS STATUS")
    events = st.session_state.ws_events[-8:]
    if not events:
        st.markdown(
            '<div class="aura-status">waiting for websocket events…</div>',
            unsafe_allow_html=True,
        )
    else:
        lines = []
        for ev in events:
            et = ev.get("type", "?")
            if et in ("tool_started", "tool_finished"):
                lines.append(
                    f"{et} · {ev.get('tool_name', '')} · success={ev.get('success', '')}"
                )
            elif et == "connected":
                lines.append(f"connected · {ev.get('message', '')}")
            else:
                lines.append(f"{et} · {ev.get('message', ev)}")
        st.markdown(
            '<div class="aura-status">' + "<br/>".join(lines) + "</div>",
            unsafe_allow_html=True,
        )

    if st.button("REFRESH STATUS", use_container_width=True, key="refresh_ws"):
        st.rerun()


# ---------------------------------------------------------------------------
# Main chat UI
# ---------------------------------------------------------------------------

st.markdown('<p class="aura-title">AURA FRAMEWORK</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="aura-sub">PHASE 3 // CHAT INTERFACE // BRIDGE @ 127.0.0.1:8000</p>',
    unsafe_allow_html=True,
)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.text(msg["content"])

# Confirmation controls (square Confirm / Cancel)
pending = st.session_state.pending_confirmation
if pending:
    st.markdown("---")
    st.markdown(
        f"**RISKY ACTION** — `{pending['tool_name']}`  \n"
        f"`{json.dumps(pending['arguments'])}`  \n"
        f"{pending['message']}"
    )
    col_ok, col_no = st.columns(2)
    with col_ok:
        if st.button("CONFIRM", use_container_width=True, key="confirm_yes"):
            try:
                data = call_agent(confirmed=True)
                handle_agent_result(data)
            except Exception as exc:
                append_assistant(f"[ERROR] confirmation failed: {exc}")
            st.rerun()
    with col_no:
        if st.button("CANCEL", use_container_width=True, key="confirm_no"):
            try:
                data = call_agent(confirmed=False)
                handle_agent_result(data)
            except Exception as exc:
                append_assistant(f"[ERROR] cancel failed: {exc}")
                st.session_state.pending_confirmation = None
            st.rerun()

# Chat input (disabled while waiting for confirmation)
prompt = st.chat_input(
    "type a command…",
    disabled=pending is not None or not healthy,
)

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    try:
        data = call_agent(message=prompt)
        handle_agent_result(data)
    except Exception as exc:
        append_assistant(
            f"[ERROR] could not reach agent: {exc}\n"
            "Is the backend running?  python -m uvicorn app.main:app --port 8000"
        )
    st.rerun()
