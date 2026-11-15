import array
import json
from pathlib import Path

import pytest

from saleae_tools.binexport import DigitalChunk, DigitalExport, write_digital
from saleae_tools.cli import main
from saleae_tools.i2c import Transaction, decode, match_transaction, parse_expect

SLOT = 4.0  # time units per bit; SCL rises at +1 and falls at +3 inside a slot
# Channel numbers from examples/icepi-i2c.toml, in (scl, sda) order.
CHANNELS = (4, 5)


class _Bus:
    """SCL/SDA transition builder for a conforming I2C capture.

    SDA moves only while SCL is low; the one exception is a START or STOP,
    whose SDA edge lands while SCL is already high. No SCL edge ever shares
    a timestamp with an SDA edge, and STOP/repeated START are preceded by
    one extra rising SCL edge with the group already complete — the sample
    that edge takes is discarded when the condition follows.
    """

    def __init__(self, t0: float = 10.0):
        self.t = t0
        self.scl = 1  # idle high
        self.sda = 1
        self.scl_t: list[float] = []
        self.sda_t: list[float] = []

    def _set(self, line: str, lvl: int) -> None:
        cur = self.scl if line == "scl" else self.sda
        if cur == lvl:
            return
        if line == "scl":
            self.scl = lvl
            self.scl_t.append(self.t)
        else:
            self.sda = lvl
            self.sda_t.append(self.t)

    def start(self):
        # From idle (SCL high, SDA high): SDA falls while SCL high.
        self._set("sda", 0)
        self.t += 1.0
        self._set("scl", 0)  # ready the first bit slot
        self.t += 3.0

    def clock(self, bit: int):
        # One data-clock slot: SDA set while SCL low, sampled on the rise,
        # committed on the fall. Enters and leaves with SCL low.
        self._set("sda", bit)
        self.t += 1.0
        self._set("scl", 1)
        self.t += 2.0
        self._set("scl", 0)
        self.t += 1.0

    def byte(self, value: int, ack: bool):
        for k in range(7, -1, -1):
            self.clock((value >> k) & 1)
        self.clock(0 if ack else 1)

    def restart(self):
        # From a completed byte group (SCL low): release SDA high, raise the
        # clock, then drop SDA while SCL is high. Leaves SCL low.
        self._set("sda", 1)
        self.t += 1.0
        self._set("scl", 1)
        self.t += 1.0
        self._set("sda", 0)
        self.t += 1.0
        self._set("scl", 0)
        self.t += 1.0

    def stop(self):
        # From a completed byte group (SCL low): pull SDA low, raise the
        # clock, then release SDA while SCL is high. Leaves the bus idle.
        self._set("sda", 0)
        self.t += 1.0
        self._set("scl", 1)
        self.t += 1.0
        self._set("sda", 1)
        self.t += SLOT

    def chunk_pair(self, begin: float, end: float) -> tuple[DigitalChunk, DigitalChunk]:
        return (
            DigitalChunk(1, begin, end, array.array("d", self.scl_t), 1e6),
            DigitalChunk(1, begin, end, array.array("d", self.sda_t), 1e6),
        )

    def exports(self, version: int = 1) -> tuple[DigitalExport, DigitalExport]:
        scl, sda = self.chunk_pair(0.0, self.t)
        return DigitalExport(version, [scl]), DigitalExport(version, [sda])


def _write_txn(bus: _Bus, addr: int, data: list[int], *, ack: bool = True, addr_ack: bool = True):
    bus.start()
    bus.byte(addr << 1, addr_ack)
    for b in data:
        bus.byte(b, ack)
    bus.stop()


def _read_txn(bus: _Bus, addr: int, data: list[int], *, addr_ack: bool = True):
    bus.start()
    bus.byte((addr << 1) | 1, addr_ack)
    for i, b in enumerate(data):
        # The controller NACKs the final byte of a read to end the transfer.
        bus.byte(b, i < len(data) - 1)
    bus.stop()


def _write(tmp_path: Path, sig: tuple[DigitalExport, DigitalExport]) -> Path:
    for num, export in zip(CHANNELS, sig, strict=True):
        write_digital(tmp_path / f"digital_{num}.bin", export)
    return tmp_path


