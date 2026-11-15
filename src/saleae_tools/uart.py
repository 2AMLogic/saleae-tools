"""Offline UART decoding of digital exports, independent of Logic 2."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from saleae_tools.binexport import DigitalExport


@dataclass
class Frame:
    time_s: float  # falling edge of the start bit
    byte: int
    framing_error: bool
    parity_error: bool
    bit_time_s: float | None = None  # least-squares fit over this frame's own edges


@dataclass
class Decoded:
    frames: list[Frame] = field(default_factory=list)
    truncated: int = 0  # frames cut off by the end of a chunk; never decoded
    bit_time_s: float | None = None  # pooled over frames with a long-enough baseline
    frames_excluded: int = 0  # frames whose furthest intra-frame edge is k_max < 2


def decode(
    export: DigitalExport,
    baud: float,
    *,
    bits: int = 8,
    parity: str = "none",
    stop_bits: int = 1,
    idle_high: bool = True,
) -> Decoded:
    """Decode UART frames (LSB first). Each chunk is decoded independently.

    A frame whose last sampled bit lies past ``chunk.end_time`` is dropped and
    counted in ``truncated`` instead of being decoded from missing data.

    Each frame's own intra-frame edges (boundaries ``1 .. n_bits - 1`` within
    0.4 bit of the nominal grid) are least-squares-fit through the origin
    for a bit time, pooled across frames into ``bit_time_s``. Frames whose
    furthest edge sits at boundary ``k_max < 2`` — too short a baseline, e.g.
    ``0xff`` — are excluded and counted; sampling itself stays on the nominal
    grid, which is what makes the reported drift meaningful.
    """
    if baud <= 0:
        raise ValueError("baud must be positive")
    if parity not in ("none", "even", "odd"):
        raise ValueError("parity must be none, even or odd")
    if bits < 1 or stop_bits < 1:
        raise ValueError("bits and stop_bits must be >= 1")
    idle = 1 if idle_high else 0
    bit_s = 1.0 / baud
    n_parity = 0 if parity == "none" else 1
    n_bits = 1 + bits + n_parity + stop_bits
    out = Decoded()
    pool_kdt = 0.0
    pool_kk = 0.0
    for chunk in export.chunks:
        ts = chunk.transitions
        floor = float("-inf")  # ignore edges at or before the previous frame's last sample
        for i, t0 in enumerate(ts):
            # State after transition i; a start bit is an edge away from idle.
            if t0 <= floor or (chunk.initial_state ^ ((i + 1) & 1)) == idle:
                continue
            last_sample = t0 + (n_bits - 0.5) * bit_s
            if last_sample > chunk.end_time:
                out.truncated += 1
                break
            sample = [chunk.level_at(t0 + (k + 0.5) * bit_s) for k in range(n_bits)]
            data = sample[1 : 1 + bits]
            value = sum((b ^ (1 - idle)) << k for k, b in enumerate(data))
            parity_error = False
            if n_parity:
                ones = bin(value).count("1") + (sample[1 + bits] ^ (1 - idle))
                parity_error = (ones % 2 == 1) if parity == "even" else (ones % 2 == 0)
            framing_error = any(b != idle for b in sample[1 + bits + n_parity :])
            # Fit the bit time to this frame's own edges: k = round(dt / bit_s)
            # on the nominal grid, kept when 1 <= k <= n_bits - 1 and within
            # 0.4 bits of boundary k. The window stops at n_bits - 1 so an ff
            # frame never folds in the next frame's start bit.
            sum_kdt = 0.0
            sum_kk = 0.0
            k_max = 0
            for t in ts[i + 1 : bisect_left(ts, t0 + n_bits * bit_s)]:
                dt = t - t0
                k = round(dt / bit_s)
                if 1 <= k <= n_bits - 1 and abs(dt - k * bit_s) < 0.4 * bit_s:
                    sum_kdt += k * dt
                    sum_kk += k * k
                    k_max = k
            bit_time = sum_kdt / sum_kk if k_max >= 2 else None
            out.frames.append(Frame(t0, value, framing_error, parity_error, bit_time))
            if bit_time is None:
                out.frames_excluded += 1
            else:
                pool_kdt += sum_kdt
                pool_kk += sum_kk
            floor = last_sample
    out.bit_time_s = pool_kdt / pool_kk if pool_kk > 0 else None
    return out
