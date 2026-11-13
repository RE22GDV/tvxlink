"""Automation on top of the meter: channel scans, spectrum sweeps, threshold runs.

Jobs run in the background, one at a time; the page polls their state.
Every job writes its results to <data>/sessions/<time>_<kind>/.
"""

from __future__ import annotations

import csv
import json
import statistics
import threading
import traceback
import time
from datetime import datetime
from pathlib import Path

from .device import LNB_CODES
from .i18n import tr
from .lab import ber_value
from .paths import data_dir
from .protocol import ProtocolError

SAT_DIR = Path(__file__).resolve().parent / "satellites"
LOCK_QUERIES = ["*?LV", "*?CN", "*?ME", "*?CB", "*?VB"]
GOOD_MARGIN_DB = 3.0      # C/N margin over the threshold that counts as a "solid" channel
STATUSES = ("excellent", "locked", "marginal", "no_lock")


def load_verified() -> list[dict]:
    """Channels confirmed on a real TV EXPLORER (satellites/verified.json)."""
    f = SAT_DIR / "verified.json"
    return json.loads(f.read_text(encoding="utf-8"))["items"] if f.exists() else []


def user_sat_dir() -> Path:
    """Where "Update channel databases" puts fresh databases (outside the program folder)."""
    return data_dir() / "satellites"


def load_satellites() -> dict:
    """Position databases (the newer of shipped / user-updated) with the
    "verified on a real meter" mark (verified.json) on matching transponders."""
    out = {}
    for folder in (SAT_DIR, user_sat_dir()):
        for f in folder.glob("*.json") if folder.exists() else []:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if "position" not in d:
                continue
            cur = out.get(d["position"])
            if cur is None or d.get("built", "") >= cur.get("built", ""):
                d["_file"] = str(f)
                d["_updated"] = folder != SAT_DIR      # updated with the button in the window
                out[d["position"]] = d
    order = lambda p: float(p.rstrip("EW")) if p[0].isdigit() else 999  # noqa: E731
    out = {k: out[k] for k in sorted(out, key=order)}
    for c in load_verified():
        sat = out.get(c["pos"])
        if not sat:
            continue
        for t in sat["transponders"]:
            if t["pol"] == c["pol"] and abs(t["freq_mhz"] - c["freq_mhz"]) < 0.6 and t["sr"] == c["sr"]:
                t.setdefault("verified", []).append(c["name"])
    return out


def classify(margin, locked: bool) -> str:
    """Verdict for a transponder: lock comes from the meter (*?ST), margin from C/N over the threshold.

    excellent — locked with ≥ 3 dB margin; locked — no threshold known;
    marginal — the meter decodes but the margin is < 3 dB; no_lock — the meter did not lock."""
    if not locked:
        return "no_lock"
    if margin is None:
        return "locked"
    if margin >= GOOD_MARGIN_DB:
        return "excellent"
    return "marginal"



def mer_says_locked(me: dict) -> bool:
    """Fallback when the meter does not answer *?ST: a MER value means lock, but not 'ME<+0030'
    (below the scale) — an unlocked meter answers exactly that (log of 12.11.2026)."""
    return me.get("value") is not None and me.get("note") != "<"

class Job:
    def __init__(self, kind: str, total: int):
        self.kind = kind
        self.total = total
        self.done = 0
        self.items: list[dict] = []
        self.current = ""
        self.error = None
        self.running = True
        self.cancel = False
        self.started = datetime.now()
        self.dir: Path | None = None
        self.extra: dict = {}

    def state(self, since: int = 0) -> dict:
        return {"kind": self.kind, "total": self.total, "done": self.done, "current": self.current,
                "running": self.running, "error": self.error, "dir": str(self.dir) if self.dir else None,
                "items": self.items[since:], "count": len(self.items), "extra": self.extra}


