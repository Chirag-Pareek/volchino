"""FastAPI application factory: ``/health``, ``WS /ws``, and static mount for the PWA."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from server import auth, db
from server.android import AdbClient
from server.audio import stt, tts
from server.config import Settings
from server.hub import Hub
from server.llm.groq import GroqClient
from server.obsidian import R2SyncBridge, schedule_daily_report_loop
from server.pet import PetService
from server.pipeline.cache import Cache
from server.pipeline.executor import Executor
from server.pipeline.pipeline import Pipeline
from server.pipeline.router import GroqRouter
from server.reasoning.opencode import OpenCodeReasoningClient
from server.telemetry import ActivityTracker
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
        adb_client = AdbClient(settings=settings, runner=runner, broadcast=hub.broadcast)
        ctx = ToolContext(
            settings=settings,
            runner=runner,
            db=database,
            broadcast=hub.broadcast,
            adb=adb_client,
        )
        executor = Executor(REGISTRY, ctx)

        if settings.adb_auto_reconnect and settings.adb_device_id:
            adb_client.start_watchdog()

        groq_client: GroqClient | None = None
        if settings.groq_api_key:
            groq_client = GroqClient(settings.groq_api_key, settings.groq_model)
        router = GroqRouter(groq_client, cache, REGISTRY)
        proposer = OpenCodeReasoningClient(api_key=settings.opencode_api_key)

        pipeline = Pipeline(
            db=database,
            registry=REGISTRY,
            executor=executor,
            cache=cache,
            router=router,
            proposer=proposer,
            pet=pet,
        )

        tracker = ActivityTracker(database, settings)
        await tracker.start()

        r2_sync = R2SyncBridge(settings)
        await r2_sync.start()

        report_task = asyncio.create_task(
            schedule_daily_report_loop(database, settings),
            name="daily_report_scheduler",
        )

        state.update(
            db=database,
            hub=hub,
            pet=pet,
            pipeline=pipeline,
            runner=runner,
            adb=adb_client,
            groq_client=groq_client,
            tracker=tracker,
            r2_sync=r2_sync,
        )
        app.state.volchino = state
        log.info(
            "Volchino agent started (dry_run=%s, groq=%s, stt=%s, tts=%s, adb=%s)",
            settings.dry_run,
            bool(groq_client),
            settings.stt_engine,
            settings.tts_engine,
            settings.adb_device_id or "local/none",
        )
        yield
        report_task.cancel()
        await tracker.stop()
        await r2_sync.stop()
        await adb_client.stop_watchdog()
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
        cfg: Settings = s["settings"]
        if not auth.token_valid(token, cfg.auth_token):
            await ws.close(code=4001, reason="unauthorized")
            return
        await ws.accept()
        hub: Hub = s["hub"]
        pipeline: Pipeline = s["pipeline"]
        pet: PetService = s["pet"]
        await pet.wake()
        await hub.add(ws)
        try:
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
                    if outcome.skill_proposal:
                        await hub.send(
                            ws,
                            {
                                "type": "skill_proposal",
                                "name": outcome.skill_proposal["name"],
                                "steps": outcome.skill_proposal.get("steps", []),
                                "description": outcome.skill_proposal.get("description", ""),
                            },
                        )
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
                elif msg_type == "audio_chunk":
                    await _handle_audio(ws, msg, s)
                elif msg_type == "approve_skill":
                    from server.skills.engine import approve_skill

                    skill_name = (msg.get("name") or "").strip()
                    ok = await approve_skill(s["db"], skill_name)
                    if ok:
                        await hub.send(
                            ws,
                            {
                                "type": "result",
                                "text": f"Skill '{skill_name}' approved and active at 0 tokens.",
                                "status": "success",
                                "stage": "skills",
                                "tool": None,
                                "tokens_used": 0,
                            },
                        )
                    else:
                        await hub.send(
                            ws,
                            {
                                "type": "error",
                                "text": (
                                    f"Skill '{skill_name}' could not be approved "
                                    "(not found or already approved)."
                                ),
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


async def _handle_audio(ws: WebSocket, msg: dict[str, Any], s: dict[str, Any]) -> None:
    """Process an incoming audio_chunk: STT -> pipeline -> TTS -> voice_response."""
    hub: Hub = s["hub"]
    pipeline: Pipeline = s["pipeline"]
    pet: PetService = s["pet"]
    cfg: Settings = s["settings"]

    data_b64 = msg.get("data", "")
    if not data_b64:
        await hub.send(ws, {"type": "error", "text": "Empty audio data."})
        return

    try:
        pcm_bytes = base64.b64decode(data_b64)
    except Exception:
        await hub.send(ws, {"type": "error", "text": "Invalid base64 audio data."})
        return

    # Pet -> listening while we transcribe
    await pet.transition_safe("listening")

    # STT
    try:
        transcript = await stt.transcribe_pcm(pcm_bytes, model_size=cfg.stt_model)
    except Exception:
        log.exception("STT failed")
        await hub.send(ws, {"type": "error", "text": "Speech recognition failed."})
        await pet.transition_safe("error")
        return

    if not transcript.strip():
        await hub.send(ws, {"type": "error", "text": "Could not understand the audio."})
        await pet.transition_safe("idle")
        return

    # Send the transcript so the client can show what was heard
    await hub.send(ws, {"type": "transcript", "text": transcript})

    # Run through the pipeline
    outcome = await pipeline.run(transcript)
    await hub.send(ws, outcome.result_message())
    if outcome.skill_proposal:
        await hub.send(
            ws,
            {
                "type": "skill_proposal",
                "name": outcome.skill_proposal["name"],
                "steps": outcome.skill_proposal.get("steps", []),
                "description": outcome.skill_proposal.get("description", ""),
            },
        )

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
        return  # wait for user to confirm before TTS

    # TTS
    try:
        audio_b64, audio_fmt = await tts.synthesize_b64(outcome.text, voice=cfg.tts_voice)
    except Exception:
        log.exception("TTS failed")
        audio_b64, audio_fmt = "", "audio/mp3"

    await hub.send(
        ws,
        {
            "type": "voice_response",
            "text": outcome.text,
            "audio": audio_b64,
            "format": audio_fmt,
            "tool": outcome.tool or "",
        },
    )
