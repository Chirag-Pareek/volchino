"""Discover the desktop session env (Hyprland signature, Wayland display) at call time.

Hyprland gets a new instance signature on every restart, so a value captured when the service
started can go stale. We re-check on each call and fall back to the newest live instance.
"""

from __future__ import annotations

import os
from pathlib import Path


def runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def discover_hyprland_signature(rt: Path | None = None) -> str | None:
    hypr = (rt or runtime_dir()) / "hypr"
    if not hypr.is_dir():
        return None
    candidates = [d for d in hypr.iterdir() if d.is_dir() and (d / ".socket.sock").exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime).name


def discover_wayland_display(rt: Path | None = None) -> str | None:
    base = rt or runtime_dir()
    if not base.is_dir():
        return None
    socks = sorted(p.name for p in base.glob("wayland-*") if not p.name.endswith(".lock"))
    return socks[0] if socks else None


def desktop_env() -> dict[str, str]:
    env = dict(os.environ)
    rt = runtime_dir()
    env.setdefault("XDG_RUNTIME_DIR", str(rt))
    sig = env.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig or not (rt / "hypr" / sig / ".socket.sock").exists():
        found = discover_hyprland_signature(rt)
        if found:
            env["HYPRLAND_INSTANCE_SIGNATURE"] = found
    if not env.get("WAYLAND_DISPLAY"):
        wl = discover_wayland_display(rt)
        if wl:
            env["WAYLAND_DISPLAY"] = wl
    return env
