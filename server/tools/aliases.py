"""Alias maps: spoken names → binaries, window classes and URLs."""

from __future__ import annotations

APP_ALIASES: dict[str, str] = {
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "code": "code",
    "firefox": "firefox",
    "browser": "firefox",
    "web browser": "firefox",
    "chrome": "google-chrome-stable",
    "google chrome": "google-chrome-stable",
    "chromium": "chromium",
    "obsidian": "obsidian",
    "notes": "obsidian",
    "terminal": "kitty",
    "kitty": "kitty",
    "files": "thunar",
    "file manager": "thunar",
    "spotify": "spotify",
    "discord": "discord",
    "telegram": "telegram-desktop",
}

# Hyprland window classes (focuswindow class:<x>).
WINDOW_CLASS_ALIASES: dict[str, str] = {
    "vs code": "code",
    "vscode": "code",
    "visual studio code": "code",
    "code": "code",
    "firefox": "firefox",
    "browser": "firefox",
    "chrome": "google-chrome",
    "obsidian": "obsidian",
    "notes": "obsidian",
    "terminal": "kitty",
    "kitty": "kitty",
    "spotify": "spotify",
    "discord": "discord",
    "telegram": "org.telegram.desktop",
}

URL_ALIASES: dict[str, str] = {
    "github": "https://github.com",
    "gmail": "https://mail.google.com",
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "tailscale": "https://login.tailscale.com/admin/machines",
    "groq": "https://console.groq.com",
    "cloudflare": "https://dash.cloudflare.com",
}


def resolve_app(name: str) -> str:
    key = " ".join(name.lower().split())
    return APP_ALIASES.get(key, key)


def resolve_window_class(name: str) -> str:
    key = " ".join(name.lower().split())
    return WINDOW_CLASS_ALIASES.get(key, key)
