"""App normalization and project auto-detection heuristics for telemetry."""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME_MAP: dict[str, str] = {
    "code": "Code",
    "code-oss": "Code",
    "code - oss": "Code",
    "vscodium": "Code",
    "visual-studio-code": "Code",
    "kitty": "Kitty",
    "kitty-terminal": "Kitty",
    "firefox": "Firefox",
    "firefox-developer-edition": "Firefox",
    "firefox-esr": "Firefox",
    "org.mozilla.firefox": "Firefox",
    "zen": "Firefox",
    "zen-browser": "Firefox",
    "zen-beta": "Firefox",
    "app.zen_browser.zen": "Firefox",
    "obsidian": "Obsidian",
    "antigravity": "Antigravity",
    "google-chrome": "Chrome",
    "chromium": "Chromium",
    "alacritty": "Alacritty",
    "wezterm": "WezTerm",
    "foot": "Foot",
    "slack": "Slack",
    "discord": "Discord",
    "spotify": "Spotify",
    "thunderbird": "Thunderbird",
    "neovim": "Neovim",
    "nvim": "Neovim",
    "android-studio": "Android Studio",
    "studio": "Android Studio",
}

# Known projects cache
_PROJECT_CACHE: list[str] | None = None


def normalize_app_name(raw_class: str | None) -> str:
    """Map raw Hyprland window class to canonical app name."""
    if not raw_class:
        return "Unknown"
    cleaned = raw_class.strip().lower()
    if cleaned in APP_NAME_MAP:
        return APP_NAME_MAP[cleaned]
    # Check prefixes or substrings
    for key, mapped in APP_NAME_MAP.items():
        if key in cleaned:
            return mapped
    # Return formatted class
    return raw_class.strip().title() or "Unknown"


def get_known_projects(projects_dir: Path | None = None) -> list[str]:
    """Discover directory names in the user's projects root."""
    global _PROJECT_CACHE
    p_dir = projects_dir or Path("/home/volchino/Allprojects")
    if not p_dir.is_dir():
        return _PROJECT_CACHE or []
    try:
        found = [d.name for d in p_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        _PROJECT_CACHE = sorted(found, key=lambda s: len(s), reverse=True)
        return _PROJECT_CACHE
    except OSError:
        return _PROJECT_CACHE or []


def detect_project(
    window_title: str | None,
    app_name: str = "",
    projects_dir: Path | None = None,
    known_projects: list[str] | None = None,
) -> str | None:
    """Extract project name from window title or path."""
    if not window_title:
        return None

    title = window_title.strip()
    projects = known_projects if known_projects is not None else get_known_projects(projects_dir)

    # 1. Check for explicit path mentions: /Allprojects/<project>/ or ~/Allprojects/<project>
    path_match = re.search(r"Allprojects/([^/\s,;:\'\"\(\)]+)", title)
    if path_match:
        matched_candidate = path_match.group(1).strip()
        for proj in projects:
            if matched_candidate.lower() == proj.lower():
                return proj
        return matched_candidate

    # 2. Check VS Code format: <file> - <project> - Visual Studio Code
    if "Visual Studio Code" in title or "Code - OSS" in title or "VSCodium" in title:
        parts = [p.strip() for p in title.split(" - ")]
        if len(parts) >= 2:
            # The second or second-to-last part is often the workspace/project name
            for candidate in parts:
                for proj in projects:
                    if candidate.lower() == proj.lower():
                        return proj

    # 3. Check exact word or substring match against known projects (longest first)
    lower_title = title.lower()
    for proj in projects:
        # Check boundary or word match
        pattern = rf"(?:\b|_){re.escape(proj.lower())}(?:\b|_)"
        if re.search(pattern, lower_title):
            return proj

    return None
