import array

import pytest

from saleae_tools.binexport import DigitalChunk, DigitalExport, read_digital, write_digital
from saleae_tools.uart import decode


def _bits(byte, *, parity="none", stop_bits=1, bits=8, bad_stop=False, flip_parity=False):
    out = [0] + [(byte >> k) & 1 for k in range(bits)]
    ones = bin(byte).count("1")
    if parity != "none":
        p = ones % 2 if parity == "even" else 1 - ones % 2
        out.append(p ^ flip_parity)
    out += [1] * stop_bits
    if bad_stop:
        out[-1] = 0
    return out


def _chunk(data, baud, *, scale=1.0, start=0.0, tail=None, **kw):
    """Idle-high chunk carrying ``data`` bytes back to back."""
    bit = scale / baud
    levels = []
    for b in data:
        levels += _bits(b, **kw)
    t = start + 2 * bit
    trans = array.array("d")
    state = 1
    for lv in levels:
        if lv != state:
            trans.append(t)
            state = lv
        t += bit
    end = t + 2 * bit if tail is None else tail
    return DigitalChunk(1, start, end, trans, 1e9)


def _roundtrip(tmp_path, chunks):
    path = tmp_path / "digital_6.bin"
    write_digital(path, DigitalExport(1, chunks))
    return read_digital(path)


@pytest.mark.parametrize("baud", [115200, 1_000_000])
def test_pattern(tmp_path, baud):
    export = _roundtrip(tmp_path, [_chunk([0x55, 0x00, 0xFF] * 4, baud)])
    result = decode(export, baud)
    assert [f.byte for f in result.frames] == [0x55, 0x00, 0xFF] * 4
    assert not any(f.framing_error or f.parity_error for f in result.frames)
    assert result.truncated == 0


@pytest.mark.parametrize("scale", [0.98, 1.02])
def test_baud_error(tmp_path, scale):
    export = _roundtrip(tmp_path, [_chunk([0x55, 0x00, 0xFF] * 3, 115200, scale=scale)])
    r = decode(export, 115200)
    assert [f.byte for f in r.frames] == [0x55, 0x00, 0xFF] * 3
    assert r.bit_time_s == pytest.approx(scale / 115200)
    for f in r.frames:
        if f.byte == 0xFF:
            assert f.bit_time_s is None  # k_max = 1, too short a baseline
        else:
            assert f.bit_time_s == pytest.approx(scale / 115200)
    assert r.frames_excluded == 3


def test_measure_on_rate(tmp_path):
    export = _roundtrip(tmp_path, [_chunk([0x55, 0x00, 0xFF] * 4, 115200)])
    r = decode(export, 115200)
    assert r.bit_time_s == pytest.approx(1 / 115200)
    assert r.frames_excluded == 4  # the four 0xFF frames (k_max = 1)
    assert [f.bit_time_s is None for f in r.frames] == [False, False, True] * 4


def test_measure_three_percent_fast(tmp_path):
    # scale multiplies bit time, so scale = 1/1.03 is a transmitter 3% fast.
    export = _roundtrip(tmp_path, [_chunk([0x55, 0x00, 0xFF] * 4, 115200, scale=1 / 1.03)])
    r = decode(export, 115200)
    assert [f.byte for f in r.frames] == [0x55, 0x00, 0xFF] * 4
    assert not any(f.framing_error or f.parity_error for f in r.frames)
    error = (1 / r.bit_time_s) / 115200 - 1
    assert error == pytest.approx(0.03, abs=1e-9)
    # 8N1 mis-decodes at |error| where (10 - 0.5) * |error| reaches 0.5 bits.
    assert (10 - 0.5) * abs(error) == pytest.approx(0.285, abs=1e-9)
    assert r.frames_excluded == 4


