"""Lab assistant: look angles, BER theory, QPSK↔8PSK pairs, protocol, journal, threshold run.
python -m unittest tests.test_lab"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from tvxlink import i18n, lab
from tvxlink.scan import Automation, load_satellites


class CalcTest(unittest.TestCase):
    def test_look_angles_match_the_lab_manual(self):
        # Kyiv lab dish → 16°E: Az 198.48965°, El 30.59637°, d 38563.09 km, b ≈ 51.93038° (worked example of the manual).
        a = lab.look_angles(lab.LAB_LAT, lab.LAB_LON, 16.0)
        self.assertAlmostEqual(a["az"], 198.48965, places=4)
        self.assertAlmostEqual(a["el"], 30.59637, places=4)
        self.assertAlmostEqual(a["d_km"], 38563.09, places=1)
        self.assertAlmostEqual(a["b_deg"], 51.93038, places=4)
        self.assertTrue(a["visible"])
        self.assertLess(a["skew"], 0)                       # the satellite is west of Kyiv

    def test_parse_coord(self):
        self.assertAlmostEqual(lab.parse_coord("50°26′51,15″ N"), 50.44754167, places=7)
        self.assertAlmostEqual(lab.parse_coord("30 27 28.12 E"), 30.45781111, places=7)
        self.assertAlmostEqual(lab.parse_coord("50,5"), 50.5)
        self.assertAlmostEqual(lab.parse_coord("5°30' W"), -5.5)
        self.assertAlmostEqual(lab.parse_coord("5°30' Зх"), -5.5)          # Ukrainian "west"
        self.assertEqual(lab.sat_longitude("19.2E"), 19.2)
        self.assertEqual(lab.sat_longitude("5W"), -5.0)
        with self.assertRaises(ValueError):
            lab.parse_coord("somewhere")

    def test_ber_theory_matches_the_lab_manual(self):
        # C/N 19.21651 dB → Eb/N0 17.66749 dB → BER ≈ 1.52·10⁻²⁷ (worked example of the manual).
        self.assertAlmostEqual(lab.ebn0_db(19.21651), 17.66749, places=4)
        self.assertAlmostEqual(lab.ber_theory(19.21651) / 1e-27, 1.52, places=1)
        for cn in (4, 8, 12):                               # 8PSK is worse than QPSK at the same C/N
            self.assertGreater(lab.ber_theory(cn, "8PSK"), lab.ber_theory(cn, "QPSK"))
        c = lab.ber_curve("QPSK")
        self.assertTrue(all(c[i][1] > c[i + 1][1] for i in range(len(c) - 1)))
        self.assertGreaterEqual(c[-1][1], lab.BER_FLOOR)

    def test_qef(self):
        q = lab.qef_cn("DVB-S2", "8PSK", "2/3")
        self.assertEqual(q["esn0"], 6.62)
        self.assertAlmostEqual(q["cn"], 6.62 - 1.461, places=2)
        self.assertIsNone(lab.qef_cn("DVB-S", "QPSK", "9/10"))

    def test_ber_value_real_meter_replies(self):
        # Replies of a real TV EXPLORER II+ 1.30 (2026-11-12) and the screen values they stand for.
        self.assertEqual(lab.ber_value("CB +38E-3"), 3.8e-2)
        self.assertEqual(lab.ber_value("VB +29E-5"), 2.9e-4)
        self.assertEqual(lab.ber_value("CB>+10E-2"), 0.1)
        self.assertEqual(lab.ber_value("VB<+10E-9"), 1e-8)
        self.assertEqual(lab.ber_value("VB +36E-9"), 3.6e-8)
        self.assertEqual(lab.ber_value("CB 23-4"), 2.3e-4)              # early guess, still accepted
        self.assertIsNone(lab.ber_value("CB!"))
        self.assertIsNone(lab.ber_value(None))
        self.assertIsNone(lab.ber_value("CB???"))

    def test_ber_bound(self):
        self.assertEqual(lab.ber_bound("VB<+10E-9"), "<")
        self.assertEqual(lab.ber_bound("CB>+10E-2"), ">")
        self.assertIsNone(lab.ber_bound("CB +38E-3"))
        self.assertIsNone(lab.ber_bound("CB!"))
        self.assertIsNone(lab.ber_bound(None))


def tp(f, pol="V", sr=27500, system="DVB-S", mod="QPSK", fec="3/4", band="low", ch=("A",)):
    return {"freq_mhz": f, "pol": pol, "sr": sr, "system": system, "modulation": mod, "fec": fec, "band": band,
            "lo_mhz": 9750, "lnb": "13V", "if_mhz": f - 9750, "required_cn_db": 6.0 if mod == "QPSK" else 9.0,
            "video": ["MPEG-2"], "mpeg2_sd": [], "verified": None, "supported": True,
            "channels": [{"name": c} for c in ch]}


class PairsTest(unittest.TestCase):
    def test_nearest_same_pol_and_band(self):
        sat = {"transponders": [tp(11000, mod="8PSK", system="DVB-S2", fec="2/3"),
                                tp(11010, pol="H"),             # closer, but the other polarisation
                                tp(11040),                       # same polarisation, further away
                                tp(11800, band="high")]}
        p = lab.pairs(sat)
        self.assertEqual(len(p), 1)
        best = p[0]["options"][0]
        self.assertEqual(best["qpsk"]["freq_mhz"], 11040)
        self.assertEqual(best["df_mhz"], 40)
        self.assertIn({"k": "standard", "a": "DVB-S", "b": "DVB-S2"}, best["diffs"])
        self.assertIn({"k": "fec", "a": "3/4", "b": "2/3"}, best["diffs"])
        self.assertEqual(best["d_req_cn"], 3.0)

    def test_real_database(self):
        p = lab.pairs(load_satellites()["13E"])
        self.assertTrue(p)
        for row in p[:10]:
            self.assertEqual(row["psk8"]["modulation"], "8PSK")
            self.assertTrue(all(o["qpsk"]["modulation"] == "QPSK" for o in row["options"]))


class RecommendTest(unittest.TestCase):
    def setUp(self):
        self.sats = load_satellites()

    def test_verified_first_on_13e(self):
        r = lab.recommend(self.sats["13E"])
        self.assertTrue(r["qpsk"][0]["verified"])                # confirmed on a real meter
        self.assertEqual(r["qpsk"][0]["system"], "DVB-S")
        self.assertTrue(all(x["modulation"] == "8PSK" for x in r["psk8"]))
        self.assertIn("verified", [w["k"] for w in r["qpsk"][0]["why"]])

    def test_every_reason_is_translated(self):
        for pos in ("9E", "13E", "16E", "19.2E", "28.2E"):
            r = lab.recommend(self.sats[pos])
            for t in r["qpsk"] + r["psk8"]:
                for w in t["why"]:
                    self.assertIn("why." + w["k"], i18n.catalog("en"))
                    self.assertIn("why." + w["k"], i18n.catalog("uk"))

    def test_fta_parsed(self):
        # KingOfSat writes "Clear" for free-to-air channels (it was once read as encrypted).
        t = next(t for t in self.sats["13E"]["transponders"] if t.get("verified") and t["freq_mhz"] < 11000)
        self.assertGreater(t["fta_count"], 0)
        self.assertGreater(sum(x["fta_count"] > 0 for x in self.sats["19.2E"]["transponders"]), 10)


class ProtocolTest(unittest.TestCase):
    def sample(self, cn, locked=True, cb="CB +23E-5"):
        return {"LV": {"value": 60.0}, "CN": {"value": cn}, "ME": {"value": cn - 1}, "CB": {"raw": cb}, "VB": {"raw": "VB<+10E-9"},
                "status": {"locked": locked, "text": "MPEG-2 TS DVB-S" if locked else "No signal received", "st": 6 if locked else 0}}

    def test_aggregate(self):
        a = lab.aggregate([self.sample(10.0), self.sample(11.0), self.sample(12.0)])
        self.assertEqual((a["n"], a["cn"], a["cn_min"], a["cn_max"]), (3, 11.0, 10.0, 12.0))
        self.assertIs(a["locked"], True)
        self.assertEqual(a["cber_v"], 2.3e-4)
        self.assertEqual(a["vber_v"], 1e-8)
        self.assertEqual(lab.aggregate([self.sample(5, False), self.sample(9)])["locked"], "partial")
        self.assertIs(lab.aggregate([self.sample(5, False)])["locked"], False)

    def test_add_delete_persist(self):
        d = Path(tempfile.mkdtemp())
        p = lab.LabProtocol(d)
        r1 = p.add({"kind": "optimum", "system": "DVB-S", "cn": 12.3})
        r2 = p.add({"kind": "something", "system": "DVB-S2", "cn": 7.0})
        self.assertEqual((r1["id"], r2["id"]), (1, 2))
        self.assertEqual(r2["kind"], "measure")              # unknown kind → measure
        self.assertEqual((r1["post_name"], r2["post_name"]), ("VBER", "LBER"))
        p.delete(1)
        again = lab.LabProtocol(d)                           # after a restart
        self.assertEqual([r["id"] for r in again.list()], [2])
        self.assertEqual(len((d / "lab" / "protocol.jsonl").read_text(encoding="utf-8").splitlines()), 3)
        csv_text = (d / "lab" / "protocol.csv").read_text(encoding="utf-8-sig")
        self.assertIn("LBER", csv_text)
        self.assertEqual(again.add({"kind": "edge"})["id"], 3)

    def test_kind_codes_are_normalised(self):
        d = Path(tempfile.mkdtemp())
        (d / "lab").mkdir()
        rows = [{"id": 1, "time": "2026-11-12T12:26:27", "kind": "optimum", "locked": True},
                {"id": 2, "time": "2026-11-12T12:30:50", "kind": "EDGE", "locked": "partial"},
                {"id": 3, "time": "2026-11-12T12:38:42", "kind": "something else"}]
        (d / "lab" / "protocol.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        got = lab.LabProtocol(d).list()
        self.assertEqual([r["kind"] for r in got], ["optimum", "edge", "measure"])
        self.assertEqual(got[1]["locked"], "partial")


class JournalTest(unittest.TestCase):
    def test_journal(self):
        d = Path(tempfile.mkdtemp())
        j = lab.LabJournal(d)
        ev = j.add("  photographed   the constellation ", "agent", {"pos": "13E", "freq_mhz": 10992.1, "pol": "V"})
        self.assertEqual(ev["text"], "photographed the constellation")
        self.assertEqual(ev["channel"], "13E 10992.1 V")
        j.add("picture is fine")
        self.assertEqual([e["text"] for e in lab.LabJournal(d).list()], ["photographed the constellation", "picture is fine"])
        self.assertIn("picture is fine", (d / "lab" / "journal.txt").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            j.add("   ")


class LabChatTest(unittest.TestCase):
    def test_no_window_says_how_to_start(self):
        from tvxlink.cli import lab as labcli
        i18n.set_lang("en")
        try:
            with self.assertRaises(SystemExit) as cm:
                labcli.main(["--url", "http://127.0.0.1:9", "status"])
        finally:
            i18n.set_lang("uk")
        self.assertIn("Start-Windows.bat", str(cm.exception))


class FakeEx:
    """A meter that loses lock on the 4th and 5th readings."""
    connected = True

    def __init__(self):
        self.i = -1
        self.seq = [True, True, True, False, False, True, True]

    def lock_status(self):
        self.i += 1
        on = self.seq[min(self.i, len(self.seq) - 1)]
        return {"locked": on, "st": 6 if on else 0, "text": "MPEG-2 TS DVB-S" if on else "No signal received", "mode": 3, "sa": 3}

    def read(self, cmds):
        cn = 12.0 - self.i
        on = self.seq[min(self.i, len(self.seq) - 1)]
        return {"LV": {"value": 60.0}, "CN": {"value": cn}, "ME": {"value": cn - 1 if on else None},
                "CB": {"raw": "CB +23E-5" if on else "CB>+10E-2"}, "VB": {"raw": "VB<+10E-9" if on else "VB>+10E-4"}}

    def log(self, *_):
        pass


class EdgeTest(unittest.TestCase):
    def test_edge_finds_last_lock_before_loss(self):
        d = Path(tempfile.mkdtemp())
        auto = Automation(FakeEx(), d)
        job = auto.start_edge(interval=0.3)
        t = time.monotonic()
        while job.done < 7 and time.monotonic() - t < 10:
            time.sleep(0.05)
        auto.stop()
        while job.running:
            time.sleep(0.05)
        self.assertIsNone(job.error)
        x = job.extra
        self.assertEqual(x["losses"], 1)
        self.assertEqual(x["edge"]["cn"], 10.0)              # the 3rd reading is the last locked one
        self.assertEqual(x["edge"]["run"], 3)
        self.assertEqual(x["loss"]["cn"], 9.0)
        self.assertEqual(x["edge"]["cber_v"], 2.3e-4)
        self.assertTrue((job.dir / "edge.csv").exists())


if __name__ == "__main__":
    unittest.main()