class Automation:
    def __init__(self, explorer, data_dir: Path):
        self.ex = explorer
        self.sessions = Path(data_dir) / "sessions"
        self.job: Job | None = None
        self.sats = load_satellites()
        self.found = None            # FoundLog — the log of locked channels (attached by the server)

    # --- common -------------------------------------------------------------------
    def _start(self, kind: str, total: int, target, *args) -> Job:
        if self.job and self.job.running:
            raise ProtocolError(tr("err.job_running"))
        if not self.ex.connected:
            raise ProtocolError(tr("err.not_connected"))
        job = Job(kind, total)
        job.dir = self.sessions / f"{job.started:%Y%m%d_%H%M%S}_{kind}"
        job.dir.mkdir(parents=True, exist_ok=True)
        self.job = job

        def run():
            try:
                target(job, *args)
            except Exception as e:  # noqa: BLE001 — show any error in the window
                job.error = str(e)
                traceback.print_exc()
                self.ex.log(tr("log.job_failed", kind=kind, error=e))
            finally:
                job.running = False
                job.current = ""
                (job.dir / "result.json").write_text(json.dumps(job.items, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")

        threading.Thread(target=run, daemon=True).start()
        return job

    def stop(self):
        if self.job:
            self.job.cancel = True

    # --- channel scan ---------------------------------------------------------------
    def start_scan(self, picks: list[dict], dwell: float = 2.0, stop_on_lock: bool = False,
                   keep_order: bool = False, kind: str | None = None) -> Job:
        """Go through transponders: tune, wait for lock (*?ST), measure.

        picks: [{"pos": "13E", "freq_mhz": 10992.1, "pol": "V", "sr": 27500}, …]
        dwell — how long to wait for lock on each channel (less if the meter locks earlier).
        stop_on_lock — stop at the first locked channel ("seek", like a radio);
        keep_order — keep the order of picks (seek up/down in frequency).
        kind — job name (scan/seek by default; "pair" is a QPSK↔8PSK lab pair).
        """
        tps = []
        for p in picks:
            sat = self.sats[p["pos"]]
            tp = next((t for t in sat["transponders"] if t["freq_mhz"] == p["freq_mhz"]
                       and t["pol"] == p["pol"] and t["sr"] == p["sr"]), None)
            if tp and tp["supported"]:
                tps.append((p["pos"], tp))
        if not keep_order:
            # Tuner order: first by LNB setting (fewer supply changes), then by frequency.
            tps.sort(key=lambda x: (x[1]["lnb"], x[1]["freq_mhz"]))
        kind = kind or ("seek" if stop_on_lock else "scan")
        return self._start(kind, len(tps), self._scan, tps, dwell, stop_on_lock)

    def _wait_lock(self, seconds: float) -> dict:
        """Wait up to `seconds` for lock; return the last status (earlier when locked)."""
        deadline = time.monotonic() + seconds
        while True:
            st = self.ex.lock_status()
            if st["locked"] or time.monotonic() >= deadline:
                return st
            time.sleep(0.25)

    def _scan(self, job: Job, tps, dwell: float, stop_on_lock: bool = False):
        path = job.dir / "scan.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([tr(k) for k in (
                "col.pos", "col.freq", "col.pol", "col.sr", "col.system", "col.modulation", "col.fec", "col.if",
                "col.level", "col.cn", "col.mer", "col.cber", "col.vber", "col.device_status", "col.st_code",
                "col.lock_s", "col.req_cn", "col.margin", "col.verdict", "col.channels")])
            for pos, tp in tps:
                if job.cancel:
                    break
                job.current = f"{pos} {tp['freq_mhz']} {tp['pol']} {tp['sr']}"
                t0 = time.monotonic()
                self.ex.current = {**self.ex.current, "pos": pos}     # which satellite — for logs and the lab protocol
                tuned = self.ex.tune_satellite(tp["freq_mhz"], tp["lo_mhz"], tp["sr"], tp["fec"], lnb=tp["lnb"],
                                               standard=tp["system"], modulation=tp["modulation"])
                # Wait until the meter locks (*?ST = "MPEG-2 TS …"), but not longer than dwell.
                st = self.ex.lock_status() if tuned["locked"] else self._wait_lock(dwell)
                lock_s = round(time.monotonic() - t0, 1) if st["locked"] else None
                best = None
                # Locked — 3 readings and the best C/N (the first one after retuning can be low); otherwise one.
                for _ in range(3 if st["locked"] else 1):
                    r = self.ex.read(LOCK_QUERIES)
                    cn = r["CN"].get("value")
                    if best is None or (cn is not None and (best["CN"].get("value") is None or cn > best["CN"]["value"])):
                        best = r
                    time.sleep(0.3)
                cn = best["CN"].get("value")
                # Lock comes from the status line (*?ST = "MPEG-2 TS …");
                # MER is the fallback in case the meter does not answer *?ST.
                locked = st["locked"] if st["st"] is not None else mer_says_locked(best["ME"])
                margin = round(cn - tp["required_cn_db"], 1) if (cn is not None and tp["required_cn_db"] is not None) else None
                item = {"pos": pos, "freq_mhz": tp["freq_mhz"], "pol": tp["pol"], "sr": tp["sr"],
                        "system": tp["system"], "modulation": tp["modulation"], "fec": tp["fec"],
                        "if_mhz": tp["if_mhz"], "lnb": tp["lnb"],
                        "level": best["LV"].get("value"), "cn": cn, "mer": best["ME"].get("value"),
                        "cber": best["CB"].get("raw"), "vber": best["VB"].get("raw"),
                        "cber_v": ber_value(best["CB"].get("raw")), "vber_v": ber_value(best["VB"].get("raw")),
                        "required_cn": tp["required_cn_db"], "margin": margin, "locked": locked,
                        "st": st["st"], "device_status": st["text"], "lock_s": lock_s,
                        "status": classify(margin, locked),
                        "channels": [c["name"] for c in tp["channels"][:8]], "video": tp["video"]}
                job.items.append(item)
                if locked and self.found is not None:
                    sat = self.sats.get(pos, {})
                    self.found.record({**item, "sat_name": sat.get("name"), "lo_mhz": tp["lo_mhz"],
                                       "channels": [c["name"] for c in tp["channels"]], "verified": tp.get("verified"),
                                       "source": job.kind})
                w.writerow([pos, tp["freq_mhz"], tp["pol"], tp["sr"], tp["system"], tp["modulation"], tp["fec"],
                            tp["if_mhz"], item["level"], cn, item["mer"], item["cber"], item["vber"],
                            st["text"], st["st"], lock_s, tp["required_cn_db"], margin,
                            tr("status." + item["status"]), ", ".join(item["channels"])])
                f.flush()
                job.done += 1
                if stop_on_lock and locked:
                    job.current = f"{pos} {tp['freq_mhz']} {tp['pol']} {tp['sr']}"
                    break

    # --- decoding threshold (EDGE) ---------------------------------------------------
    def start_edge(self, interval: float = 1.0) -> Job:
        """Continuous readings while the antenna is moved off the optimum step by step.

        Nothing is tuned. Every reading: *?ST status + LV/CN/ME/CB/VB.
        extra.edge — the last locked reading before a loss of lock (EDGE candidate),
        extra.loss — the first unlocked reading after it. The user decides what goes
        into the protocol."""
        return self._start("edge", 0, self._edge, max(0.3, float(interval)))

    def _edge(self, job: Job, interval: float):
        job.extra = {"edge": None, "loss": None, "losses": 0, "min_locked_cn": None, "best_cn": None}
        t0 = time.monotonic()
        run, last_locked, prev = 0, None, None
        with open(job.dir / "edge.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([tr(k) for k in ("col.time", "col.t_s", "col.locked", "col.device_status", "col.st_code",
                                        "col.level", "col.cn", "col.mer", "col.cber", "col.post_ber", "col.run")])
            while not job.cancel:
                st = self.ex.lock_status()
                r = self.ex.read(LOCK_QUERIES)
                v = lambda k: r[k].get("value")  # noqa: E731
                locked = bool(st["locked"])
                run = run + 1 if locked else 0
                item = {"t": round(time.monotonic() - t0, 1), "time": datetime.now().isoformat(timespec="seconds"),
                        "locked": locked, "st": st["st"], "device_status": st["text"],
                        "level": v("LV"), "cn": v("CN"), "mer": v("ME"),
                        "cber": r["CB"].get("raw"), "vber": r["VB"].get("raw"),
                        "cber_v": ber_value(r["CB"].get("raw")), "vber_v": ber_value(r["VB"].get("raw")), "run": run}
                x = job.extra
                if locked:
                    last_locked = item
                    if item["cn"] is not None:
                        x["min_locked_cn"] = item["cn"] if x["min_locked_cn"] is None else min(x["min_locked_cn"], item["cn"])
                        x["best_cn"] = item["cn"] if x["best_cn"] is None else max(x["best_cn"], item["cn"])
                elif prev is not None and prev["locked"] and last_locked is not None:
                    x["edge"], x["loss"] = last_locked, item
                    x["losses"] += 1
                x["last_locked"] = last_locked
                job.items.append(item)
                job.done += 1
                job.current = f"{'lock' if locked else 'no_lock'} C/N {item['cn']}"
                w.writerow([item["time"], item["t"], tr("yes") if locked else tr("no"), st["text"], st["st"],
                            item["level"], item["cn"], item["mer"], item["cber"], item["vber"], run])
                f.flush()
                prev = item
                end = time.monotonic() + interval
                while not job.cancel and time.monotonic() < end:
                    time.sleep(0.05)

    # --- spectrum sweep --------------------------------------------------------------
    def start_sweep(self, center_mhz: float, span_mhz: float, step_mhz: float, lo_mhz: float, lnb: str,
                    settle: float = 1.0) -> Job:   # the level settles 0.77 s after *FR (verified)
        start = max(950.0, center_mhz - lo_mhz - span_mhz / 2)
        stop = min(2150.0, center_mhz - lo_mhz + span_mhz / 2)
        if not step_mhz > 0:
            raise ProtocolError(tr("err.bad_step"))
        n = int((stop - start) / step_mhz) + 1
        if n < 2:
            raise ProtocolError(tr("err.span_too_small"))
        return self._start("sweep", n, self._sweep, start, step_mhz, n, lo_mhz, lnb, settle)

    def _sweep(self, job: Job, start: float, step: float, n: int, lo: float, lnb: str, settle: float):
        # The LNB supply (polarisation/band) is set once, then only *FR changes.
        cmd = f"*LB{LNB_CODES[lnb]}"
        if self.ex._sat_sent.get("LB") != cmd:
            with self.ex.lock:
                self.ex.link.send(cmd)
            self.ex._sat_sent["LB"] = cmd
            self.ex._wait_ready()
        with open(job.dir / "sweep.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([tr("col.if_mhz"), tr("col.rf_mhz"), tr("col.level")])
            for i in range(n):
                if job.cancel:
                    break
                if_mhz = round(start + i * step, 3)
                job.current = f"{if_mhz + lo:.1f} MHz"
                self.ex.set_if(round(if_mhz * 1000), settle)
                r = self.ex.read(["*?LV"])
                lv = r["LV"].get("value")
                job.items.append({"if_mhz": if_mhz, "rf_mhz": round(if_mhz + lo, 3), "level": lv})
                w.writerow([if_mhz, round(if_mhz + lo, 3), lv])
                job.done += 1
        levels = [x["level"] for x in job.items if x["level"] is not None]
        if levels:
            floor = statistics.quantiles(levels, n=10)[0] if len(levels) >= 10 else min(levels)
            (job.dir / "noise.json").write_text(json.dumps({"noise_floor": floor}), encoding="utf-8")
