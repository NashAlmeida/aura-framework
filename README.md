# Web Agent Bridge — Phase 1

Local FastAPI bridge that discovers and executes **Mark-LVII** tools/actions over REST and WebSocket.

## Phase 1 goal

Stand up a clean local API so you can send a command (curl / Postman / script) and see an action run on the machine.

## Quick start

```bash
cd web-agent-bridge
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python scripts/run_bridge.py
```

Health check: `GET http://127.0.0.1:8000/health`

## Layout

- `app/` — bridge API (FastAPI)
- `mark_lvii/` — place forked [Mark-LVII](https://github.com/FatihMakes/Mark-LVII) source (submodule or copy)
- `tests/` — basic tests
- `scripts/` — convenience launchers

## Status

Skeleton only. Tool discovery and execution against Mark-LVII are stubs for later Phase 1 work.
