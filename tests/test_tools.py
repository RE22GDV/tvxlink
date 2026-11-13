"""Command-line tools (tvx, selftest) against the simulator.  python -m unittest tests.test_tools"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TVXLINK_DATA"] = self.tmp

    def run_tvx(self, *argv):
        from tvxlink.cli import tvx
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = tvx.main(["--port", "demo", "--json", *argv])
        return code, json.loads(buf.getvalue())

    def test_tvx_ident_query_tune(self):
        code, d = self.run_tvx("ident")
        self.assertEqual(code, 0)
        self.assertIn("SIMULATOR", d["info"]["model"])
        code, d = self.run_tvx("q", "LV", "*?SA", "*?FL0Ax")
        self.assertTrue(d["results"][0]["ack"])
        self.assertIn("error", d["results"][2])          # file transfers through q are refused
        code, d = self.run_tvx("tune", "10992.1", "--sr", "27500", "--fec", "2/3", "--dwell", "0")
        self.assertIn("*FR1242100", d["sent"])

    def test_tvx_refuses_dangerous(self):
        code, d = self.run_tvx("set", "*BO")
        self.assertEqual(code, 2)
        self.assertIn("error", d)

    def test_selftest_demo(self):
        from tvxlink.cli import selftest
        with contextlib.redirect_stdout(io.StringIO()):
            code = selftest.main(["--port", "demo", "--skip-unittests", "--listen", "1.2", "--tune", "--verified", "3",
                                  "--sat", "13E", "--dwell", "0", "--samples", "3"])
        rep = next(Path(self.tmp, "selftest").glob("*/report.json"))
        r = json.loads(rep.read_text(encoding="utf-8"))
        self.assertTrue(r["verdict"].startswith("go"), r["verdict"])
        bad = [c for c in r["checks"] if c["status"] == "FAIL"]
        self.assertEqual(bad, [])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
