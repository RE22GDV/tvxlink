"""Satellite tuning (RM-404 commands) and the *?ST lock status.  python -m unittest tests.test_tune"""
import tempfile
import unittest
from pathlib import Path

from tvxlink.device import Explorer, LOCK_ST
from tvxlink.protocol import PromaxLink, XON, XOFF, ACK


class FakeTuner:
    """A meter that locks once everything in lock_after has been set."""

    def __init__(self, lock_after=("FR", "RA")):
        self.out = bytearray([XON])
        self.mode = 0
        self.set = {}
        self.lock_after = lock_after
        self.log = []

    def write(self, data):
        cmd = data.decode().strip()
        self.log.append(cmd)
        self.out += bytes([XOFF, ACK])
        if cmd == "*?SA":
            self.out += f"*SA{self.mode:02X}\r".encode()
        elif cmd == "*?ST":
            locked = all(k in self.set for k in self.lock_after)
            self.out += f"*ST{(LOCK_ST[self.mode] if locked else 1):02X}\r".encode()
        elif cmd.startswith("*SA"):
            self.mode = int(cmd[3:], 16)
            self.set = {}
        elif not cmd.startswith("*?"):
            self.set[cmd[1:3]] = cmd
        self.out += bytes([XON])

    def read(self, size=4096, timeout_ms=100):
        chunk, self.out = bytes(self.out[:32]), self.out[32:]
        return chunk


def explorer(dev):
    ex = Explorer(Path(tempfile.mkdtemp()))
    ex.link = PromaxLink(dev)
    ex.settle_after_sa = 0
    ex.settle_after_set = 0.05
    return ex


class TuneTest(unittest.TestCase):
    def test_variant9_full(self):
        dev = FakeTuner(lock_after=("FR", "RA", "SI"))
        r = explorer(dev).tune_satellite(11471, 9750, 29950, "3/4", lnb="13V", standard="DVB-S")
        self.assertEqual(r["if_mhz"], 1721.0)
        sets = [c for c in dev.log if not c.startswith("*?")]
        self.assertEqual(sets, ["*SA3", "*LB2", "*FR1721000", "*CR02", "*RA29950", "*SI0"])
        self.assertTrue(r["locked"])
        self.assertEqual(r["st"], 6)
        self.assertEqual(r["status"], "MPEG-2 TS DVB-S")

    def test_stops_when_locked(self):
        # the meter locked right after the frequency — like RM-404, FEC/SR/inversion are not sent
        dev = FakeTuner(lock_after=("FR",))
        r = explorer(dev).tune_satellite(11471, 9750, 29950, "3/4")
        sets = [c for c in dev.log if not c.startswith("*?")]
        self.assertEqual(sets, ["*SA3", "*LB2", "*FR1721000"])
        self.assertEqual(r["skipped"], ["*CR02", "*RA29950", "*SI0"])
        self.assertTrue(r["locked"])

    def test_dvbs2_8psk_not_locked(self):
        dev = FakeTuner(lock_after=("XX",))     # never locks
        r = explorer(dev).tune_satellite(11471, 9750, 29950, "3/4", standard="DVB-S2", modulation="8PSK")
        sets = [c for c in dev.log if not c.startswith("*?")]
        self.assertEqual(sets, ["*SA7", "*LB2", "*FR1721000", "*CR02", "*RA29950", "*CO09", "*SI0"])
        self.assertFalse(r["locked"])
        self.assertIn("1", r["status"])

    def test_lock_status_codes(self):
        dev = FakeTuner(lock_after=())
        ex = explorer(dev)
        ex.link.send("*SA7")
        st = ex.lock_status()
        self.assertEqual((st["sa"], st["st"], st["locked"], st["text"]), (7, 8, True, "MPEG-2 TS DVB-S2"))


if __name__ == "__main__":
    unittest.main()


class SafetyTest(unittest.TestCase):
    def test_firmware_and_delete_refused_even_in_expert_mode(self):
        from tvxlink.protocol import ProtocolError
        dev = FakeTuner()
        ex = explorer(dev)
        for cmd in ("*BO", "*3141&", "*SB230400", "*FD0A", "fd13", "UpdEII.pro"):
            with self.assertRaises(ProtocolError, msg=cmd):
                ex.command(cmd, expert=True)
        self.assertEqual(dev.log, [])                   # nothing reached the meter
        with self.assertRaises(ProtocolError):
            ex.command("*RM", expert=False)              # write command without expert mode
        self.assertTrue(ex.command("*LB2")["ack"])       # tuning commands pass