def test_write_transaction():
    bus = _Bus()
    _write_txn(bus, 0x3C, [0x01, 0x02, 0x03])
    r = decode(*bus.exports())
    assert r.truncated == 0
    [t] = r.transactions
    assert (t.address, t.read, t.address_ack) == (0x3C, False, True)
    assert t.data == [0x01, 0x02, 0x03] and t.acks == [True, True, True]
    assert not t.repeated_start


def test_read_transaction():
    bus = _Bus()
    _read_txn(bus, 0x3C, [0xA5, 0x5A])
    r = decode(*bus.exports())
    [t] = r.transactions
    assert (t.address, t.read, t.address_ack) == (0x3C, True, True)
    assert t.data == [0xA5, 0x5A]
    # The controller NACKs the final byte of a read to end the transfer.
    assert t.acks == [True, False]


def test_repeated_start_closes_the_open_transaction():
    bus = _Bus()
    bus.start()
    bus.byte(0x3C << 1, True)
    bus.byte(0x01, True)
    bus.restart()  # from the completed group, with no STOP in between
    bus.byte((0x3C << 1) | 1, True)
    bus.byte(0, False)  # read one byte, NACKed to end it
    bus.stop()
    r = decode(*bus.exports())
    assert r.truncated == 0 and len(r.transactions) == 2
    first, second = r.transactions
    assert not first.repeated_start and first.data == [0x01]
    assert second.repeated_start and second.read and second.data == [0]
    assert first.end_s == second.start_s


def test_nacked_address():
    bus = _Bus()
    bus.start()
    bus.byte(0x3C << 1, False)  # no device answers
    bus.stop()
    r = decode(*bus.exports())
    [t] = r.transactions
    assert t.address == 0x3C and not t.address_ack and t.data == []


def test_transaction_cut_by_chunk_end_is_truncated():
    bus = _Bus()
    bus.start()
    bus.byte(0x3C << 1, True)
    bus.byte(0x01, True)  # the capture ends before the STOP
    r = decode(*bus.exports())
    assert r.truncated == 1 and r.transactions == []


def test_incomplete_final_byte_group_is_truncated():
    bus = _Bus()
    bus.start()
    bus.byte(0x3C << 1, True)
    for k in range(7, -1, -1):  # eight data bits, but the ACK clock never comes
        bus.clock((0x55 >> k) & 1)
    r = decode(*bus.exports())
    assert r.truncated == 1 and r.transactions == []


def test_no_start_gives_no_transactions():
    bus = _Bus()
    r = decode(*bus.exports())
    assert r.transactions == [] and r.truncated == 0


def test_chunk_count_mismatch_raises():
    bus = _Bus()
    _write_txn(bus, 0x3C, [0x01])
    scl, sda = bus.exports()
    sda.chunks.append(sda.chunks[0])
    with pytest.raises(ValueError, match="different chunk counts"):
        decode(scl, sda)


def test_v0_single_chunk_and_multichunk_v1_decode(tmp_path):
    bus = _Bus()
    _write_txn(bus, 0x3C, [0x01])
    d = _write(tmp_path, (bus.exports(version=0)))
    assert main(["i2c", str(d), "--channels", "4,5", "--expect", "3c:w:01"]) == 0

    b1, b2 = _Bus(), _Bus(t0=500.0)
    _write_txn(b1, 0x10, [0x01])
    _read_txn(b2, 0x11, [0xFF])
    scl = DigitalExport(1, [*b1.chunk_pair(0.0, 400.0)[:1], *b2.chunk_pair(400.0, 800.0)[:1]])
    sda = DigitalExport(1, [b1.chunk_pair(0.0, 400.0)[1], b2.chunk_pair(400.0, 800.0)[1]])
    r = decode(scl, sda)
    assert [(t.address, t.read) for t in r.transactions] == [(0x10, False), (0x11, True)]
    assert r.truncated == 0


def test_parse_expect_forms():
    assert parse_expect("3c:w:01,02,03") == (0x3C, False, [0x01, 0x02, 0x03])
    assert parse_expect("3c:r:a5") == (0x3C, True, [0xA5])
    assert parse_expect("3c:w:") == (0x3C, False, None)  # payload not asserted
    with pytest.raises(ValueError):
        parse_expect("zz:w:00")
    with pytest.raises(ValueError):
        parse_expect("3c:x:00")
    with pytest.raises(ValueError):
        parse_expect("3c")  # missing direction and payload
    with pytest.raises(ValueError):
        parse_expect("80:w:")  # out of 7-bit address range
    with pytest.raises(ValueError):
        parse_expect("3c:w:01,zz")


