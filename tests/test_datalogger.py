"""Datalogger: .dl parsing and the transfer from an emulated meter.  python -m unittest tests.test_datalogger"""
import struct
import unittest

from tvxlink.dlfile import parse_dl
from tvxlink.protocol import PromaxLink, XON, XOFF, ACK


def build_dl():
    """Builds a file following NetUpdate3 (0x43a1c0 / 0x43cbd0)."""
    name = b"LAB9"
    # plan: 2 channels, no global attributes → FF FF right away
    plan = bytearray(struct.pack("<H", 2) + b"\x01" + b"\xff\xff")
    def chan(nm, f_khz, attrs):
        rec = bytearray(b"\x00\x00\x00")          # +5..+7
        rec += nm.ljust(8).encode()                 # +8..+0xf name
        rec += struct.pack("<II", f_khz, f_khz)     # +0x10, +0x14
        rec += attrs + b"\xff\xff"
        rec += b"\x00" * 5                          # record step = s + 0x1a
        return rec
    plan += chan("S12", 11471000, bytes([2, 2, 0, 0, 0, 11, 2, 0, 0, 0]))
    plan += chan("C21", 474000, bytes([2, 0, 0, 0, 0]))
    plan += b"\x00" * 8
    body = bytearray([0xDA, len(name)]) + name + struct.pack("<I", len(plan))[:3] + plan
    # record header: k = 0x17 (id + date)
    hdr = bytearray(5)
    k = 0x17
    hdr[:] = body[-0:] if False else hdr
    p = len(name) + len(plan)
    body += b"\x00" * (p + 5 - len(body))
    body += bytes([k]) + b"TVEXPLORER2PLUS ".ljust(16)[:16]
    body += bytes([29, 10]) + struct.pack("<H", 2026) + bytes([11, 30])
    body += b"\x00" * (p + k + 6 - len(body))
    # type table: channel 1 — Level, C/N, MER, VBER, MPEG; channel 2 — Level
    body += bytes([5, 0, 2, 4, 6, 8, 1]) + (9750000).to_bytes(3, "little")
    body += bytes([1, 0, 0]) + b"\x00\x00\x00"
    body += struct.pack("<H", 1)                     # number of test points
    body += bytes([3]) + b"TP1" + bytes([1])         # point name, units dBµV
    body += bytes([0x00]) + struct.pack("<h", 652)   # Level 65.2
    body += bytes([0x00]) + struct.pack("<h", 192)   # C/N 19.2
    body += bytes([0x80]) + struct.pack("<h", 120)   # MER >12.0
    body += bytes([0x00, 23, 0xFC])                  # VBER 2.3E-4
    body += bytes([1, 0, 0])                         # LOCKED
    body += bytes([0xC0, 0, 0])                      # Level of channel 2: !!
    return bytes(body)


class ParseTest(unittest.TestCase):
    def test_synthetic(self):
        d = parse_dl(build_dl())
        self.assertEqual(d["name"], "LAB9")
        self.assertEqual(d["header"]["datetime"], "2026-10-29 11:30")
        ch = d["plan"]["channels"]
        self.assertEqual([c["name"] for c in ch], ["S12", "C21"])
        self.assertEqual(ch[0]["standard"], "DVB-S (QPSK)")
        self.assertEqual(ch[0]["freq_khz"], 11471000 - 9750000)
        t = d["tests"][0]
        self.assertEqual(t["name"], "TP1")
        texts = [m["text"] for m in t["rows"][0]["measures"]]
        self.assertEqual(texts, ["65.2", "19.2", ">12.0", "2.3E-4", "LOCKED"])
        self.assertEqual(t["rows"][1]["measures"][0]["text"], "!!")


class FakeExplorerFiles:
    """Emulator: *?FI0A, *?FI0ANN and the *?FL0A<name> stream."""

    def __init__(self, files):
        self.files = files
        self.out = bytearray([XON])

    def write(self, data):
        cmd = data.decode("latin-1").strip()[1:]
        self.out += bytes([XOFF, ACK])
        names = list(self.files)
        if cmd == "?FI0A":
            self.out += f"*FI{len(names):02X}\r".encode()
        elif cmd.startswith("?FI0A"):
            nm = names[int(cmd[5:7], 16)]
            self.out += f"*FI{int(cmd[5:7], 16):02X}00000000{nm}\r".encode()
        elif cmd.startswith("?FL0A"):
            payload = self.files[cmd[5:]] + b"\x00\x00\x00\x00"
            self.out += f"*FL{len(payload):08X}".encode() + payload
        self.out += bytes([XON])

    def read(self, size=4096, timeout_ms=100):
        chunk, self.out = bytes(self.out[:64]), self.out[64:]
        return chunk


class TransferTest(unittest.TestCase):
    def test_list_and_download(self):
        from pathlib import Path
        import tempfile
        from tvxlink.device import Explorer
        data = build_dl()
        ex = Explorer(Path(tempfile.mkdtemp()))
        ex.link = PromaxLink(FakeExplorerFiles({"LAB9": data, "SAT2": data}))
        files = ex.list_datalogger()
        self.assertEqual([f["name"] for f in files], ["LAB9", "SAT2"])
        path = ex.download_datalogger("LAB9", Path(tempfile.mkdtemp()))
        self.assertEqual(path.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
