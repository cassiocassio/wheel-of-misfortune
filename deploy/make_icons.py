#!/usr/bin/env python3
"""Generate the PWA icons — a fairground ferris wheel, gold on the app's night
sky, with cabins in the five chore-kind hues.

Pure stdlib (zlib + struct): the project ships no binary art and pulls no image
library, so the icons are *drawn* here and the PNGs are committed. Re-run after
tweaking the design:  python deploy/make_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

WEB_ICONS = Path(__file__).resolve().parent.parent / "web" / "icons"

BG = (0x17, 0x10, 0x22)        # --bg  night sky
GOLD = (0xE8, 0xB8, 0x4B)      # --gold rim, spokes, hub
INK = (0xF4, 0xEC, 0xFF)       # --ink highlights
KIND_HUES = [                  # cabins cycle the five kinds (SPEC §5)
    (0xE0, 0xA1, 0x00),  # surfaces
    (0x1F, 0x8A, 0x8A),  # floors
    (0x3D, 0x6F, 0xD6),  # laundry
    (0xC2, 0x41, 0x0C),  # waste
    (0xA0, 0x1A, 0x58),  # wet
]
SS = 4                         # supersample factor → cheap anti-aliasing


def _png(path: Path, w: int, h: int, rgb: bytearray) -> None:
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    stride = w * 3
    for y in range(h):
        raw.append(0)  # filter: none
        raw += rgb[y * stride:(y + 1) * stride]
    body = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))  # 8-bit RGB
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(body)


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _sample(nx: float, ny: float):
    """Colour at normalised coords in [-1, 1] (centre = 0). Full-bleed bg so the
    icon also works as a maskable: the OS may round/crop the corners freely."""
    r = math.hypot(nx, ny)
    ang = math.atan2(ny, nx)

    rim_r, rim_t = 0.82, 0.055
    hub_r = 0.13
    spoke_t = 0.018
    cabin_r = 0.085
    n = len(KIND_HUES) + 3  # 8 cabins / spokes

    # cabins on the rim (drawn on top)
    for i in range(n):
        a = (2 * math.pi * i) / n - math.pi / 2
        cx, cy = rim_r * math.cos(a), rim_r * math.sin(a)
        if math.hypot(nx - cx, ny - cy) <= cabin_r:
            return KIND_HUES[i % len(KIND_HUES)]

    # hub
    if r <= hub_r:
        return GOLD
    if r <= hub_r + 0.02:
        return _mix(GOLD, BG, (r - hub_r) / 0.02)

    # rim ring
    if abs(r - rim_r) <= rim_t:
        return GOLD

    # spokes (inside the rim only)
    if r < rim_r:
        nearest = round((ang + math.pi / 2) / (2 * math.pi) * n)
        sa = (2 * math.pi * nearest) / n - math.pi / 2
        if abs(r * math.sin(ang - sa)) <= spoke_t:
            return GOLD

    # faint vignette so the disc reads against the sky
    if r < rim_r:
        return _mix(BG, INK, 0.04 * (1 - r / rim_r))
    return BG


def render(size: int) -> bytearray:
    hi = size * SS
    half = hi / 2
    # supersampled RGB, then box-downscale SSxSS → smooth edges
    big = [None] * (hi * hi)
    for y in range(hi):
        ny = (y + 0.5 - half) / half
        for x in range(hi):
            nx = (x + 0.5 - half) / half
            big[y * hi + x] = _sample(nx, ny)

    out = bytearray(size * size * 3)
    inv = 1.0 / (SS * SS)
    for y in range(size):
        for x in range(size):
            r = g = b = 0
            for dy in range(SS):
                row = (y * SS + dy) * hi + x * SS
                for dx in range(SS):
                    px = big[row + dx]
                    r += px[0]
                    g += px[1]
                    b += px[2]
            o = (y * size + x) * 3
            out[o] = round(r * inv)
            out[o + 1] = round(g * inv)
            out[o + 2] = round(b * inv)
    return out


def main() -> None:
    WEB_ICONS.mkdir(parents=True, exist_ok=True)
    for size in (180, 192, 512):
        _png(WEB_ICONS / f"icon-{size}.png", size, size, render(size))
        print(f"wrote web/icons/icon-{size}.png")


if __name__ == "__main__":
    main()
