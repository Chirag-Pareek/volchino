"""Async ADB Manager & Wireless Bridge ('Ghost in the Droid' pattern).

Connects over Wi-Fi/Tailscale IP, manages connection lifecycle with auto-reconnect
watchdog, executes safe mobile actions (Set A), and guards destructive actions (Set B).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from server.config import Settings

log = logging.getLogger(__name__)

Broadcast = Callable[[dict[str, Any]], Awaitable[None]]

_PKG_RE = re.compile(r"^[a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+$")
_APP_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_.]+$")

PHONE_APP_ALIASES: dict[str, str] = {
    "youtube": "com.google.android.youtube",
    "spotify": "com.spotify.music",
    "music": "com.spotify.music",
    "whatsapp": "com.whatsapp",
    "chrome": "com.android.chrome",
    "browser": "com.android.chrome",
    "camera": "com.android.camera",
    "settings": "com.android.settings",
    "termux": "com.termux",
    "maps": "com.google.android.apps.maps",
    "google maps": "com.google.android.apps.maps",
    "photos": "com.google.android.apps.photos",
    "gallery": "com.google.android.apps.photos",
    "clock": "com.google.android.deskclock",
    "calendar": "com.google.android.calendar",
    "telegram": "org.telegram.messenger",
    "phone": "com.google.android.dialer",
    "dialer": "com.google.android.dialer",
    "messages": "com.google.android.apps.messaging",
    "messaging": "com.google.android.apps.messaging",
    "gmail": "com.google.android.gm",
    "mail": "com.google.android.gm",
    "calculator": "com.google.android.calculator",
    "notes": "com.miui.notes",
    "files": "com.google.android.documentsui",
}


def resolve_phone_app(name: str) -> str:
    key = " ".join(name.lower().split())
    return PHONE_APP_ALIASES.get(key, key)


@dataclass(frozen=True)
class CmdResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    async def run(
        self, argv: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None
    ) -> CmdResult: ...

    def which(self, name: str) -> str | None: ...


class SubprocessRunner:
    """Runs argv lists via create_subprocess_exec (no shell) with a hard timeout."""

    async def run(
        self, argv: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None
    ) -> CmdResult:
        if not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a non-empty list of strings")
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(env) if env is not None else None,
            )
        except FileNotFoundError as e:
            raise AdbError(f"{argv[0]} is not installed") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError as e:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise AdbError(f"{argv[0]} timed out after {timeout:.0f}s") from e
        return CmdResult(
            proc.returncode or 0,
            out.decode(errors="replace").strip(),
            err.decode(errors="replace").strip(),
        )

    def which(self, name: str) -> str | None:
        return shutil.which(name)


class AdbError(RuntimeError):
    """An ADB command failed or device is unreachable."""


class AdbClient:
    """Async wireless ADB client managing device connection, watchdog, and mobile actions."""

    def __init__(
        self,
        settings: Settings | None = None,
        runner: Runner | None = None,
        broadcast: Broadcast | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.runner = runner or SubprocessRunner()
        self.broadcast = broadcast
        self._watchdog_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def device_id(self) -> str:
        return self.settings.adb_device_id.strip()

    @property
    def port(self) -> int:
        return self.settings.adb_port or 5555

    @property
    def target_address(self) -> str:
        """Formatted target address for adb -s or adb connect (e.g. 100.x.y.z:5555)."""
        raw = self.device_id
        if not raw:
            return ""
        if ":" in raw:
            return raw
        return f"{raw}:{self.port}"

    @property
    def _target_args(self) -> list[str]:
        target = self.target_address
        return ["-s", target] if target else []

    # -- Status broadcast ----------------------------------------------------------------

    async def _emit_status(self, status: str, action: str = "", **extra: Any) -> None:
        """Send status event over WebSocket so the UI / avatar can display phone state."""
        if not self.broadcast:
            return
        payload = {
            "type": "adb_status",
            "status": status,
            "device": self.target_address or "default",
            "action": action,
            "target": "phone",
            **extra,
        }
        try:
            await self.broadcast(payload)
        except Exception:
            log.debug("Failed to emit ADB status to WebSocket", exc_info=True)

    # -- Command execution & connectivity -----------------------------------------------

    async def run_adb(
        self,
        args: Sequence[str],
        *,
        timeout: float | None = None,
        retry_on_disconnect: bool = True,
    ) -> CmdResult:
        """Execute an adb command with target routing and optional auto-reconnect retry."""
        if self.runner.which("adb") is None:
            raise AdbError("adb is not installed on this system")

        argv = ["adb", *self._target_args, *args]
        to = timeout or self.settings.tool_timeout_s
        res = await self.runner.run(argv, timeout=to)

        if res.returncode != 0:
            detail = (res.stderr or res.stdout or "").strip()
            # Detect disconnected / offline states and attempt reconnect once
            err_lower = detail.lower()
            disconnect_triggers = (
                "device offline",
                "no devices",
                "device not found",
                "device unauthorized",
                "closed",
                "not found",
            )
            if retry_on_disconnect and any(t in err_lower for t in disconnect_triggers):
                log.warning("ADB execution encountered '%s'. Attempting auto-reconnect...", detail)
                reconnected = await self.connect()
                if reconnected:
                    return await self.run_adb(args, timeout=timeout, retry_on_disconnect=False)

            first_line = detail.splitlines()[0] if detail else "unknown error"
            raise AdbError(f"adb {' '.join(args[:2])} failed ({res.returncode}): {first_line}")

        return res

    async def is_connected(self) -> bool:
        """Check if target device is currently attached and online."""
        if self.runner.which("adb") is None:
            return False
        try:
            res = await self.runner.run(["adb", "devices"], timeout=self.settings.tool_timeout_s)
        except Exception:
            return False

        lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
        target = self.target_address

        for line in lines[1:]:  # skip 'List of devices attached'
            parts = line.split()
            if len(parts) >= 2:
                dev, state = parts[0], parts[1]
                if target:
                    if dev == target and state == "device":
                        return True
                else:
                    if state == "device":
                        return True
        return False

    async def connect(self, target: str | None = None) -> bool:
        """Connect to wireless ADB target over Wi-Fi / Tailscale."""
        if self.runner.which("adb") is None:
            return False
        addr = target or self.target_address
        if not addr:
            return await self.is_connected()

        try:
            res = await self.runner.run(
                ["adb", "connect", addr], timeout=self.settings.tool_timeout_s
            )
            out = (res.stdout + " " + res.stderr).lower()
            ok = "connected to" in out or "already connected to" in out
            if ok:
                log.info("Connected to wireless ADB: %s", addr)
                await self._emit_status("connected", action="connect")
            else:
                log.warning("Failed to connect wireless ADB: %s (%s)", addr, out)
            return ok
        except Exception as e:
            log.warning("Error connecting to wireless ADB (%s): %s", addr, e)
            return False

    async def disconnect(self, target: str | None = None) -> bool:
        """Disconnect wireless ADB target."""
        if self.runner.which("adb") is None:
            return True
        addr = target or self.target_address
        if not addr:
            return True
        try:
            await self.runner.run(
                ["adb", "disconnect", addr], timeout=self.settings.tool_timeout_s
            )
            return True
        except Exception:
            return False

    async def ensure_connected(self) -> bool:
        """Verify device is connected, reconnecting if disconnected."""
        if await self.is_connected():
            return True
        return await self.connect()

    # -- Watchdog ------------------------------------------------------------------------

    def start_watchdog(self, interval_s: float = 30.0) -> None:
        """Start auto-reconnect watchdog in the background."""
        if self._watchdog_task and not self._watchdog_task.done():
            return
        if not self.target_address:
            log.debug("No ADB_DEVICE_ID configured; watchdog inactive")
            return
        self._watchdog_task = asyncio.create_task(self._watchdog_loop(interval_s))
        log.info(
            "ADB auto-reconnect watchdog started for %s (interval %.1fs)",
            self.target_address,
            interval_s,
        )

    async def stop_watchdog(self) -> None:
        """Stop auto-reconnect watchdog."""
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog_task
        self._watchdog_task = None

    async def _watchdog_loop(self, interval_s: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval_s)
                if not await self.is_connected():
                    log.info(
                        "ADB watchdog: connection lost to %s. Reconnecting...",
                        self.target_address,
                    )
                    reconnected = await self.connect()
                    if reconnected:
                        log.info(
                            "ADB watchdog: reconnected successfully to %s",
                            self.target_address,
                        )
                        await self._emit_status("connected", action="watchdog_reconnect")
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("ADB watchdog loop encountered an unexpected error")

    # -- Safe Actions (Set A) ------------------------------------------------------------

    async def open_phone_app(self, package: str, activity: str | None = None) -> str:
        """Launch an application on the phone."""
        raw_pkg = package.strip()
        resolved_pkg = resolve_phone_app(raw_pkg)

        if not _APP_TOKEN_RE.fullmatch(resolved_pkg):
            raise AdbError(f"Invalid phone package or application name: {raw_pkg!r}")

        act = activity.strip() if activity else None
        if act and not _APP_TOKEN_RE.fullmatch(act):
            raise AdbError(f"Invalid activity name: {activity!r}")

        await self._emit_status("executing", action="open_phone_app", package=resolved_pkg)
        try:
            if act:
                target_component = f"{resolved_pkg}/{act}"
                await self.run_adb(["shell", "am", "start", "-n", target_component])
            else:
                # Monkey launch is universal across all Android launchers without needing activity
                await self.run_adb(
                    [
                        "shell",
                        "monkey",
                        "-p",
                        resolved_pkg,
                        "-c",
                        "android.intent.category.LAUNCHER",
                        "1",
                    ]
                )
            await self._emit_status("success", action="open_phone_app", package=resolved_pkg)
            return f"Launched {resolved_pkg} on phone."
        except Exception as e:
            await self._emit_status("error", action="open_phone_app", error=str(e))
            raise

    async def media_play_pause(self) -> str:
        """Toggle media play/pause on phone (keyevent 85)."""
        await self._emit_status("executing", action="media_play_pause")
        try:
            await self.run_adb(["shell", "input", "keyevent", "85"])
            await self._emit_status("success", action="media_play_pause")
            return "Phone media play/pause toggled."
        except Exception as e:
            await self._emit_status("error", action="media_play_pause", error=str(e))
            raise

    async def media_next(self) -> str:
        """Skip to next media track on phone (keyevent 87)."""
        await self._emit_status("executing", action="media_next")
        try:
            await self.run_adb(["shell", "input", "keyevent", "87"])
            await self._emit_status("success", action="media_next")
            return "Phone media skipped to next track."
        except Exception as e:
            await self._emit_status("error", action="media_next", error=str(e))
            raise

    async def media_prev(self) -> str:
        """Skip to previous media track on phone (keyevent 88)."""
        await self._emit_status("executing", action="media_prev")
        try:
            await self.run_adb(["shell", "input", "keyevent", "88"])
            await self._emit_status("success", action="media_prev")
            return "Phone media skipped to previous track."
        except Exception as e:
            await self._emit_status("error", action="media_prev", error=str(e))
            raise

    async def set_phone_volume(self, level: int) -> str:
        """Set media stream volume on phone (level: 0-100%)."""
        if not (0 <= level <= 100):
            raise AdbError(f"Volume level must be between 0 and 100, got {level}")

        # Android media stream 3 has 0-15 steps
        step = max(0, min(15, round(level * 15 / 100)))

        await self._emit_status("executing", action="set_phone_volume", level=level)
        try:
            # cmd media_session is the standard Android 10+ command
            try:
                await self.run_adb(
                    ["shell", "cmd", "media_session", "volume", "--stream", "3", "--set", str(step)]
                )
            except AdbError:
                # Fallback to legacy media volume command
                await self.run_adb(
                    ["shell", "media", "volume", "--stream", "3", "--set", str(step)]
                )
            await self._emit_status("success", action="set_phone_volume", level=level)
            return f"Phone volume set to {level}%."
        except Exception as e:
            await self._emit_status("error", action="set_phone_volume", error=str(e))
            raise

    async def read_phone_notifications(self) -> str:
        """Read phone notifications via Termux API or dumpsys notification."""
        await self._emit_status("executing", action="read_phone_notifications")
        try:
            notifs = await self._fetch_notifications()
            await self._emit_status("success", action="read_phone_notifications")
            if not notifs:
                return "No active notifications on phone."
            lines = [f"Phone notifications ({len(notifs)} active):"]
            for n in notifs[:8]:
                lines.append(f"• {n}")
            return "\n".join(lines)
        except Exception as e:
            await self._emit_status("error", action="read_phone_notifications", error=str(e))
            raise

    async def _fetch_notifications(self) -> list[str]:
        # 1. Try Termux API first if installed
        try:
            res = await self.run_adb(
                ["shell", "termux-notification-list"], retry_on_disconnect=False
            )
            raw = res.stdout.strip()
            if raw.startswith("[") and raw.endswith("]"):
                items = json.loads(raw)
                formatted = []
                for it in items:
                    app = it.get("appName") or it.get("packageName") or "App"
                    title = it.get("title") or ""
                    content = it.get("content") or ""
                    if title or content:
                        formatted.append(f"{app}: {title} — {content}".strip(" —"))
                if formatted:
                    return formatted
        except Exception:
            pass

        # 2. Dumpsys fallback (universal Android)
        try:
            res = await self.run_adb(["shell", "dumpsys", "notification", "--noredact"])
        except AdbError:
            res = await self.run_adb(["shell", "dumpsys", "notification"])

        return self._parse_dumpsys_notifications(res.stdout)

    @staticmethod
    def _parse_dumpsys_notifications(output: str) -> list[str]:
        records = []
        # Find NotificationRecord blocks
        # Extract package name, title, and text
        pkg_pattern = re.compile(r"pkg=([a-zA-Z0-9_.]+)")
        title_pattern = re.compile(r"android\.title=(?:String \()?(.*?)(?:\)|,\s*android\.|\n|$)")
        text_pattern = re.compile(
            r"(?:android\.bigText|android\.text)=(?:String \()?(.*?)(?:\)|,\s*android\.|\n|$)"
        )

        blocks = output.split("NotificationRecord(")
        noise_packages = {
            "android",
            "com.android.systemui",
            "com.miui.powerkeeper",
            "com.miui.securitycenter",
        }

        for blk in blocks[1:]:
            pkg_m = pkg_pattern.search(blk)
            if not pkg_m:
                continue
            pkg = pkg_m.group(1)
            if pkg in noise_packages:
                continue

            title_m = title_pattern.search(blk)
            text_m = text_pattern.search(blk)

            title = title_m.group(1).strip() if title_m else ""
            body = text_m.group(1).strip() if text_m else ""

            # Clean null or empty representations
            if title.lower() in ("null", ""):
                title = ""
            if body.lower() in ("null", ""):
                body = ""

            if title or body:
                app_name = pkg.split(".")[-1].capitalize()
                desc = f"{app_name}: {title} — {body}".strip(" —")
                if desc not in records:
                    records.append(desc)

        return records

    async def take_phone_screenshot(self, dest_path: Path | None = None) -> str:
        """Capture screenshot on phone and pull to laptop."""
        dest = dest_path or (
            self.settings.screenshot_dir / f"phone_sc_{datetime.now():%Y%m%d_%H%M%S}.png"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        remote_tmp = "/sdcard/phone_screenshot.png"

        await self._emit_status("executing", action="take_phone_screenshot")
        try:
            await self.run_adb(["shell", "screencap", "-p", remote_tmp])
            await self.run_adb(["pull", remote_tmp, str(dest)])
            # Clean remote temporary file
            with contextlib.suppress(Exception):
                await self.run_adb(["shell", "rm", "-f", remote_tmp], retry_on_disconnect=False)

            await self._emit_status("success", action="take_phone_screenshot", path=str(dest))
            return f"Phone screenshot saved to {dest}."
        except Exception as e:
            await self._emit_status("error", action="take_phone_screenshot", error=str(e))
            raise

    # -- Destructive Actions (Set B: Requires User Confirmation) -------------------------

    async def uninstall_phone_package(self, package: str) -> str:
        """Uninstall an app package from phone (requires confirmation)."""
        pkg = package.strip()
        if not _PKG_RE.fullmatch(pkg):
            raise AdbError(f"Invalid package name format: {pkg!r}")

        await self._emit_status("executing", action="uninstall_phone_package", package=pkg)
        try:
            await self.run_adb(["shell", "pm", "uninstall", pkg])
            await self._emit_status("success", action="uninstall_phone_package", package=pkg)
            return f"Uninstalled {pkg} from phone."
        except Exception as e:
            await self._emit_status("error", action="uninstall_phone_package", error=str(e))
            raise

    async def reboot_phone(self) -> str:
        """Reboot phone (requires confirmation)."""
        await self._emit_status("executing", action="reboot_phone")
        try:
            await self.run_adb(["reboot"], retry_on_disconnect=False)
            await self._emit_status("success", action="reboot_phone")
            return "Phone reboot initiated."
        except Exception as e:
            await self._emit_status("error", action="reboot_phone", error=str(e))
            raise

    async def run_phone_shell(self, command: str) -> str:
        """Execute arbitrary shell command on phone (requires confirmation)."""
        cmd = command.strip()
        if not cmd:
            raise AdbError("Shell command cannot be empty.")

        await self._emit_status("executing", action="run_phone_shell")
        try:
            res = await self.run_adb(["shell", cmd])
            await self._emit_status("success", action="run_phone_shell")
            out = res.stdout or res.stderr or "Command executed successfully."
            return out
        except Exception as e:
            await self._emit_status("error", action="run_phone_shell", error=str(e))
            raise
