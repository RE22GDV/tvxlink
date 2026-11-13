"""Guards found in the pre-release review.  python -m unittest tests.test_security"""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_protocol import FakeExplorer
from tests.test_tune import FakeTuner, explorer
from tvxlink.device import is_dangerous
from tvxlink.foundlog import FoundLog
from tvxlink.lab import LabProtocol
from tvxlink.protocol import PromaxLink, ProtocolError
from tvxlink.scan import mer_says_locked
from tvxlink.server import origin_ok

SMUGGLED = ("*?NA\r*FD0Atest", "?NA\n*BO", "*?NA*3141&", "*LB2\r*BO","*?NA\x00", "*?NA\t*FD0A", "*?ÑA")


class OneCommandPerLineTest(unittest.TestCase):
    """A second command hidden behind CR/LF or '*' must never reach the wire."""

    def test_protocol_refuses_control_characters_and_a_second_command(self):
        dev = FakeExplorer({"?NA": "TV EXPLORER II+"})
        link = PromaxLink(dev)
        for cmd in SMUGGLED:
            with self.assertRaises(ProtocolError, msg=repr(cmd)):
                link.send(cmd)
        self.assertEqual(dev.sent, [])
        self.assertEqual(link.query("*?NA"), "TV EXPLORER II+")   # an ordinary query still works

    def test_explorer_command_cannot_smuggle_firmware_or_delete(self):
        dev = FakeTuner()
        ex = explorer(dev)
        for cmd in SMUGGLED:
            with self.assertRaises(ProtocolError, msg=repr(cmd)):
                ex.command(cmd, expert=True)
        self.assertEqual(dev.log, [])

    def test_datalogger_name_cannot_carry_a_command(self):
        dev = FakeTuner()
        ex = explorer(dev)
        with self.assertRaises(ProtocolError):
            ex.download_datalogger("LOG1\r*FD0ALOG1", Path(tempfile.mkdtemp()))
        self.assertEqual(dev.log, [])


class FileWriteNeedsExpertTest(unittest.TestCase):
    def test_file_block_writes_need_expert_mode(self):
        dev = FakeTuner()
        ex = explorer(dev)
        for cmd in ("*FL13LOG1", "*FI13", "fl0a"):          # PROTOCOL.md §9: write file blocks
            with self.assertRaises(ProtocolError, msg=cmd):
                ex.command(cmd, expert=False)
        self.assertEqual(dev.log, [])
        for query in ("*?FI0A", "*?FL0ALOG1"):              # reading files is still allowed
            self.assertFalse(is_dangerous(query), query)


class OriginTest(unittest.TestCase):
    def test_only_our_own_page(self):
        for origin in (None, "http://127.0.0.1:8765", "http://localhost:8765"):
            self.assertTrue(origin_ok(origin, 8765), origin)
        for origin in ("http://localhost.evil.com", "http://127.0.0.1.attacker.net", "http://127.0.0.1:9999",
                       "https://127.0.0.1:8765", "null", "http://evil.com", "http://127.0.0.1"):
            self.assertFalse(origin_ok(origin, 8765), origin)


class LockedCsvTest(unittest.TestCase):
    """Excel locks an open CSV on Windows: the journal (jsonl) must still be written."""

    def _locked(self, *_a, **_k):
        raise PermissionError(13, "The process cannot access the file")

    def test_found_log_survives_a_locked_csv(self):
        d = Path(tempfile.mkdtemp())
        log = FoundLog(d)
        with mock.patch.object(FoundLog, "_write_csv", self._locked), contextlib.redirect_stderr(io.StringIO()):
            it = log.record({"pos": "13E", "freq_mhz": 10992.1, "pol": "V", "sr": 27500, "cn": 9.0})
        self.assertEqual(it["freq_mhz"], 10992.1)
        self.assertEqual(len((d / "found" / "found.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_lab_protocol_survives_a_locked_csv(self):
        d = Path(tempfile.mkdtemp())
        prot = LabProtocol(d)
        with mock.patch.object(LabProtocol, "_write_csv", self._locked), contextlib.redirect_stderr(io.StringIO()):
            row = prot.add({"kind": "optimum", "cn": 8.4})
        self.assertEqual(row["id"], 1)
        self.assertEqual(len(prot.list()), 1)


class MerFallbackTest(unittest.TestCase):
    def test_clipped_mer_is_not_a_lock(self):
        self.assertTrue(mer_says_locked({"value": 8.5, "note": None}))
        self.assertTrue(mer_says_locked({"value": 30.0, "note": ">"}))
        self.assertFalse(mer_says_locked({"value": 3.0, "note": "<"}))     # real reply 'ME<+0030' without lock
        self.assertFalse(mer_says_locked({"value": None, "note": "---"}))  # 'ME!'


if __name__ == "__main__":
    unittest.main()
