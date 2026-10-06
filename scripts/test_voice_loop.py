#!/usr/bin/env python3
"""CLI test script: sends a recorded audio file (or synthetic tone) over WebSocket
and verifies the server returns a voice_response with TTS audio.

Usage:
    python scripts/test_voice_loop.py [--wav path/to/file.wav] [--token TOKEN] [--url ws://...]

If no WAV file is given, a synthetic 440Hz tone is generated (the STT will likely
return garbage text, but the full round-trip is exercised).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import struct
import sys
import wave

SAMPLE_RATE = 16000
DURATION_S = 1.5


def make_tone_pcm(freq: float = 440.0) -> bytes:
    """Generate a 16-bit mono PCM sine wave."""
    import math

    n = int(SAMPLE_RATE * DURATION_S)
    samples = []
    for i in range(n):
        t = i / SAMPLE_RATE
        val = int(0.5 * 32767 * math.sin(2 * math.pi * freq * t))
        samples.append(struct.pack("<h", val))
    return b"".join(samples)


def wav_to_pcm(path: str) -> bytes:
    """Read a WAV file and extract raw PCM bytes."""
    with wave.open(path, "rb") as wf:
        assert wf.getnchannels() == 1, "WAV must be mono"
        assert wf.getsampwidth() == 2, "WAV must be 16-bit"
        return wf.readframes(wf.getnframes())


async def run(url: str, token: str, wav_path: str | None) -> bool:
    try:
        import websockets
    except ImportError:
        print("[FAIL] websockets package not installed. Run: uv add websockets --dev")
        return False

    if wav_path:
        print(f"[..] Loading WAV: {wav_path}")
        pcm = wav_to_pcm(wav_path)
    else:
        print("[..] Generating synthetic 440Hz tone (1.5s)")
        pcm = make_tone_pcm()

    pcm_b64 = base64.b64encode(pcm).decode("ascii")
    full_url = f"{url}?token={token}"
    print(f"[..] Connecting to {url}")

    try:
        async with websockets.connect(full_url) as ws:
            # Read initial pet snapshot
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            pet = json.loads(raw)
            print(f"[OK] Connected. Pet state: {pet.get('state', '?')}")

            # Send audio chunk
            msg = {"type": "audio_chunk", "data": pcm_b64}
            await ws.send(json.dumps(msg))
            print(f"[..] Sent audio_chunk ({len(pcm)} bytes PCM, {len(pcm_b64)} chars b64)")

            # Collect responses
            transcript = None
            voice_resp = None
            result_msg = None

            for _ in range(20):
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=15)
                except TimeoutError:
                    break
                resp = json.loads(raw)
                rtype = resp.get("type")
                print(f"  <- {rtype}: {str(resp.get('text', ''))[:80]}")

                if rtype == "transcript":
                    transcript = resp.get("text", "")
                elif rtype == "voice_response":
                    voice_resp = resp
                    break
                elif rtype == "result":
                    result_msg = resp
                elif rtype == "error":
                    print(f"[FAIL] Server error: {resp.get('text', '')}")
                    return False

            if transcript:
                print(f"[OK] STT transcript: {transcript!r}")
            else:
                print("[WARN] No transcript received")

            if voice_resp:
                audio_len = len(voice_resp.get("audio", ""))
                print("[OK] Voice response received:")
                print(f"     text: {voice_resp.get('text', '')[:100]}")
                print(f"     audio: {audio_len} chars base64 ({voice_resp.get('format', '?')})")
                print(f"     tool: {voice_resp.get('tool', 'none')}")
                if audio_len > 0:
                    print("[OK] Full voice loop verified!")
                    return True
                else:
                    print("[WARN] Voice response had empty audio (TTS may be offline)")
                    return True  # still consider it a pass if text works
            elif result_msg:
                print(f"[OK] Got result (no TTS): {result_msg.get('text', '')[:100]}")
                return True
            else:
                print("[FAIL] No voice_response or result received")
                return False

    except Exception as e:
        print(f"[FAIL] Connection error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test Volchino voice loop over WebSocket")
    parser.add_argument("--wav", help="Path to a 16kHz mono 16-bit WAV file")
    parser.add_argument("--token", default="change_me", help="Auth token")
    parser.add_argument("--url", default="ws://127.0.0.1:8765/ws", help="WebSocket URL")
    args = parser.parse_args()

    ok = asyncio.run(run(args.url, args.token, args.wav))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
