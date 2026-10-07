"""Permission sets (spec §10).

Policy: a permission tag runs without confirmation **only** if it is in Set A. Everything
else — Set B and any unknown tag (e.g. ``system:open_url``, which the spec does not list) —
requires an explicit user confirmation over the WebSocket.
"""

from __future__ import annotations

from enum import StrEnum

SAFE_WITHOUT_CONFIRMATION: frozenset[str] = frozenset(
    {
        "hyprctl:open_app",
        "hyprctl:close_window",
        "hyprctl:switch_workspace",
        "hyprctl:focus_window",
        "system:set_volume",
        "system:take_screenshot",
        "system:get_battery",
        "system:get_work_time",
        "system:git_status",
        "obsidian:read_note",
        "obsidian:append_daily_log",
        "search:duckduckgo",
        "cache:read",
        # Phase 7 — ADB Safe Actions (Set A)
        "adb:open_app",
        "adb:media_control",
        "adb:set_volume",
        "adb:get_notifications",
        "adb:take_screenshot",
    }
)

REQUIRES_USER_CONFIRMATION: frozenset[str] = frozenset(
    {
        "social:publish_linkedin",
        "social:publish_x",
        "system:delete_file",
        "system:modify_config",
        "system:run_arbitrary_shell",
        "git:push",
        "adb:uninstall_package",
        "adb:reboot",
        "adb:run_shell",
        "financial:any_transaction",
    }
)


class Decision(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"


def check(permission: str) -> Decision:
    return Decision.ALLOW if permission in SAFE_WITHOUT_CONFIRMATION else Decision.CONFIRM


def check_all(permissions: list[str] | set[str] | tuple[str, ...]) -> Decision:
    if not permissions:
        return Decision.CONFIRM
    return (
        Decision.ALLOW if all(check(p) is Decision.ALLOW for p in permissions) else Decision.CONFIRM
    )
