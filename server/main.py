"""FastAPI application factory: ``/health``, ``WS /ws``, and static mount for the PWA."""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server import auth, db
from server.config import Settings
from server.hub import Hub
from server.llm.groq import GroqClient
from server.llm.opencode import OpenCodeStub
from server.pet import PetService
from server.pipeline.cache import Cache
from server.pipeline.executor import Executor
from server.pipeline.pipeline import Pipeline
from server.pipeline.router import GroqRouter
from server.tools import REGISTRY, DryRunRunner, SubprocessRunner, ToolContext

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state: dict[str, Any] = {"settings": settings}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = await db.connect(settings.sqlite_path)
        runner = DryRunRunner() if settings.dry_run else SubprocessRunner()
        hub = Hub()
        pet = PetService(
            database,
            hub.broadcast,
            success_timeout_s=settings.pet_success_timeout_s,
            sleep_after_s=settings.pet_sleep_after_s,
        )
        cache = Cache(database)
        ctx = ToolContext(settings=settings, runner=runner, db=database)
        executor = Executor(REGISTRY, ctx)

        groq_client: GroqClient | None = None
        if settings.groq_api_key:
            groq_client = GroqClient(settings.groq_api_key, settings.groq_model)
        router = GroqRouter(groq_client, cache, REGISTRY)
        proposer = OpenCodeStub()

        pipeline = Pipeline(
            db=database,
            registry=REGISTRY,
            executor=executor,
            cache=cache,
            router=router,
            proposer=proposer,
            pet=pet,
        )

        state.update(
            db=database,
            hub=hub,
            pet=pet,
            pipeline=pipeline,
            runner=runner,
            groq_client=groq_client,
        )
        app.state.volchino = state
        log.info(
            "Volchino agent started (dry_run=%s, groq=%s)", settings.dry_run, bool(groq_client)
        )
        yield
        await pet.close()
        if groq_client:
            await groq_client.aclose()
        await database.close()
        log.info("Volchino agent stopped")

    app = FastAPI(title="Volchino Agent", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok"})

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket, token: str = Query("")):
        s: dict[str, Any] = app.state.volchino
        if not auth.token_valid(token, s["settings"].auth_token):
            await ws.close(code=4001, reason="unauthorized")
            return
        await ws.accept()
        hub: Hub = s["hub"]
        pipeline: Pipeline = s["pipeline"]
        pet: PetService = s["pet"]
        await hub.add(ws)
        try:
            await pet.wake()
            snap = await pet.snapshot()
            await hub.send(ws, snap)
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await hub.send(ws, {"type": "error", "text": "Invalid JSON."})
                    continue
                msg_type = msg.get("type")
                if msg_type == "text":
                    text = (msg.get("text") or "").strip()
                    if not text:
                        continue
                    outcome = await pipeline.run(text)
                    await hub.send(ws, outcome.result_message())
                    if outcome.pending:
                        await hub.send(
                            ws,
                            {
                                "type": "confirm",
                                "id": outcome.pending.id,
                                "action": outcome.pending.action,
                                "kind": outcome.pending.kind,
                            },
                        )
                elif msg_type == "confirm":
                    pending_id = msg.get("id", "")
                    approved = bool(msg.get("approved"))
                    outcome = await pipeline.resolve_confirmation(pending_id, approved)
                    await hub.send(ws, outcome.result_message())
                elif msg_type == "wake":
                    await pet.wake()
                elif msg_type == "acknowledge_error":
                    await pet.acknowledge_error()
                else:
                    await hub.send(
                        ws, {"type": "error", "text": f"Unknown message type: {msg_type!r}"}
                    )
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("ws handler crashed")
        finally:
            await hub.remove(ws)

    # Serve PWA static files
    if settings.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(settings.web_dir), html=True), name="web")

    return app
