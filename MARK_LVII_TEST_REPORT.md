# Mark-LVII integration test report

Date: 2026-11-15

## Where the clone actually is

Jarvis is cloned at:

`D:\final year proejct\mark_lvii\mark_lvii`

That is one folder deeper than the name `mark_lvii`. The bridge default `MARK_LVII_PATH=./mark_lvii` (inside `web-agent-bridge`) still holds only the two sample stubs `browser_control.py` and `file_controller.py`. Those are not the Jarvis sources.

This test pointed the registry at the real clone with:

`MARK_LVII_PATH=D:\final year proejct\mark_lvii\mark_lvii`

Startup log: `ToolRegistry: loaded 23 tool(s)`. `GET /tools` returned the same 23.

## Registry result

| Check | Result |
| --- | --- |
| Registry loads | Yes, no parse errors |
| Tools discovered | **23** |
| From `actions/` | 23 Python files, all listed |
| From `plugins/` | 0 tools |
| Files skipped by design | `plugins/__init__.py`, `plugins/_template.py` (names start with `_`) |
| Import of action modules | **23/23 imported** with the clone root on `sys.path`. No missing-dependency errors at import time |

`plugins/_template.py` does contain a `PLUGIN` dict. The registry skips it because the filename starts with `_`, which matches Mark-LVII’s own loader (templates and shared helpers are not tools).

Four action files have **no** `TOOL` dict. They are still listed, using the filename as the name and a placeholder description. They are not execution failures.

| File | Why it has no tool definition |
| --- | --- |
| `actions/background_monitor.py` | Helper used by other actions |
| `actions/proactive.py` | Engine, not a declared tool |
| `actions/screen_processor.py` | Capture helper |
| `actions/system_monitor.py` | Telemetry helper (inline tools live in Jarvis `main.py`, not this file) |

## Tools discovered

| Name | Source | Short description |
| --- | --- | --- |
| background_monitor | actions/background_monitor.py | Placeholder — no TOOL metadata |
| background_tasks | actions/background_tasks.py | Long background work: research, reports, lists |
| browser_control | actions/browser_control.py | Open pages, search, click, type, tabs |
| code_helper | actions/code_helper.py | Write, edit, explain, run, or build code |
| computer_control | actions/computer_control.py | Mouse, keyboard, screenshots, close apps |
| computer_settings | actions/computer_settings.py | Volume, windows, Wi-Fi, restart, shutdown |
| desktop_control | actions/desktop.py | Wallpaper and desktop file tidy / list |
| dev_agent | actions/dev_agent.py | Multi-file coding agent on a project folder |
| file_controller | actions/file_controller.py | List, create, delete, move, read, write files |
| file_processor | actions/file_processor.py | Act on an uploaded file (image, PDF, and so on) |
| flight_finder | actions/flight_finder.py | Search Google Flights |
| game_updater | actions/game_updater.py | Steam / Epic install, update, list games |
| open_app | actions/open_app.py | Launch an application by name |
| proactive | actions/proactive.py | Placeholder — no TOOL metadata |
| reminder | actions/reminder.py | Timed reminder via Task Scheduler |
| routines | actions/routines.py | Scheduled say / prompt routines |
| screen_processor | actions/screen_processor.py | Placeholder — no TOOL metadata |
| send_message | actions/send_message.py | Send WhatsApp / Telegram text |
| system_monitor | actions/system_monitor.py | Placeholder — no TOOL metadata |
| video_player | actions/video_player.py | Show a video or picture on the assistant display |
| weather_report | actions/weather_report.py | Speak or return a weather report |
| web_search | actions/web_search.py | Web search: search, news, research, price, compare |
| youtube_video | actions/youtube_video.py | Play, summarize, or describe YouTube videos |

## Safe for a first real test

These are read-only or low impact if arguments are constrained:

- `weather_report` — small, no filesystem writes
- `web_search` — read-only network search (needs network; Gemini key only if that backend is used)
- `desktop_control` with `action=list` — lists desktop files, does not tidy or change wallpaper
- `flight_finder` — search only
- `youtube_video` when limited to info / summarize, not play

Leave these until there is an allowlist. They change the machine or send messages:

- `computer_settings` (includes restart and shutdown)
- `computer_control`, `browser_control`, `open_app`
- `file_controller`, `file_processor`, `code_helper`, `dev_agent` (write and run)
- `send_message`, `reminder`, `routines`, `game_updater`, `desktop_control` wallpaper / clean

## Simulated dry-run

`POST /tools/run` still does **not** call Jarvis. It looks the name up and returns a simulated result. All three calls returned HTTP 200, `success: true`, `simulated: true`.

| Tool | Arguments | Result |
| --- | --- | --- |
| web_search | query=`python tutorials`, mode=`search` | Simulated success, source `actions/web_search.py` |
| weather_report | city=`Mumbai` | Simulated success, source `actions/weather_report.py` |
| desktop_control | action=`list` | Simulated success, source `actions/desktop.py` |

Message on each: `simulated execution — Mark-LVII integration pending`.

## Imports

With `D:\final year proejct\mark_lvii\mark_lvii` on `sys.path`, every non-skipped module under `actions/` imported. No `ModuleNotFoundError` during that probe. Runtime calls can still fail later if a tool touches an optional package, an API key, or a Windows app that is not installed. The bridge executor does not import these modules yet.

## Tests added

- `tests/test_mark_lvii_discovery.py` — registry load, at least 5 tools, simulated dry-run of the three tools above
- `scripts/inspect_mark_lvii.py` — reprints discovery plus import OK/FAIL lines

## Recommended next step

Keep discovery on the real clone path (`MARK_LVII_PATH` as above). Wire real execution for `weather_report` first, then `web_search`, then `desktop_control` limited to `action=list`. Do not enable `file_controller` or `computer_settings` until names and arguments are allowlisted.
