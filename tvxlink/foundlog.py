"""Log of found channels: everything the meter locked (*?ST = "MPEG-2 TS …").

Written automatically from tuning, seeking, checking the spectrum, scans,
antenna aiming and live measurements. Kept between runs:
  <data>/found/found.jsonl   — every lock event (append-only)
  <data>/found/found.csv     — summary per transponder (rebuilt)

To avoid flooding, an event per transponder is written on the first lock, when
the best C/N improves, and not more often than once a minute while watching.
"""

from __future__ import annotations

import csv
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .i18n import tr

REPEAT_S = 60.0


def tp_key(pos, freq_mhz, pol, sr) -> str:
    return f"{pos or '?'}|{float(freq_mhz):.1f}|{pol}|{int(sr) if sr else '?'}"


def write_csv_copy(write, csv_path: Path, journal: Path):
    """Rewrite a CSV copy of a journal. Excel locks an open CSV on Windows: the record is already
    in the journal, so warn and carry on — the CSV is rewritten on the next record."""
    try:
        write()
    except OSError as e:
        print(tr("err.csv_locked", path=csv_path, error=e, journal=journal), file=sys.stderr)


class FoundLog:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "found"
        self.jsonl = self.dir / "found.jsonl"
        self.csv = self.dir / "found.csv"
        self.items: dict[str, dict] = {}
        self._last_write: dict[str, float] = {}
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        if not self.jsonl.exists():
            return
        for line in self.jsonl.read_text(encoding="utf-8").splitlines():
            try:
                self._merge(json.loads(line))
            except (ValueError, KeyError):
                continue

    def _merge(self, ev: dict) -> tuple[dict, bool, bool]:
        k = ev["key"]
        it = self.items.get(k)
        new = it is None
        if new:
            it = self.items[k] = {"key": k, "first_seen": ev["time"], "times": 0, "best_cn": None, "best_mer": None}
        it["times"] += 1
        it["last_seen"] = ev["time"]
        for f in ("pos", "sat_name", "freq_mhz", "pol", "sr", "system", "modulation", "fec", "lo_mhz", "lnb",
                  "device_status", "st", "level", "cn", "mer", "cber", "vber", "channels", "video", "source", "verified", "caught"):
            if ev.get(f) is not None:
                it[f] = ev[f]
        better = False
        if ev.get("cn") is not None and (it["best_cn"] is None or ev["cn"] > it["best_cn"]):
            it["best_cn"] = ev["cn"]
            better = not new
        if ev.get("mer") is not None and (it["best_mer"] is None or ev["mer"] > it["best_mer"]):
            it["best_mer"] = ev["mer"]
        return it, new, better

    def record(self, ev: dict) -> dict | None:
        """A lock event. ev: pos, freq_mhz, pol, sr, system, modulation, fec, level, cn, mer, cber, vber, …"""
        if not ev.get("freq_mhz") or not ev.get("pol"):
            return None
        ev = dict(ev)
        ev["time"] = datetime.now().isoformat(timespec="seconds")
        ev["key"] = tp_key(ev.get("pos"), ev["freq_mhz"], ev["pol"], ev.get("sr"))
        with self.lock:
            k = ev["key"]
            prev_best = (self.items.get(k) or {}).get("best_cn")
            now = time.monotonic()
            is_new = k not in self.items
            is_better = ev.get("cn") is not None and prev_best is not None and ev["cn"] > prev_best
            due = now - self._last_write.get(k, 0) >= REPEAT_S
            if not (is_new or is_better or due):
                # update 'last seen' in memory only, nothing is written
                it = self.items[k]
                it["last_seen"] = ev["time"]
                return it
            it, _, _ = self._merge(ev)
            self._last_write[k] = now
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            write_csv_copy(self._write_csv, self.csv, self.jsonl)
            return it

    def _write_csv(self):
        rows = sorted(self.items.values(), key=lambda x: (x.get("pos") or "", x.get("freq_mhz") or 0))
        with open(self.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([tr(k) for k in (
                "col.pos", "col.freq", "col.pol", "col.sr", "col.system", "col.modulation", "col.fec", "col.device_status",
                "col.first_seen", "col.last_seen", "col.times", "col.best_cn", "col.best_mer", "col.level", "col.cn",
                "col.mer", "col.cber", "col.vber", "col.source", "col.channels")])
            for it in rows:
                w.writerow([it.get("pos"), it.get("freq_mhz"), it.get("pol"), it.get("sr"), it.get("system"),
                            it.get("modulation"), it.get("fec"), it.get("device_status"), it.get("first_seen"),
                            it.get("last_seen"), it.get("times"), it.get("best_cn"), it.get("best_mer"),
                            it.get("level"), it.get("cn"), it.get("mer"), it.get("cber"), it.get("vber"),
                            it.get("source"), ", ".join(it.get("channels") or [])])

    def list(self) -> list[dict]:
        with self.lock:
            return sorted(self.items.values(), key=lambda x: x.get("last_seen") or "", reverse=True)
