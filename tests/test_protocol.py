"""Protocol checks against an emulated meter (no hardware):  python -m unittest tests.test_protocol"""
import unittest

from tvxlink.protocol import PromaxLink, ProtocolError, XON, XOFF, ACK, NAK


class FakeExplorer:
    """Behaves like the meter: XON, then for '*CMD\\r' — XOFF, ACK/NAK, reply, XON."""

    def __init__(self, answers):
        self.answers = answers
        self.out = bytearray([XON])
        self.sent = []

    def write(self, data):
        self.sent.append(data)
        cmd = data.decode().strip()[1:]
        self.out += bytes([XOFF])
        if cmd in self.answers:
            self.out += bytes([ACK])
            ans = self.answers[cmd]
            if ans is not None:
                self.out += ("*" + ans + "\r").encode()
        else:
            self.out += bytes([NAK])
        self.out += bytes([XON])

    def read(self, size=4096, timeout_ms=100):
        # hand out 3-byte chunks like a slow UART
        chunk, self.out = bytes(self.out[:3]), self.out[3:]
        return chunk


class ProtocolTest(unittest.TestCase):
    def test_query_and_order(self):
        dev = FakeExplorer({"?NA": "TV EXPLORER II+", "?VE": "3.10", "RM": None})
        link = PromaxLink(dev)
        self.assertEqual(link.query("*?NA"), "TV EXPLORER II+")
        self.assertEqual(link.query("?VE"), "3.10")
        self.assertTrue(link.send("*RM").ack)
        self.assertEqual(dev.sent[0], b"*?NA\r")

    def test_nak(self):
        link = PromaxLink(FakeExplorer({}))
        self.assertFalse(link.send("*?XX").ack)
        with self.assertRaises(ProtocolError):
            link.query("*?XX")

    def test_no_xon(self):
        class Dead:
            def write(self, d): pass
            def read(self, size=0, timeout_ms=0): return b""
        self.assertFalse(PromaxLink(Dead()).wait_xon(timeout=0.3))


class ParseTest(unittest.TestCase):
    def test_parse(self):
        from tvxlink.device import parse_measure
        self.assertEqual(parse_measure("*?FR", "FR11471000")["value"], 11471.0)
        self.assertEqual(parse_measure("*?LV", "LV +0652")["value"], 65.2)
        self.assertEqual(parse_measure("*?CN", "CN -0031")["value"], -3.1)
        self.assertEqual(parse_measure("*?ME", "ME!")["note"], "---")
        self.assertEqual(parse_measure("*?LV", "LV <0300")["note"], "<")
        # Replies of a real TV EXPLORER II+ 1.30 (2026-11-12), checked against the meter screen.
        self.assertEqual(parse_measure("*?LV", "LV +0591"), {"value": 59.1, "note": None})
        self.assertEqual(parse_measure("*?CN", "CN -0060"), {"value": -6.0, "note": None})
        self.assertEqual(parse_measure("*?ME", "ME<+0030"), {"value": 3.0, "note": "<"})
        self.assertEqual(parse_measure("*?CB", "CB>+10E-2"), {"value": 0.1, "note": ">"})   # screen ">1.0E-1"
        self.assertEqual(parse_measure("*?VB", "VB>+10E-4"), {"value": 0.001, "note": ">"}) # screen ">1.0E-3"


if __name__ == "__main__":
    unittest.main()
