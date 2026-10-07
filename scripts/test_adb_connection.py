#!/usr/bin/env python3
"""CLI test script: verifies wireless ADB connectivity to the connected Android phone.

Tests wireless connection over Wi-Fi / Tailscale IP, queries device properties,
and optionally verifies safe actions (notifications, screenshot, media).

Usage:
    python scripts/test_adb_connection.py [--device IP] [--port PORT]
                                          [--screenshot] [--notifications] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from server.android.adb_client import AdbClient, AdbError  # noqa: E402
from server.config import Settings  # noqa: E402
from server.tools.base import CmdResult, DryRunRunner, SubprocessRunner  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="Test wireless ADB connection to Android phone.")
    parser.add_argument(
        "--device",
        help="Device IP or Tailscale address (defaults to ADB_DEVICE_ID in .env)",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="ADB wireless port (defaults to ADB_PORT in .env, 5555)",
    )
    parser.add_argument(
        "--screenshot",
        action="store_true",
        help="Test taking a screenshot and pulling to laptop",
    )
    parser.add_argument(
        "--notifications",
        action="store_true",
        help="Test reading phone notifications",
    )
    parser.add_argument(
        "--media",
        action="store_true",
        help="Test toggling media play/pause keyevent",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate ADB execution without physical device",
    )

    args = parser.parse_args()

    settings = Settings.from_env()

    # Override settings if flags provided
    device_id = args.device or settings.adb_device_id
    port = args.port or settings.adb_port or 5555

    print("=" * 60)
    print("  Volchino Personal AI Agent — Wireless ADB Bridge Test")
    print("=" * 60)
    print(f"Target Device: {device_id or '(none configured in .env)'}")
    print(f"Port:          {port}")
    print(f"Dry Run:       {args.dry_run}")
    print("-" * 60)

    if args.dry_run:
        runner = DryRunRunner()
        runner.installed = {"adb"}
        runner.result = CmdResult(
            0,
            f"List of devices attached\n{device_id or '100.1.2.3'}:{port}\tdevice\n",
            "",
        )
    else:
        adb_path = shutil.which("adb")
        if not adb_path:
            print("[!] ERROR: 'adb' binary not found on PATH.")
            print("    Please install android-tools: sudo pacman -S android-tools")
            return 1
        print(f"[✓] ADB Binary: {adb_path}")
        runner = SubprocessRunner()

    # Custom settings with updated target
    custom_settings = Settings(
        adb_device_id=device_id,
        adb_port=port,
        tool_timeout_s=10.0,
        screenshot_dir=settings.screenshot_dir,
    )

    client = AdbClient(settings=custom_settings, runner=runner)
    target = client.target_address

    print(f"[*] Testing connection to: {target or 'default connected device'}...")

    try:
        if target:
            connected = await client.connect()
            if connected:
                print(f"[✓] Successfully connected to {target}!")
            else:
                print(f"[!] 'adb connect {target}' did not report success.")
                print("[*] Checking if device is already listed in 'adb devices'...")

        is_conn = await client.is_connected()
        if is_conn:
            print("[✓] Device state: ONLINE (device attached)")
        else:
            print("[!] Device is NOT connected.")
            if not args.dry_run:
                print("\n--- Troubleshooting Tips for Redmi Note 8 Pro ---")
                print("1. Ensure Phone and Laptop are both on Tailscale or the same Wi-Fi.")
                print("2. On Phone: Settings -> Developer Options -> Enable 'Wireless Debugging'.")
                print("3. Check IP address and port displayed under 'Wireless Debugging'.")
                print(f"4. Add to your .env file: ADB_DEVICE_ID=<phone_ip> and ADB_PORT={port}")
                print("5. Run manually: adb connect <phone_ip>:<port>")
                print("--------------------------------------------------\n")
                return 2

        # Device info check
        if not args.dry_run:
            try:
                model_res = await client.run_adb(["shell", "getprop", "ro.product.model"])
                android_res = await client.run_adb(["shell", "getprop", "ro.build.version.release"])
                print(f"[✓] Phone Model: {model_res.stdout} (Android {android_res.stdout})")
            except Exception as e:
                print(f"[-] Could not read device model: {e}")

        # Optional checks
        if args.notifications:
            print("\n[*] Fetching phone notifications...")
            notifs = await client.read_phone_notifications()
            print(notifs)

        if args.media:
            print("\n[*] Testing media play/pause keyevent...")
            media_res = await client.media_play_pause()
            print(f"[✓] {media_res}")

        if args.screenshot:
            print("\n[*] Capturing test phone screenshot...")
            sc_res = await client.take_phone_screenshot()
            print(f"[✓] {sc_res}")

        print("\n[✓] All requested wireless ADB checks passed!")
        return 0

    except AdbError as e:
        print(f"[!] ADB Error: {e}")
        return 1
    except Exception as e:
        print(f"[!] Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