def test_measure_unmeasurable_ff_only():
    r = decode(DigitalExport(1, [_chunk([0xFF] * 6, 115200)]), 115200)
    assert [f.byte for f in r.frames] == [0xFF] * 6
    assert all(f.bit_time_s is None for f in r.frames)
    assert r.frames_excluded == 6 and r.bit_time_s is None


def test_measure_zero_frames_are_kept():
    # 0x00 offers the longest baseline (one edge at boundary 9): it must measure.
    r = decode(DigitalExport(1, [_chunk([0x00] * 3, 115200)]), 115200)
    assert [f.byte for f in r.frames] == [0x00] * 3
    assert r.bit_time_s == pytest.approx(1 / 115200)
    assert r.frames_excluded == 0


def test_measure_wide_frame():
    # n_bits = 1 + 8 + 1 + 2 = 12; the edge window must follow parity/stop bits.
    r = decode(
        DigitalExport(1, [_chunk([0x55] * 2, 115200, parity="even", stop_bits=2)]),
        115200,
        parity="even",
        stop_bits=2,
    )
    assert r.bit_time_s == pytest.approx(1 / 115200)
    assert r.frames_excluded == 0


def test_framing_error():
    c = DigitalChunk(1, 0, 1e-3, array.array("d"))
    bit = 1 / 115200
    levels = _bits(0xA5, bad_stop=True) + [1, 1]
    t, state = 1e-5, 1
    for lv in levels:
        if lv != state:
            c.transitions.append(t)
            state = lv
        t += bit
    frames = decode(DigitalExport(1, [c]), 115200).frames
    assert len(frames) == 1 and frames[0].byte == 0xA5 and frames[0].framing_error


@pytest.mark.parametrize("parity", ["even", "odd"])
def test_parity(parity):
    good = _chunk([0x55, 0x07], 9600, parity=parity)
    f = decode(DigitalExport(1, [good]), 9600, parity=parity).frames
    assert [x.byte for x in f] == [0x55, 0x07] and not any(x.parity_error for x in f)
    other = "odd" if parity == "even" else "even"
    f = decode(DigitalExport(1, [good]), 9600, parity=other).frames
    assert all(x.parity_error for x in f)


def test_two_stop_bits():
    c = _chunk([0x3C, 0x3C], 9600, stop_bits=2)
    assert [f.byte for f in decode(DigitalExport(1, [c]), 9600, stop_bits=2).frames] == [0x3C] * 2


def test_gap_mid_frame_is_truncated_not_bad_byte(tmp_path):
    baud = 115200
    full = _chunk([0x55, 0x00], baud)
    # Cut the chunk in the middle of the second frame (0x00: start bit then all low).
    bit = 1 / baud
    second_start = 2 * bit + 10 * bit
    first = DigitalChunk(
        1,
        0,
        second_start + 4.2 * bit,
        array.array("d", [t for t in full.transitions if t < second_start + 4.2 * bit]),
        1e9,
    )
    second = _chunk([0xFF], baud, start=1.0)
    export = _roundtrip(tmp_path, [first, second])
    result = decode(export, baud)
    assert [f.byte for f in result.frames] == [0x55, 0xFF]
    assert result.truncated == 1
    # The chunk-cut 0x00 is truncated, never decoded, so it feeds no measurement;
    # the surviving 0x55 measures, the 0xFF is excluded.
    assert result.frames_excluded == 1
    assert result.bit_time_s == pytest.approx(1 / baud)


def test_empty_and_constant():
    for export in (
        DigitalExport(1),
        DigitalExport(1, [DigitalChunk(1, 0, 1, array.array("d"))]),
        DigitalExport(1, [DigitalChunk(0, 0, 1, array.array("d"))]),
    ):
        r = decode(export, 115200)
        assert r.frames == [] and r.truncated == 0


def test_bad_args():
    with pytest.raises(ValueError):
        decode(DigitalExport(1), 0)
    with pytest.raises(ValueError):
        decode(DigitalExport(1), 9600, parity="mark")
