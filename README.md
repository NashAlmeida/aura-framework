# Aura Framework

Local web-agent bridge that discovers **Mark-LVII** tools, runs a LangGraph agent over them, and exposes a Streamlit chat UI.

**Repo:** https://github.com/NashAlmeida/aura-framework

## Quick start

```bash
cd web-agent-bridge   # or clone root of aura-framework
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

### Run backend + frontend

```bash
# Terminal 1 — FastAPI bridge (port 8000)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2 — Streamlit UI (port 8501)
streamlit run frontend/app.py
```

- Health: `GET http://127.0.0.1:8000/health`
- UI: http://localhost:8501/

## Layout

- `app/` — FastAPI bridge (registry, executor, WebSocket)
- `app/agent/` — LangGraph ReAct agent + risky-tool confirmation
- `frontend/` — Streamlit chat UI (dot-matrix font, charcoal dark theme)
- `mark_lvii/` — sample / forked Mark-LVII actions
- `tests/` — Phase 1–2 API tests
- `scripts/` — convenience launchers

## Phase 1 — Bridge

- Tool discovery from `mark_lvii/actions` and `plugins`
- Simulated `POST /tools/run`
- WebSocket status at `ws://127.0.0.1:8000/ws`

## Phase 2 — Agent

`POST /agent/run` runs a multi-step LangGraph agent over bridge tools.

- With `OPENAI_API_KEY` → LLM agent node
- Without a key → local multi-step demo planner
- Risky tools (`delete_file`, send/shutdown/…) pause with `needs_confirmation`

### Confirmation flow

```bash
# 1) Triggers interrupt on delete_file
curl -X POST http://127.0.0.1:8000/agent/run ^
  -H "Content-Type: application/json" ^
  -d "{\"message\": \"delete file temp.txt\", \"session_id\": \"demo-1\"}"

# 2) Resume with the same session_id
curl -X POST http://127.0.0.1:8000/agent/run ^
  -H "Content-Type: application/json" ^
  -d "{\"session_id\": \"demo-1\", \"confirmed\": true}"
```

## Phase 3 — Streamlit frontend

- Title: **Aura Framework**
- Dot-matrix font (`VT323` / Share Tech Mono)
- Light (white) / dark (**charcoal grey**) theme toggle
- Square sharp-cornered buttons
- Chat → `/agent/run` with Confirm / Cancel for risky tools
- Optional live WebSocket status in the sidebar

```bash
streamlit run frontend/app.py
```

## Status

Phase 1 bridge + Phase 2 multi-step agent (HITL) + Phase 3 Streamlit UI.