def test_match_transaction():
    txns = [
        Transaction(0.0, 1.0, 0x3C, False, True, [0x01, 0x02], [True, True]),
        Transaction(2.0, 3.0, 0x3C, True, True, [0xA5], [False]),
    ]
    assert match_transaction(txns, 0x3C, False, [0x01, 0x02])["matched"] is True
    assert match_transaction(txns, 0x3C, True, None)["matched"] is True
    assert match_transaction(txns, 0x3C, True, [0xA5])["index"] == 1
    m = match_transaction(txns, 0x3C, False, [9, 9])
    assert m["matched"] is False and m["mismatch_field"] == "data"
    m = match_transaction(txns, 0x55, False, None)
    assert m["matched"] is False and m["mismatch_field"] == "address"
    assert match_transaction([], 0x3C, False, None)["candidate_index"] is None


def test_cli_expect_pass_fail_and_bad_argument(tmp_path):
    bus = _Bus()
    _write_txn(bus, 0x3C, [0x01, 0x02, 0x03])
    d = _write(tmp_path, bus.exports())
    base = ["i2c", str(d), "--channels", "4,5"]
    assert main([*base, "--expect", "3c:w:01,02,03"]) == 0
    assert main([*base, "--expect", "3c:w:01,02,04"]) == 1
    assert main([*base, "--expect", "3c:w:"]) == 0  # address+direction only
    assert main([*base, "--expect", "zz:w:00"]) == 2
    assert main([*base, "--expect", ""]) == 0  # empty string means no --expect


def test_cli_read_final_nack_exits_zero(tmp_path):
    bus = _Bus()
    _read_txn(bus, 0x3C, [0xA5])
    d = _write(tmp_path, bus.exports())
    assert main(["i2c", str(d), "--channels", "4,5", "--expect", "3c:r:a5"]) == 0


def test_cli_nacked_address_exits_one(tmp_path):
    bus = _Bus()
    bus.start()
    bus.byte(0x3C << 1, False)
    bus.stop()
    d = _write(tmp_path, bus.exports())
    assert main(["i2c", str(d), "--channels", "4,5"]) == 1


def test_cli_truncated_exits_one(tmp_path):
    bus = _Bus()
    bus.start()
    bus.byte(0x3C << 1, True)
    d = _write(tmp_path, bus.exports())
    assert main(["i2c", str(d), "--channels", "4,5"]) == 1


def test_cli_no_transactions_exits_zero(tmp_path):
    d = _write(tmp_path, _Bus().exports())
    assert main(["i2c", str(d), "--channels", "4,5"]) == 0


def test_cli_two_files_and_json_shape(tmp_path, capsys):
    bus = _Bus()
    _write_txn(bus, 0x3C, [0x01])
    d = _write(tmp_path, bus.exports())
    files = [str(d / f"digital_{n}.bin") for n in CHANNELS]
    assert main(["i2c", *files, "--json", "--expect", "3c:w:01"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["transaction_count"] == 1 and out["truncated"] == 0
    assert out["unexpected_nacks"] == 0
    [t] = out["transactions"]
    assert t["address"] == 0x3C and t["read"] is False and t["data"] == [0x01]
    assert out["expect"]["matched"] is True and out["expect"]["index"] == 0


def test_cli_text_report(tmp_path, capsys):
    bus = _Bus()
    _write_txn(bus, 0x3C, [0x01])
    d = _write(tmp_path, bus.exports())
    base = ["i2c", str(d), "--channels", "4,5"]
    assert main([*base, "--expect", "3c:w:01"]) == 0
    out = capsys.readouterr().out
    assert "1 transactions, 0 unexpected NACKs, 0 truncated" in out
    assert "expect: matched transaction 0" in out
    assert main([*base, "--expect", "3c:r:01"]) == 1
    assert "no match" in capsys.readouterr().out


def test_cli_wrong_number_of_files(tmp_path):
    d = _write(tmp_path, _Bus().exports())
    assert main(["i2c", str(d / f"digital_{CHANNELS[0]}.bin")]) == 2
    three = [str(d / f"digital_{n}.bin") for n in (*CHANNELS, 6)]
    assert main(["i2c", *three]) == 2
    assert main(["i2c", str(d), "--channels", "4"]) == 2
