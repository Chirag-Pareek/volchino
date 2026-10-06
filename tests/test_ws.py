"""Test the WebSocket round-trip: auth rejection, text flow, confirm flow."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from server.main import create_app


@pytest.fixture()
def app(tmp_path, settings):
    return create_app(settings)


@pytest.fixture()
def client(app):
    # Use the context manager so the lifespan runs (db/hub/pipeline are initialised).
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ws_auth_rejection(client):
    try:
        with client.websocket_connect("/ws?token=wrong") as ws:
            ws.receive_json()
            pytest.fail("Should have been disconnected")
    except Exception:
        pass  # closed as expected


def test_ws_auth_no_token(client):
    try:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            pytest.fail("Should have been disconnected")
    except Exception:
        pass


def test_ws_roundtrip_volume(client, settings):
    with client.websocket_connect(f"/ws?token={settings.auth_token}") as ws:
        # First message = pet snapshot
        pet = ws.receive_json()
        assert pet["type"] == "pet"

        # Send a volume command
        ws.send_json({"type": "text", "text": "volume 30%"})

        # Collect messages until we get a result
        result = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "result":
                result = msg
                break
        assert result is not None
        assert result["status"] == "success"
        assert result["tokens_used"] == 0
        assert result["tool"] == "set_volume"


def test_ws_roundtrip_open_firefox(client, settings):
    with client.websocket_connect(f"/ws?token={settings.auth_token}") as ws:
        ws.receive_json()  # pet
        ws.send_json({"type": "text", "text": "open firefox"})
        result = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "result":
                result = msg
                break
        assert result is not None
        assert result["status"] == "success"
        assert result["tokens_used"] == 0


def test_ws_roundtrip_work_time(client, settings):
    with client.websocket_connect(f"/ws?token={settings.auth_token}") as ws:
        ws.receive_json()  # pet
        ws.send_json({"type": "text", "text": "what's my work time today"})
        result = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "result":
                result = msg
                break
        assert result is not None
        assert result["status"] == "success"
        assert result["tokens_used"] == 0
        assert "0h 0m" in result["text"]


def test_ws_roundtrip_unknown_falls_to_draft(client, settings):
    with client.websocket_connect(f"/ws?token={settings.auth_token}") as ws:
        ws.receive_json()  # pet
        ws.send_json({"type": "text", "text": "deploy my kubernetes cluster to mars"})
        result = None
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "result":
                result = msg
            elif msg["type"] == "confirm":
                pass  # confirm received
            if result:
                break
        assert result is not None
        # Should produce a draft skill and request approval
        assert "draft" in result["text"].lower() or result["status"] == "pending_confirmation"


def test_ws_approve_skill(client, settings):
    with client.websocket_connect(f"/ws?token={settings.auth_token}") as ws:
        ws.receive_json()  # pet

        # First trigger an unknown command that creates a draft skill and skill_proposal
        ws.send_json({"type": "text", "text": "start morning coffee routine"})
        draft_name = None
        has_proposal = False

        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "skill_proposal":
                has_proposal = True
                draft_name = msg["name"]
            elif msg["type"] == "confirm" and msg.get("kind") == "approve_skill":
                if not draft_name:
                    draft_name = msg.get("action", "").split("'")[1]
            if has_proposal and draft_name:
                break

        assert has_proposal is True
        assert draft_name is not None

        # Now send approve_skill message
        ws.send_json({"type": "approve_skill", "name": draft_name})
        result = None
        for _ in range(5):
            msg = ws.receive_json()
            if msg["type"] == "result":
                result = msg
                break

        assert result is not None
        assert result["status"] == "success"
        assert "approved" in result["text"].lower()
