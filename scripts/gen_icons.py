"""Generate minimal placeholder PNG icons for the PWA (no external deps)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web" / "icons"


def _png(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Produce a solid-colour PNG. Minimal, valid, no dependencies."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b""
    for _ in range(height):
        raw += b"\x00" + bytes([r, g, b]) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    WEB.mkdir(parents=True, exist_ok=True)
    # Dark accent blue (#58a6ff) placeholder icons.
    for size in (192, 512):
        (WEB / f"icon-{size}.png").write_bytes(_png(size, size, 0x58, 0xA6, 0xFF))
        print(f"wrote icon-{size}.png ({size}x{size})")


if __name__ == "__main__":
    main()
