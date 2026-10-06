# Deployment & Setup Guide

## Prerequisites

- Arch Linux laptop with Hyprland
- `uv` (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Tools: `grim`, `wpctl` (wireplumber), `hyprctl`, `xdg-open`, `git`
- Tailscale installed and joined to a tailnet

## 1. Install & first run

```bash
cd ~/Allprojects/VoLchiNo
cp .env.example .env
# Edit .env: set AUTH_TOKEN to a strong random string.
make install
make dev   # dry-run mode (tools log argv instead of executing)
# or
make run   # real mode
```

## 2. systemd --user service

```bash
chmod +x deploy/start.sh

# Link the unit file
mkdir -p ~/.config/systemd/user
ln -sf ~/Allprojects/VoLchiNo/deploy/volchino-agent.service \
       ~/.config/systemd/user/volchino-agent.service

systemctl --user daemon-reload
systemctl --user enable --now volchino-agent.service
journalctl --user -u volchino-agent -f
```

## 3. Tailscale HTTPS (exposes the server to your phone)

```bash
# Get a Tailscale cert for your machine
tailscale cert <hostname>.<tailnet>.ts.net

# Proxy HTTP -> HTTPS so the phone can reach the server over the tailnet
tailscale serve --bg https / http://127.0.0.1:8765

# Verify
curl https://<hostname>.<tailnet>.ts.net/health
```

## 4. Disable sleep on AC (so the server stays reachable)

Edit `/etc/systemd/logind.conf`:

```ini
HandleLidSwitchExternalPower=ignore
HandleLidSwitch=ignore
IdleAction=ignore
```

Then `sudo systemctl restart systemd-logind`.

## 5. Install the PWA on Android (Redmi Note 8 Pro)

1. Open Chrome on the phone.
2. Go to `https://<hostname>.<tailnet>.ts.net/?token=<YOUR_AUTH_TOKEN>`.
3. Tap the three-dot menu -> **Install app** (or "Add to Home screen").
4. The app opens in standalone mode with the CyberCat avatar.

The `?token=` parameter is stored in the PWA start URL. Generate a long random token.

## 6. Run tests

```bash
make test   # pytest
make lint   # ruff check + format check
```

## 7. Live verification

```bash
# With the server running:
AUTH_TOKEN=<your-token> make verify
```
