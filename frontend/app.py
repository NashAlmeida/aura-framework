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

# Dot-matrix stack: VT323 first (pixel), then Courier New.
DOTMATRIX = '"VT323", "Courier New", Courier, monospace'


# ---------------------------------------------------------------------------
# Theme CSS — chess-app look: B&W, pixel font, hard offset shadows
# ---------------------------------------------------------------------------

def inject_theme_css(theme: str) -> None:
    """Charcoal chess-board page: thin square panels, small light buttons, pixel type."""
    if theme == "light":
        bg, panel, fg, muted, line, btn_bg, btn_fg = (
            "#F2F2F2",
            "#FFFFFF",
            "#1A1A1A",
            "#444444",
            "#1A1A1A",
            "#E6E6E6",
            "#111111",
        )
        dots = "rgba(0,0,0,0.08)"
    else:
        # Matches the chess app: near-black page, slightly lighter side panel.
        bg, panel, fg, muted, line, btn_bg, btn_fg = (
            "#1A1A1A",
            "#2A2A2A",
            "#E6E6E6",
            "#9A9A9A",
            "#C8C8C8",
            "#E8E8E8",
            "#111111",
        )
        dots = "rgba(255,255,255,0.05)"

    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=VT323&display=swap');

        html, body, [data-testid="stAppViewContainer"],
        [data-testid="stHeader"], [data-testid="stToolbar"],
        [data-testid="stBottom"], [data-testid="stMain"],
        .stApp, .main .block-container {{
            background-color: {bg} !important;
            background-image: radial-gradient({dots} 1px, transparent 1px) !important;
            background-size: 3px 3px !important;
            color: {fg} !important;
            font-family: {DOTMATRIX} !important;
        }}

        section[data-testid="stSidebar"],
        section[data-testid="stSidebar"] > div {{
            background-color: {bg} !important;
            background-image: radial-gradient({dots} 1px, transparent 1px) !important;
            background-size: 3px 3px !important;
            border-right: 1px solid {line} !important;
        }}

        .stApp h1, .stApp h2, .stApp h3, .stApp p, .stApp label,
        .stApp li, .stApp code, .stApp pre, .stApp textarea, .stApp input,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stChatMessage"] {{
            font-family: {DOTMATRIX} !important;
            color: {fg} !important;
        }}

        /* Keep Streamlit's icon font so the sidebar collapse control stays an icon */
        [data-testid="stIconMaterial"],
        [data-testid="stSidebarCollapseButton"] span,
        span[class*="material"] {{
            font-family: "Material Symbols Rounded", "Material Symbols Outlined" !important;
            font-size: 1.25rem !important;
            color: {fg} !important;
        }}

        /* Small flat buttons, like Undo / Redo / Reset on the chess page */
        div.stButton > button {{
            border-radius: 0 !important;
            border: 1px solid #111111 !important;
            background-color: {btn_bg} !important;
            color: {btn_fg} !important;
            font-family: {DOTMATRIX} !important;
            font-size: 1.15rem !important;
            letter-spacing: 0.02em !important;
            box-shadow: none !important;
            padding: 0.15rem 0.7rem !important;
            min-height: 2.1rem !important;
        }}
        div.stButton > button p,
        div.stButton > button span {{
            color: {btn_fg} !important;
            font-family: {DOTMATRIX} !important;
        }}
        div.stButton > button:hover,
        div.stButton > button:hover p,
        div.stButton > button:hover span {{
            background-color: #FFFFFF !important;
            color: #111111 !important;
            border-color: #111111 !important;
        }}

        /* One square icon for the theme switch */
        .st-key-theme_icon button {{
            width: 2.4rem !important;
            min-width: 2.4rem !important;
            height: 2.4rem !important;
            padding: 0 !important;
            font-size: 1.35rem !important;
            line-height: 1 !important;
        }}

        /* Chat bar — follow the page colour, including the outer dark shell */
        [data-testid="stBottom"],
        [data-testid="stBottom"] > div,
        [data-testid="stBottomBlockContainer"],
        [data-testid="stChatInput"],
        [data-testid="stChatInput"] > div,
        [data-testid="stChatInputContainer"] {{
            background-color: {bg} !important;
            background-image: none !important;
            border-radius: 0 !important;
            border-color: {line} !important;
        }}
        [data-testid="stChatInput"] textarea {{
            border-radius: 0 !important;
            border: 1px solid {line} !important;
            background-color: {panel} !important;
            color: {fg} !important;
            font-family: {DOTMATRIX} !important;
            font-size: 1.15rem !important;
            box-shadow: none !important;
            caret-color: {fg} !important;
        }}
        [data-testid="stChatInput"] textarea::placeholder {{
            color: {muted} !important;
            opacity: 1 !important;
        }}
        [data-testid="stChatInputSubmitButton"] button {{
            border-radius: 0 !important;
            background-color: {btn_bg} !important;
            color: {btn_fg} !important;
            border: 1px solid #111111 !important;
        }}
        [data-testid="stChatInputSubmitButton"] button svg {{
            fill: {btn_fg} !important;
        }}

        [data-testid="stChatMessage"] {{
            border: 1px solid {line} !important;
            border-radius: 0 !important;
            background-color: {panel} !important;
            box-shadow: none !important;
        }}
        [data-testid="stChatMessageAvatar"] {{
            border-radius: 0 !important;
            border: 1px solid {line} !important;
            background-color: {panel} !important;
        }}

        .aura-panel, .aura-status {{
            border: 1px solid {line};
            border-radius: 0;
            background: {panel};
            color: {fg};
            box-shadow: none;
            padding: 0.7rem 0.85rem;
            margin: 0.45rem 0 0.8rem 0;
            font-family: {DOTMATRIX};
            font-size: 1.05rem;
        }}
        .aura-status .aura-muted {{
            color: {muted} !important;
        }}
        .aura-title {{
            font-family: {DOTMATRIX} !important;
            font-size: 2.4rem !important;
            letter-spacing: 0.08em;
            line-height: 1;
            margin: 0.2rem 0 0;
            color: {fg} !important;
        }}
        .aura-sub {{
            font-family: {DOTMATRIX} !important;
            color: {muted} !important;
            font-size: 1.05rem;
            margin: 0.15rem 0 0.7rem 0;
        }}
        hr {{
            border: none;
            border-top: 1px solid {line};
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
                    events.append({"type": "connected", "message": "Bridge WebSocket ready"})
                    while not stop_flag.get("stop"):
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            continue
                        events.append(json.loads(raw))
                        if len(events) > 12:
                            del events[:-12]
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
    # Bump this when the listener changes so a running session drops the old thread.
    if st.session_state.get("ws_version") == 2:
        return
    old_stop = st.session_state.get("_ws_stop")
    if isinstance(old_stop, dict):
        old_stop["stop"] = True
    stop_flag = {"stop": False}
    st.session_state._ws_stop = stop_flag
    st.session_state.ws_events = []
    st.session_state.ws_version = 2
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
    side_title, side_icon = st.columns([4, 1])
    with side_title:
        st.markdown("### CONTROLS")
    with side_icon:
        icon = "☀" if st.session_state.theme == "dark" else "☾"
        if st.button(icon, key="theme_icon", help="Switch dark / light"):
            st.session_state.theme = "light" if st.session_state.theme == "dark" else "dark"
            st.rerun()

    healthy = backend_healthy()
    state = "ONLINE" if healthy else "OFFLINE"
    st.markdown(
        (
            '<div class="aura-status">'
            "STATUS<br/>"
            f"BACKEND {state}<br/>"
            f'<span class="aura-muted">SESSION {st.session_state.session_id[:8]}</span>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    st.markdown("### WS STATUS")
    events = st.session_state.ws_events[-6:]
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
                    f"{et} · {ev.get('tool_name', '')} · mode={ev.get('mode', '')}"
                    f" · success={ev.get('success', '')}"
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
    '<p class="aura-sub">CHAT // BRIDGE @ 127.0.0.1:8000</p>',
    unsafe_allow_html=True,
)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.text(msg["content"])

# Confirmation controls (square Confirm / Cancel)
pending = st.session_state.pending_confirmation
if pending:
    st.markdown(
        (
            '<div class="aura-panel">RISKY ACTION<br/>'
            f"TOOL {pending['tool_name']}<br/>"
            f"ARGS {json.dumps(pending['arguments'])}<br/>"
            f"{pending['message']}</div>"
        ),
        unsafe_allow_html=True,
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
