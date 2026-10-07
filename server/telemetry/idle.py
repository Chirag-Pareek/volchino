"""Idle detection using hypridle, loginctl session status, hyprctl DPMS, and input silence."""

from __future__ import annotations

import asyncio
import contextlib
import glob
import json
import logging
import os
import select
import time

from server.tools.session_env import desktop_env

log = logging.getLogger(__name__)


class IdleDetector:
    """Detects whether the user is currently idle.

    Uses multiple telemetry signals:
    1. loginctl session lock or idle hint (triggered by hypridle / screen locker)
    2. hyprctl monitors DPMS status (monitors turned off)
    3. Input silence (no /dev/input activity for > idle_threshold_s)
    """

    def __init__(
        self,
        idle_threshold_s: float = 180.0,
        enable_input_listener: bool = True,
    ) -> None:
        self.idle_threshold_s = idle_threshold_s
        self.enable_input_listener = enable_input_listener
        self._last_input_time = time.monotonic()
        self._input_fds: list[int] = []
        self._session_id: str = os.environ.get("XDG_SESSION_ID", "")
        self._override_idle: bool | None = None  # for testing

        if self.enable_input_listener:
            self._init_input_devices()

    def _init_input_devices(self) -> None:
        """Open readable /dev/input/event* nodes in non-blocking mode."""
        device_paths = sorted(glob.glob("/dev/input/event*"))
        for path in device_paths:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                self._input_fds.append(fd)
            except (OSError, PermissionError):
                continue
        if self._input_fds:
            log.debug("Opened %d input device fds for idle detection", len(self._input_fds))

    def record_activity(self) -> None:
        """Explicitly signal user activity (e.g., on activewindow or workspace change)."""
        self._last_input_time = time.monotonic()

    def set_override_idle(self, idle: bool | None) -> None:
        """Override idle state for testing."""
        self._override_idle = idle

    def _poll_input_silence(self) -> None:
        """Poll open input fds without blocking and update activity time if data arrived."""
        if not self._input_fds:
            return
        try:
            readable, _, _ = select.select(self._input_fds, [], [], 0)
            if readable:
                self.record_activity()
                for fd in readable:
                    with contextlib.suppress(OSError):
                        # Drain up to 512 bytes so buffer doesn't stay readable
                        os.read(fd, 512)
        except (OSError, ValueError):
            pass

    async def _check_loginctl(self) -> bool:
        """Check if session is locked or IdleHint is yes."""
        cmd = ["loginctl", "show-session"]
        if self._session_id:
            cmd.append(self._session_id)
        cmd.extend(["-p", "LockedHint", "-p", "IdleHint"])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.0)
            text = stdout.decode("utf-8", errors="replace")
            for line in text.splitlines():
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().lower()
                if k == "LockedHint" and v == "yes":
                    return True
                if k == "IdleHint" and v == "yes":
                    return True
        except (TimeoutError, OSError):
            pass
        return False

    async def _check_hyprctl_dpms(self) -> bool:
        """Check if display DPMS is turned off (monitors sleeping)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "hyprctl",
                "monitors",
                "-j",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=desktop_env(),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=1.0)
            monitors = json.loads(stdout.decode("utf-8", errors="replace"))
            if (
                isinstance(monitors, list)
                and monitors
                and all(not m.get("dpmsStatus", True) for m in monitors)
            ):
                return True
        except (TimeoutError, OSError, Exception):
            pass
        return False

    async def is_idle(self) -> bool:
        """Determine whether the system is currently idle."""
        if self._override_idle is not None:
            return self._override_idle

        # 1. Check session lock / hypridle trigger via loginctl
        if await self._check_loginctl():
            return True

        # 2. Check DPMS off (screens dark)
        if await self._check_hyprctl_dpms():
            return True

        # 3. Poll hardware input devices
        self._poll_input_silence()
        silence_duration = time.monotonic() - self._last_input_time
        return silence_duration > self.idle_threshold_s

    def close(self) -> None:
        """Close opened input device file descriptors."""
        for fd in self._input_fds:
            with contextlib.suppress(OSError):
                os.close(fd)
        self._input_fds.clear()
