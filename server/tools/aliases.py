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

