"""Channel scans and spectrum sweeps on the simulator.  python -m unittest tests.test_scan"""
import tempfile
import time
import unittest
from pathlib import Path

from tvxlink.device import Explorer
from tvxlink.scan import Automation


def wait(job, limit=60):
    t = time.monotonic()
    while job.running and time.monotonic() - t < limit:
        time.sleep(0.05)


class ScanTest(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.ex = Explorer(d / "logs")
        self.ex.connect(demo=True)
        self.ex._wait_ready = lambda timeout=10.0: True   # the simulator is ready at once
        self.auto = Automation(self.ex, d)

    def tearDown(self):
        self.ex.disconnect()

    def test_scan_ranks(self):
        sat = self.auto.sats["16E"]
        picks = [{"pos": "16E", "freq_mhz": t["freq_mhz"], "pol": t["pol"], "sr": t["sr"]}
                 for t in sat["transponders"] if t["supported"] and 11400 < t["freq_mhz"] < 11600]
        job = self.auto.start_scan(picks, dwell=0)
        wait(job)
        self.assertIsNone(job.error)
        self.assertEqual(job.done, len(picks))
        self.assertTrue(any(i["locked"] for i in job.items))
        self.assertTrue((job.dir / "scan.csv").exists())

    def test_scan_uses_device_lock(self):
        sat = self.auto.sats["16E"]
        picks = [{"pos": "16E", "freq_mhz": t["freq_mhz"], "pol": t["pol"], "sr": t["sr"]}
                 for t in sat["transponders"] if t["supported"] and 11400 < t["freq_mhz"] < 11600]
        job = self.auto.start_scan(picks, dwell=0.3)
        wait(job)
        for it in job.items:        # "locked" comes strictly from the meter's *?ST
            self.assertEqual(it["locked"], it["device_status"].startswith("MPEG-2 TS"))
            self.assertEqual(it["locked"], it["lock_s"] is not None)

    def test_seek_stops_on_first_lock(self):
        sat = self.auto.sats["16E"]
        tps = sorted((t for t in sat["transponders"] if t["supported"] and t["pol"] == "V" and t["band"] == "low"),
                     key=lambda t: t["freq_mhz"])
        picks = [{"pos": "16E", "freq_mhz": t["freq_mhz"], "pol": t["pol"], "sr": t["sr"]} for t in tps]
        job = self.auto.start_scan(picks, dwell=0.3, stop_on_lock=True, keep_order=True)
        wait(job)
        self.assertEqual(job.kind, "seek")
        self.assertTrue(job.items[-1]["locked"])                       # stopped on a locked one
        self.assertTrue(all(not i["locked"] for i in job.items[:-1]))  # none locked before it
        self.assertEqual([i["freq_mhz"] for i in job.items], [p["freq_mhz"] for p in picks[:len(job.items)]])

    def test_sweep(self):
        job = self.auto.start_sweep(11500, 200, 4, 9750, "13V", settle=0)
        wait(job)
        self.assertIsNone(job.error)
        lv = [i["level"] for i in job.items]
        self.assertEqual(len(lv), job.total)
        self.assertGreater(max(lv) - min(lv), 5)   # transponder humps are visible


if __name__ == "__main__":
    unittest.main()
