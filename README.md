# Aura Framework guide

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
# Point MARK_LVII_PATH at the cloned Mark-LVII tree (the folder that contains actions/).
# Windows PowerShell:
#   $env:MARK_LVII_PATH = "D:\path\to\mark_lvii"
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

- Tool discovery from `mark_lvii/actions` and `plugins` (no import during discovery)
- `POST /tools/run` and the agent both go through `execute_tool`
- WebSocket status at `ws://127.0.0.1:8000/ws` (`tool_started` / `tool_finished`, including `mode`)

### Real vs simulated execution

Only a small allowlist calls the Mark-LVII handler. Logs say `mode=real` or `mode=simulated`.

| Tool | Runs for real when |
| --- | --- |
| `weather_report` | always |
| `web_search` | always |
| `desktop_control` | `action` is `list` |
| `browser_control` | `go_to`, `search`, or `new_tab` with an `http(s)` URL (or a query for `search`) |

Everything else stays simulated. Browser click, type, and close stay simulated. Risky tools (`delete_file`, `send_message`, shutdown, and similar names) still pause for confirmation before they run, and the run itself stays simulated.

`browser_control` opens the address in the user's normal browser. It does not attach Playwright for these three actions.

## Opening a browser tab

The demo planner (no `OPENAI_API_KEY`) treats these as two steps: `web_search`, then `browser_control` `new_tab`.

- `open new tab and search griezmann`
- `open chrome and search python tutorials`
- `search griezmann in browser`

If the search returns a link, that link is opened. Otherwise the tab is a Google search for the same query. “Chrome” in the request passes `browser=chrome`. A plain search with no tab/browser wording only calls `web_search`.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/agent/run -Method Post -ContentType "application/json" -Body '{"message":"open new tab and search griezmann","session_id":"demo-tab"}'
```

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
- Chat → `POST /agent/run`, with Confirm / Cancel when the status is `needs_confirmation`
- Sidebar WebSocket status, including whether a tool ran `real` or `simulated`

```bash
streamlit run frontend/app.py
```

## Langfuse tracing

Agent turns and tool calls are traced when both keys are set in `.env` (copied from `.env.example`). Keys stay in `.env` — do not commit them.

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_TRACING_ENVIRONMENT=development
```

Restart the API, then send `POST /agent/run`. Open the Langfuse project **Traces** view and filter by name `run-agent`. The same `session_id` groups a confirmation resume (`confirm-agent-run`) under **Sessions**.

## Status

Phase 1 bridge + Phase 2 multi-step agent (HITL) + Phase 3 Streamlit UI. Safe Mark-LVII tools (`weather_report`, `web_search`, desktop list, and browser open/search/new-tab) execute for real; other tools stay simulated.
