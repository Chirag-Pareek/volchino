"""Hyprland socket2 IPC listener and state query helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from server.tools.session_env import desktop_env, discover_hyprland_signature, runtime_dir

log = logging.getLogger(__name__)


def get_hyprland_socket2_path() -> Path | None:
    """Find the live Hyprland socket2 path."""
    rt = runtime_dir()
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        sig = discover_hyprland_signature(rt)
    if not sig:
        return None

    path = rt / "hypr" / sig / ".socket2.sock"
    if path.exists():
        return path

    # Fallback search under rt/hypr
    hypr_dir = rt / "hypr"
    if hypr_dir.is_dir():
        candidates = list(hypr_dir.glob("*/.socket2.sock"))
        if candidates:
            return max(candidates, key=lambda p: p.stat().st_mtime)

    return None


def parse_event_line(line: str) -> tuple[str, str] | None:
    """Parse a single Hyprland socket2 event line of form 'event>>data'."""
    line = line.strip()
    if not line or ">>" not in line:
        return None
    event, _, data = line.partition(">>")
    return event.strip(), data.strip()


def parse_activewindow_data(data: str) -> tuple[str, str]:
    """Parse the data string from an activewindow event into (class, title)."""
    if not data or data == ",":
        return "", ""
    klass, _, title = data.partition(",")
    return klass.strip(), title.strip()


async def query_initial_state() -> tuple[str, str, str]:
    """Query current active window (class, title) and workspace via hyprctl."""
    window_class, window_title, workspace = "", "", "1"
    env = desktop_env()

    # Query active window
    try:
        proc = await asyncio.create_subprocess_exec(
            "hyprctl",
            "activewindow",
            "-j",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.0)
        data = json.loads(stdout.decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            window_class = data.get("class", "")
            window_title = data.get("title", "")
            ws = data.get("workspace", {})
            if isinstance(ws, dict):
                workspace = str(ws.get("name") or ws.get("id") or "1")
    except Exception:
        pass

    # Query active workspace if not yet discovered
    if workspace == "1":
        try:
            proc = await asyncio.create_subprocess_exec(
                "hyprctl",
                "activeworkspace",
                "-j",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.0)
            data = json.loads(stdout.decode("utf-8", errors="replace"))
            if isinstance(data, dict):
                workspace = str(data.get("name") or data.get("id") or "1")
        except Exception:
            pass

    return window_class, window_title, workspace


async def stream_socket_events(
    reader: asyncio.StreamReader,
) -> AsyncIterator[tuple[str, str]]:
    """Yield parsed events (event_name, data) from a stream reader."""
    while True:
        line_bytes = await reader.readline()
        if not line_bytes:
            break
        line = line_bytes.decode("utf-8", errors="replace")
        parsed = parse_event_line(line)
        if parsed:
            yield parsed
