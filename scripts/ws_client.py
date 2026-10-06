"""WS client script for live verification against a running server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import websockets


async def run(url: str, messages: list[str], *, expect_zero_tokens: bool) -> bool:
    ok = True
    async with websockets.connect(url) as ws:
        # First message should be pet snapshot.
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        pet = json.loads(raw)
        assert pet["type"] == "pet", f"expected pet snapshot, got {pet['type']}"
        print(f"[OK] Connected. Pet: {pet.get('pet_name')} ({pet.get('state')})")

        for text in messages:
            await ws.send(json.dumps({"type": "text", "text": text}))
            print(f"\n→ {text}")

            # Collect messages until we get a result.
            result = None
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                print(f"  ← {msg['type']}: {json.dumps(msg, indent=None)[:200]}")
                if msg["type"] == "result":
                    result = msg
                    break
                if msg["type"] == "confirm":
                    # Auto-approve for verification.
                    await ws.send(
                        json.dumps({"type": "confirm", "id": msg["id"], "approved": True})
                    )

            status = result.get("status", "?")
            tokens = result.get("tokens_used", -1)
            if status != "success":
                print(f"  [FAIL] status={status}")
                ok = False
            elif expect_zero_tokens and tokens != 0:
                print(f"  [FAIL] tokens_used={tokens} (expected 0)")
                ok = False
            else:
                print(f"  [OK] {status}, tokens={tokens}")

            # Drain pet update(s).
            try:
                while True:
                    extra = await asyncio.wait_for(ws.recv(), timeout=1)
                    em = json.loads(extra)
                    if em["type"] == "pet":
                        pass
                    else:
                        print(f"  ← extra: {em['type']}")
            except TimeoutError:
                pass

    return ok


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=None, help="ws:// URL (default uses AUTH_TOKEN env)")
    p.add_argument("--expect-zero-tokens", action="store_true")
    p.add_argument("messages", nargs="*")
    args = p.parse_args()

    url = args.url
    if not url:
        token = os.environ.get("AUTH_TOKEN", "")
        url = f"ws://127.0.0.1:8765/ws?token={token}"

    if not args.messages:
        args.messages = ["volume 30%", "open firefox", "what's my work time today"]

    ok = asyncio.run(run(url, args.messages, expect_zero_tokens=args.expect_zero_tokens))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
