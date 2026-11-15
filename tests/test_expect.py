"""Tests for the protocol-neutral --expect matcher."""

from dataclasses import asdict

from saleae_tools.expect import Match, match_expected


def test_match_expected():
    p = [0x55, 0x00, 0xFF]
    ok = match_expected([0x00, 0x55, 0x00, 0xFF, 0x55, 0x00], p)
    assert ok.matched and ok.start_offset == 1
    bad = match_expected([0x55, 0x00, 0xFF, 0x55, 0x01, 0xFF], p)
    assert not bad.matched and bad.mismatch_offset == 4
    assert match_expected([0x55, 0x00], p) == Match(
        matched=False, start_offset=None, mismatch_offset=None, repeats=0
    )


def test_asdict_emits_documented_keys_in_order():
    """The --json contract: these four keys, in this order, byte-identical."""
    assert list(asdict(Match(True, 0, None, 2))) == [
        "matched",
        "start_offset",
        "mismatch_offset",
        "repeats",
    ]
