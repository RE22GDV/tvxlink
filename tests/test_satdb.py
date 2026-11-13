"""Updating the satellite databases (offline: downloads are replaced by files from reverse/satellites_raw).
python3 -m unittest tests.test_satdb"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tvxlink import satdb

RAW = Path(__file__).resolve().parent.parent / "reverse" / "satellites_raw"


def fake_fetch(url):
    pos = url.split("pos=")[-1] if "dl.php" in url else url.split("pos-")[-1]
    name = f"dl_{pos}.bin" if "dl.php" in url else f"kos_{pos}.html"
    return (RAW / name).read_text(encoding="utf-8", errors="replace")


class SatDbTest(unittest.TestCase):
    def setUp(self):
        self.orig = satdb.fetch
        satdb.fetch = fake_fetch

    def tearDown(self):
        satdb.fetch = self.orig

    @unittest.skipUnless(RAW.exists(), "reverse/satellites_raw is not present (it is not shipped)")
    def test_update_all_and_load(self):
        d = Path(tempfile.mkdtemp())
        os.environ["TVXLINK_DATA"] = str(d)
        from tvxlink.scan import load_satellites, user_sat_dir
        old = load_satellites()
        rep = satdb.update_all(user_sat_dir(), positions=["13E", "16E"], old=old, pause=0)
        self.assertTrue(all(r["ok"] for r in rep), rep)
        self.assertTrue((user_sat_dir() / "16E.json").exists())
        self.assertEqual(rep[1]["after"], len(old["16E"]["transponders"]))
        new = load_satellites()
        self.assertTrue(new["16E"]["_updated"])
        self.assertTrue(any(t.get("verified") for t in new["13E"]["transponders"]))   # "verified" marks survive

    def test_broken_page_keeps_old(self):
        d = Path(tempfile.mkdtemp())
        satdb.fetch = lambda url: "<html>the site changed its layout</html>"
        rep = satdb.update_all(d, positions=["16E"], old={"16E": {"transponders": [{}] * 150}}, pause=0)
        self.assertFalse(rep[0]["ok"])
        self.assertFalse((d / "16E.json").exists())


if __name__ == "__main__":
    unittest.main()
