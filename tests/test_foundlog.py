"""Log of found channels.  python -m unittest tests.test_foundlog"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from tvxlink.device import Explorer
from tvxlink.foundlog import FoundLog
from tvxlink.scan import Automation


class FoundLogTest(unittest.TestCase):
    def test_dedup_better_and_persist(self):
        d = Path(tempfile.mkdtemp())
        log = FoundLog(d)
        ev = {"pos": "13E", "freq_mhz": 10992.1, "pol": "V", "sr": 27500, "system": "DVB-S", "cn": 9.0, "mer": 8.0,
              "device_status": "MPEG-2 TS DVB-S", "channels": ["Camera dei Deputati"], "source": "tune"}
        log.record(ev)
        log.record({**ev, "cn": 8.5})                 # worse and right away — not written
        log.record({**ev, "cn": 11.2})                # better — written
        lines = (d / "found" / "found.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        it = log.list()[0]
        self.assertEqual((it["best_cn"], it["times"]), (11.2, 2))
        again = FoundLog(d)                            # everything is back after a restart
        self.assertEqual(again.list()[0]["best_cn"], 11.2)
        self.assertIn("Camera dei Deputati", (d / "found" / "found.csv").read_text(encoding="utf-8-sig"))

    def test_scan_records_locked(self):
        d = Path(tempfile.mkdtemp())
        ex = Explorer(d / "logs")
        ex.connect(demo=True)
        try:
            auto = Automation(ex, d)
            auto.found = FoundLog(d)
            sat = auto.sats["16E"]
            picks = [{"pos": "16E", "freq_mhz": t["freq_mhz"], "pol": t["pol"], "sr": t["sr"]}
                     for t in sat["transponders"] if t["supported"] and 11400 < t["freq_mhz"] < 11600][:6]
            job = auto.start_scan(picks, dwell=0.3)
            t0 = time.monotonic()
            while job.running and time.monotonic() - t0 < 60:
                time.sleep(0.05)
            locked = {(i["freq_mhz"], i["pol"]) for i in job.items if i["locked"]}
            logged = {(i["freq_mhz"], i["pol"]) for i in auto.found.list()}
            self.assertEqual(locked, logged)
            self.assertTrue(all(i["source"] == "scan" and i["pos"] == "16E" for i in auto.found.list()))
        finally:
            ex.disconnect()


if __name__ == "__main__":
    unittest.main()
