"""Offline I2C decoding of digital exports, independent of Logic 2."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from saleae_tools.binexport import DigitalChunk, DigitalExport


@dataclass
class Transaction:
    start_s: float  # the START (or repeated START) condition
    end_s: float  # the STOP (or the repeated START that ended it)
    address: int  # 7-bit
    read: bool  # the R/W bit; True = read
    address_ack: bool
    data: list[int] = field(default_factory=list)
    acks: list[bool] = field(default_factory=list)  # one per byte in data
    repeated_start: bool = False  # this transaction opened on a repeated START


@dataclass
class Decoded:
    transactions: list[Transaction] = field(default_factory=list)
    truncated: int = 0  # transactions cut by a chunk end; never decoded


def _level(chunk: DigitalChunk, t: float) -> int:
    return chunk.initial_state ^ (bisect_right(chunk.transitions, t) & 1)


def _bits_value(bits: list[int], width: int) -> int:
    return sum(b << (width - 1 - k) for k, b in enumerate(bits))


def decode(scl: DigitalExport, sda: DigitalExport) -> Decoded:
    """Decode I2C transactions from one SCL and one SDA export. Chunks are
    paired by index; each pair is decoded alone over its shared span.

    SDA is sampled on every rising SCL edge and the sample is committed when
    that clock period completes (the falling edge); if the next event is a
    START or STOP instead, the sample belonged to that condition and is
    discarded. A byte group is nine clocks: address groups are 7 address bits
    (MSB first), the R/W bit and the acknowledge, data groups eight bits and
    the acknowledge; SDA low is ACK.

    A transaction still open at the end of the shared span, or whose final
    byte group is incomplete there, is counted in ``truncated`` instead of
    being decoded from missing data. Conforming I2C only changes SDA while
    SCL is low; captures that put an SCL and an SDA edge on the same
    timestamp are malformed, and the SDA edge is processed first.
    """
    if len({len(e.chunks) for e in (scl, sda)}) != 1:
        raise ValueError("channel exports have different chunk counts")
    out = Decoded()
    for i, ck in enumerate(scl.chunks):
        cd = sda.chunks[i]
        begin = max(ck.begin_time, cd.begin_time)
        end = min(ck.end_time, cd.end_time)
        _decode_span(ck, cd, begin, end, out)
    return out


def _decode_span(
    ck: DigitalChunk,
    cd: DigitalChunk,
    begin: float,
    end: float,
    out: Decoded,
) -> None:
    clock_edges = [t for t in ck.transitions if begin < t <= end]
    data_edges = [t for t in cd.transitions if begin < t <= end]
    # SDA edges sort before SCL edges at identical timestamps: conforming
    # captures never share one, so this only fixes malformed ones.
    events = [(t, False) for t in data_edges] + [(t, True) for t in clock_edges]
    events.sort()

    start: float | None = None
    repeated = False  # whether the open transaction opened on a repeated START
    bits: list[int] = []
    txn: Transaction | None = None
    pending: int | None = None  # SDA sampled at a rising edge, not yet committed

    def reset() -> None:
        nonlocal start, repeated, bits, txn, pending
        start, repeated, bits, txn, pending = None, False, [], None, None

    def close(t_end: float) -> None:
        nonlocal txn
        if txn is not None and len(bits) % 9 == 0:
            txn.end_s = t_end
            out.transactions.append(txn)
        else:
            out.truncated += 1
        reset()

    for t, is_clock in events:
        if is_clock:
            if start is None:
                continue
            if _level(ck, t) == 1:  # rising edge: sample SDA
                pending = _level(cd, t)
            elif pending is not None:  # falling edge: commit the sample
                bits.append(pending)
                pending = None
                if len(bits) % 9 == 0:
                    if txn is None:
                        txn = Transaction(
                            start,
                            start,
                            _bits_value(bits[:7], 7),
                            bool(bits[7]),
                            bits[8] == 0,
                            repeated_start=repeated,
                        )
                    else:
                        txn.data.append(_bits_value(bits[-9:-1], 8))
                        txn.acks.append(bits[-1] == 0)
        else:
            if _level(ck, t) != 1:
                continue  # SDA change while SCL low: ordinary data
            # SDA moved while SCL high: START (fell) or STOP (rose).
            pending = None  # the sample taken by the pre-condition clock
            if _level(cd, t) == 0:
                was_repeated = start is not None
                if was_repeated:
                    close(t)  # the open transaction ends at this repeated START
                start, bits, txn, repeated = t, [], None, was_repeated
            elif start is not None:
                close(t)
    if start is not None:  # still open at the end of the shared span
        out.truncated += 1


def parse_expect(text: str) -> tuple[int, bool, list[int] | None]:
    """Parse ``ADDR:w|r:BYTES`` into ``(address, read, data)``.

    ``ADDR`` is hex within the 7-bit range; ``BYTES`` is a comma-separated
    hex byte list, or empty to assert the address and direction only (a
    ``None`` payload). Raises ``ValueError`` on any malformed field.
    """
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError("--expect must be ADDR:w|r:BYTES, e.g. 3c:w:01,02,03")
    address_s, dir_s, data_s = parts
    try:
        address = int(address_s, 16)
    except ValueError:
        raise ValueError(f"--expect address {address_s!r} must be hex") from None
    if not 0 <= address <= 0x7F:
        raise ValueError(f"--expect address {address_s} is outside the 7-bit range")
    if dir_s not in ("w", "r"):
        raise ValueError(f"--expect direction must be w or r, not {dir_s!r}")
    if data_s == "":
        return address, dir_s == "r", None
    try:
        data = [int(b, 16) for b in data_s.split(",")]
    except ValueError:
        raise ValueError(f"--expect bytes {data_s!r} must be hex") from None
    if not data or not all(0 <= b <= 0xFF for b in data):
        raise ValueError("--expect bytes must be 00..ff")
    return address, dir_s == "r", data


def match_transaction(
    transactions: list[Transaction],
    address: int,
    read: bool,
    data: list[int] | None,
) -> dict[str, object]:
    """Find the first transaction matching all asserted fields.

    A ``None`` payload leaves the bytes unasserted. On failure the result
    names the field that differed on the closest candidate: the first
    transaction that got furthest through the address, direction and data
    checks.
    """
    best: tuple[int, int, str] | None = None  # (score, index, differing field)
    for i, t in enumerate(transactions):
        if t.address == address and t.read == read and (data is None or t.data == data):
            return {
                "matched": True,
                "index": i,
                "mismatch_field": None,
                "candidate_index": i,
            }
        score, mismatch_field = 0, "address"
        if t.address == address:
            score, mismatch_field = 1, "direction"
            if t.read == read:
                score, mismatch_field = 2, "data"
        if best is None or score > best[0]:
            best = (score, i, mismatch_field)
    score, index, mismatch_field = best if best is not None else (0, None, "address")
    return {
        "matched": False,
        "index": None,
        "mismatch_field": mismatch_field,
        "candidate_index": index,
    }
