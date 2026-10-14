#!/usr/bin/env python3
"""Write the PWA icons (stdlib only).

Three bars, a forecast distribution, on near-black with an orange centre bar.

    python3 app/web/scripts/make_icons.py
"""

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "public" / "icons"
BG = (15, 17, 21)
LIGHT = (243, 243, 240)
CREAM = (255, 122, 26)


def in_round_rect(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def render(size: int, margin: float, round_corners: bool) -> bytes:
    u = size / 64.0
    bars = [(14, 36, 9, 14, LIGHT), (27.5, 20, 9, 30, CREAM), (41, 29, 9, 21, LIGHT)]
    # glyph scaled into the safe area (maskable icons keep ~80% inside)
    k = 1 - margin
    off = (1 - k) / 2 * 64
    rows = bytearray()
    for py in range(size):
        rows.append(0)
        for px in range(size):
            x, y = px / u, py / u
            inside = (not round_corners) or in_round_rect(x, y, 0, 0, 64, 64, 14)
            if not inside:
                rows += bytes((0, 0, 0, 0))
                continue
            col = BG
            gx, gy = (x - off) / k, (y - off) / k
            for bx, by, bw, bh, c in bars:
                if in_round_rect(gx, gy, bx, by, bx + bw, by + bh, 2):
                    col = c
            rows += bytes((*col, 255))
    raw = bytes(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon-192.png").write_bytes(render(192, 0.0, True))
    (OUT / "icon-512.png").write_bytes(render(512, 0.0, True))
    (OUT / "icon-maskable-512.png").write_bytes(render(512, 0.2, False))


if __name__ == "__main__":
    main()
