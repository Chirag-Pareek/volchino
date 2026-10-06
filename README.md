# Volchino Personal AI Agent

A local-first, privacy-respecting personal AI agent for Arch Linux + Hyprland.
The laptop is the deterministic server core; a Redmi Note 8 Pro runs a Chrome-installed
PWA (CyberCat avatar + text input) over Tailscale.

## Quick start

```bash
# 1. Install (uv must be on PATH)
make install          # downloads Python 3.12 via uv, installs deps

# 2. Configure
cp .env.example .env  # then edit: set AUTH_TOKEN and optionally GROQ_API_KEY

# 3. Run
make dev              # dry-run mode (tools log their argv instead of executing)
make run              # real mode (binds to 127.0.0.1:8765)

# 4. Test & lint
make test             # pytest (61 tests)
make lint             # ruff check + format

# 5. Generate PWA icons
make icons
```

Open `http://127.0.0.1:8765/?token=<AUTH_TOKEN>` in a browser to use the PWA locally,
or follow `deploy/setup.md` to expose it over Tailscale HTTPS and install it on Android.

## Architecture

```
server/
  config.py          Settings from .env (python-dotenv; secrets never logged)
  db.py              aiosqlite, WAL mode, spec section 9 schema, seeded pet_state
  auth.py            Constant-time token comparison
  permissions.py     Set A (safe) / Set B (confirmation required)
  pet.py             Tamagotchi state machine with evolution stages
  hub.py             WebSocket connection hub
  main.py            FastAPI app factory: /health, WS /ws, static mount
  __main__.py        uvicorn entrypoint, hard-bound to 127.0.0.1
  llm/
    groq.py          Groq router client (httpx, mockable)
    opencode.py      OpenCode fallback stub (TODO for Phase 5+)
  pipeline/
    types.py         ToolCall, Plan, Outcome, PendingAction
    normalize.py     Stage 0: normalize raw utterance
    cache.py         Stage 1: TTL cache (result: and route: keys)
    memory.py        Stage 2: answer from SQLite (history, preferences, pet)
    skills.py        Stage 3: approved skills only
    deterministic.py Stage 4: regex + alias map (0 tokens)
    router.py        Stage 5: Groq cheap router skeleton
    fallback.py      Stage 6: OpenCode stub -> draft skill
    executor.py      Permission gate + tool runner
    pipeline.py      Orchestrator (spec section 3 order)
  tools/
    base.py          Runner protocol, DryRunRunner, Tool dataclass, arg validation
    aliases.py       Alias maps ("vs code" -> code, URL aliases)
    session_env.py   Discovers HYPRLAND_INSTANCE_SIGNATURE + WAYLAND_DISPLAY
    hyprland.py      open_app, close_window, switch_workspace, focus_window
    system.py        take_screenshot, set_volume, get_battery, git_status, open_url
    memory_tools.py  get_work_time_today (SQLite aggregation)
web/                 Installable PWA (vanilla JS, no build step)
deploy/              systemd unit, start.sh wrapper, setup.md
tests/               pytest (61 tests)
scripts/             ws_client.py (live verification), gen_icons.py
```

## Pipeline (spec section 3)

```
User text -> normalize -> cache -> SQLite memory -> approved skills
          -> deterministic regex -> Groq router -> OpenCode fallback (draft skill)
```

Each stage short-circuits: if it resolves the request, later stages are skipped.
Deterministic tools run at 0 tokens. The Groq router caches its classifications.
The OpenCode fallback is a stub that saves a draft skill -- it never executes until approved.

## Hard rules enforced

| Rule | How |
|------|-----|
| Never read/log .env | `python-dotenv`, secret fields excluded from `repr()`, no logging of env values |
| Set A tools run without confirmation | `permissions.py` gates every tool call |
| Set B actions need WebSocket confirmation | Confirmation message sent; pipeline parks until user approves/declines |
| Draft skills never execute | `skills.py` only matches `status='approved'`; fallback saves as `'draft'` |
| Bind to 127.0.0.1 only | Hard-coded in `__main__.py`, overrides any SERVER_HOST env |
| Auth token checked with constant-time compare | `hmac.compare_digest` in `auth.py` |
| No shell=True anywhere | `SubprocessRunner` uses `create_subprocess_exec` |
| Subprocess timeouts | Configurable `tool_timeout_s` with hard kill |

## Design decisions

1. **Python 3.12 via uv**: the system Python is 3.14; uv manages a 3.12 venv as required.
2. **DryRunRunner**: `VOLCHINO_DRY_RUN=1` logs tool argv instead of executing, safe for testing.
3. **open_url permission**: not in spec Set A, so it requires confirmation (fail-closed).
4. **Groq router disabled gracefully**: if `GROQ_API_KEY` is empty, the router stage returns None and the pipeline falls through to the OpenCode stub.
5. **pet_state single-row**: enforced by `CHECK (id = 1)`, seeded on DB creation.
6. **Pending confirmations expire**: after 5 minutes, to prevent stale approvals.
7. **Route cache**: Groq classifications cached for 24h; `needs_reasoning` cached for 1h.
8. **Session env discovery**: `deploy/start.sh` and `tools/session_env.py` both discover the live Hyprland instance so hyprctl works from systemd.

## What is verified

- [x] 79 pytest tests pass (auth, permissions, normalize, cache TTL, deterministic regex, tool arg validation, router parsing, WebSocket round-trip, STT PCM/WAV transcription, edge-tts/pyttsx3 synthesis, WS audio_chunk voice loop)
- [x] WS round-trip: "volume 30%", "open firefox", "what's my work time today" all return tokens_used=0
- [x] WS voice loop: `audio_chunk` PCM -> faster-whisper STT -> pipeline -> TTS -> `voice_response`
- [x] Auth rejection: wrong/missing token -> connection closed
- [x] Unknown requests fall through to draft skill creation
- [x] ruff check and ruff format clean

## What needs real-environment testing

- [ ] Hyprland tools (hyprctl dispatch) -- need a live Hyprland session
- [ ] wpctl (volume), grim (screenshot) -- need PipeWire + Wayland
- [ ] Battery status -- need /sys/class/power_supply/BAT*
- [ ] Groq router -- needs GROQ_API_KEY
- [ ] Tailscale HTTPS proxy -- needs Tailscale setup
- [ ] PWA install on Android Chrome -- needs the phone
- [ ] systemd --user service -- needs the systemd user session
