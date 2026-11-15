"""Protocol-neutral expected-pattern matching shared by the decoder verbs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Match:
    matched: bool
    start_offset: int | None
    mismatch_offset: int | None
    repeats: int


def match_expected(stream: list[int], pattern: list[int]) -> Match:
    """Check that ``pattern`` repeats through ``stream`` from its first occurrence.

    A trailing partial repetition is accepted. ``mismatch_offset`` is an index
    into ``stream``; it is None when the pattern matched or was never found.
    """
    n = len(pattern)
    if n == 0:
        raise ValueError("expected pattern is empty")
    start = next((i for i in range(len(stream) - n + 1) if stream[i : i + n] == pattern), None)
    if start is None:
        return Match(False, None, None, 0)
    for i in range(start, len(stream)):
        if stream[i] != pattern[(i - start) % n]:
            return Match(False, start, i, (i - start) // n)
    return Match(True, start, None, (len(stream) - start) // n)
