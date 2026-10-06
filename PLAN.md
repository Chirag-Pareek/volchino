# Build Plan — Phases 1–3 + Phase 5 router skeleton

Source of truth: `Personal_AI_Agent_System.md`.

## Layout
```
server/
  config.py        Settings from env (python-dotenv, never logged)
  db.py            aiosqlite, WAL, §9 schema, seeded pet_state
  auth.py          constant-time token check
  permissions.py   Set A / Set B; anything not in Set A needs confirmation
  pet.py           §7 state machine + evolution + WS push
  hub.py           WebSocket connection hub (broadcast)
  main.py          FastAPI app factory: /health, /ws, static mount of web/
  __main__.py      uvicorn, hard-bound to 127.0.0.1
  llm/             groq.py (httpx, mockable), opencode.py (stub)
  pipeline/        normalize, cache, memory, skills, deterministic, router, fallback, executor, pipeline
  tools/           base (runner, timeouts, no shell), aliases, hyprland, system, memory_tools, registry
web/               vanilla-JS PWA (index.html, app.js, avatar.js, styles.css, sw.js, manifest, icons)
deploy/            systemd --user unit, wrapper script, setup.md
scripts/           ws_client.py (live verification), gen_icons.py
tests/             pytest
```

## Pipeline (§3)
normalize → cache (`result:` keys, TTL; only read-only tools are cached) → memory
(history/preferences/pet) → approved skills → deterministic regex+aliases → Groq router
(route cache, `{tool,args}` | `needs_reasoning`) → OpenCode stub (saves `draft` skill).
Each tool call goes through the permission gate: Set A runs, otherwise a `confirm` message is sent.

## Steps
1. Scaffold (pyproject, uv, ruff, Makefile) → commit
2. config/db/auth/permissions/pet → commit
3. tools + tests → commit
4. pipeline stages + tests → commit
5. FastAPI/WS + tests → commit
6. PWA → commit
7. deploy files → commit
8. pytest + ruff green, live WS verification (dry-run runner) → README → commit
