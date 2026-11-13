"""Self-test of a TV EXPLORER II / II+ and of the whole program on a real meter.

    tvxlink-selftest                read-only checks (safe)
    tvxlink-selftest --tune --verified 3 --sat 13E   also tune, lock, sweep, scan
    tvxlink-selftest --port demo    run the test itself against the simulator

Stages:
  0  environment: Python, packages, COM ports, driver, unit tests
  1  link: open the port, listen for XON (the meter sends it once a second)
  2  identification: *?NA / *?VE / *?ID
  3  query catalogue: every known read-only *?.. — ACK/NAK, format, latency
  4  datalogger: file list (read-only; --dl downloads and parses the first one)
  --- only with --tune (the meter retunes and powers the LNB!) ---
  5  tune to a transponder, *?FR == IF; lock from *?ST (6 = MPEG-2 TS DVB-S,
     8 = DVB-S2) checked against MER, unknown codes collected
  6  measurement series (level, C/N, MER, BER) and stability
  7  level settling time after *FR → recommended spectrum dwell
  8  spectrum sweep around the channel with the program's own code (scan.py)
  9  channel scan with the program's own code on this transponder
  10 put the meter back into its previous mode/frequency

Result: <data>/selftest/<time>/report.md (+ report.json, hex log of the exchange).
Firmware or delete commands are never sent.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from .. import __version__
from ..device import Explorer, IDENT_COMMANDS, parse_measure
from ..i18n import tr
from ..paths import data_dir
from ..protocol import ProtocolError, XON
from . import add_lang, apply_lang

ROOT = Path(__file__).resolve().parent.parent.parent

# Queries only (reading). Source: NetUpdate 3/4 and RM-404 (docs/PROTOCOL.md).
# Queries that start a file/memory transfer (*?FL.., *?RD.., *?PP.., *?DIR) are NOT here.
QUERY_CATALOG = ["*?NA", "*?NAM", "*?VE", "*?VER", "*?ID", "*?IDA", "*?IDEXT", "*?SA", "*?ST", "*?SY", "*?FR", "*?LV",
                 "*?CN", "*?ME", "*?CB", "*?VB", "*?SE", "*?VA", "*?FM", "*?T", "*?FI0A", "*?FI14", "*?FI01", "*?FI04"]
QUERY_WHAT = {"*?NA": "model name", "*?NAM": "name (NetUpdate)", "*?VE": "firmware", "*?VER": "version (RANGER)",
              "*?ID": "identifier", "*?IDA": "ID ext.", "*?IDEXT": "ID ext.", "*?SA": "mode/standard (hex)",
              "*?ST": "demodulator status (6 = MPEG-2 TS DVB-S, 8 = DVB-S2)", "*?SY": "analogue system",
              "*?FR": "frequency, kHz", "*?LV": "level ×10", "*?CN": "C/N ×10", "*?ME": "MER ×10", "*?CB": "CBER",
              "*?VB": "VBER/LBER", "*?SE": "SER / status", "*?VA": "V/A (analogue)", "*?FM": "FM deviation", "*?T": "?",
              "*?FI0A": "dataloggers: count", "*?FI14": "spectra: count", "*?FI01": "channel plans (terr.)",
              "*?FI04": "channel plans (sat.)"}
EXPECT = {   # expected reply formats (after the '*' is stripped)
    "FR": r"^FR\s*\d{5,8}$",
    "LV": r"^LV(\s*[<>]?\s*[+-]\s*\d{1,5}|\s*!.*)$",
    "CN": r"^CN(\s*[<>]?\s*[+-]\s*\d{1,5}|\s*!.*)$",
    "ME": r"^ME(\s*[<>]?\s*[+-]\s*\d{1,5}|\s*!.*)$",
    "SA": r"^SA[0-9A-F]{1,2}$", "ST": r"^ST[0-9A-F]{1,2}$",
    "NA": r"^NA.+", "VE": r"^VE.+", "ID": r"^ID.*",
}
MEAS = ["*?FR", "*?LV", "*?CN", "*?ME", "*?CB", "*?VB"]
STAGE_NO = {"env": 0, "link": 1, "ident": 2, "queries": 3, "dl": 4, "tune": 5, "lock": 5, "series": 6, "settle": 7,
            "sweep": 8, "scan": 9, "restore": 10}
MARK = {"PASS": "✔", "WARN": "!", "FAIL": "✘", "INFO": "·", "SKIP": "–"}


def stage_name(code: str) -> str:
    return f"{STAGE_NO.get(code, '')} {tr('cli.st.stage.' + code)}".strip()


class Report:
    def __init__(self, folder: Path):
        self.dir = folder
        self.checks: list[dict] = []
        self.data: dict = {}
        self.notes: list[str] = []
        self.recs: list[str] = []

    def add(self, stage: str, name: str, status: str, detail: str = "", **data):
        self.checks.append({"stage": stage, "name": name, "status": status, "detail": detail, **data})
        print(f"  [{MARK[status]} {status}] {stage_name(stage)} · {name}" + (f" — {detail}" if detail else ""), flush=True)

    def status_of(self, stage: str) -> set:
        return {c["status"] for c in self.checks if c["stage"] == stage}

    def verdict(self) -> str:
        st = self.status_of
        if "FAIL" in st("link") or "FAIL" in st("ident") or not st("link"):
            return "no"
        if "FAIL" in st("queries"):
            return "partial_read"
        if any("FAIL" in st(s) for s in ("tune", "lock", "sweep", "scan")):
            return "partial_tune"
        if any(c["status"] == "WARN" for c in self.checks):
            return "go_warn"
        if not st("tune") or st("tune") == {"SKIP"}:
            return "go_readonly"
        return "go"

    def write(self, args) -> str:
        v = self.verdict()
        js = {"verdict": v, "verdict_text": tr("cli.st.v." + v), "why": tr("cli.st.why." + v), "version": __version__,
              "args": vars(args), "checks": self.checks, "data": self.data, "notes": self.notes,
              "recommendations": self.recs, "time": datetime.now().isoformat(timespec="seconds")}
        (self.dir / "report.json").write_text(json.dumps(js, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        L = [f"# {tr('cli.st.title')} — {datetime.now():%Y-%m-%d %H:%M}", "",
             f"**{tr('cli.st.verdict')}: {tr('cli.st.v.' + v)}** — {tr('cli.st.why.' + v)}", "",
             tr("cli.st.header", port=args.port, tune=tr("yes") if args.tune else tr("no"), py=platform.python_version(),
                os=platform.platform(), version=__version__), "",
             f"| {tr('cli.st.col.stage')} | {tr('cli.st.col.check')} | {tr('cli.st.col.result')} | {tr('cli.st.col.detail')} |",
             "|---|---|---|---|"]
        for c in self.checks:
            L.append(f"| {stage_name(c['stage'])} | {c['name']} | {c['status']} | {str(c['detail']).replace('|', '/')} |")
        if self.recs:
            L += ["", f"## {tr('cli.st.recs')}", ""] + [f"- {r}" for r in self.recs]
        if self.notes:
            L += ["", f"## {tr('cli.st.notes')}", ""] + [f"- {n}" for n in self.notes]
        q = self.data.get("queries")
        if q:
            L += ["", f"## {tr('cli.st.replies')}", "", "| Query | What | ACK | Reply | hex | ms |", "|---|---|---|---|---|---|"]
            for r in q:
                L.append(f"| `{r['cmd']}` | {r['what']} | {r.get('ack', '—')} | `{r.get('reply', r.get('error', ''))}` | "
                         f"`{r.get('raw_hex', '')[:60]}` | {r.get('ms', '')} |")
        ms = self.data.get("measure_series")
        if ms:
            L += ["", f"## {tr('cli.st.series')}", "", "| # | FR | LV | C/N | MER | CBER | VBER |", "|---|---|---|---|---|---|---|"]
            for i, m in enumerate(ms):
                L.append(f"| {i} | " + " | ".join(f"`{m.get(k, {}).get('raw')}`" for k in ("FR", "LV", "CN", "ME", "CB", "VB")) + " |")
        L += ["", tr("cli.st.files")]
        (self.dir / "report.md").write_text("\n".join(L), encoding="utf-8")
        return v


def resync(ex: Explorer, ms: int = 150):
    """Drop leftovers after a reply (when the meter sent more than expected).

    XON ("ready") is kept: otherwise the next query would wait up to 1 s for the next XON."""
    seen = bytearray(ex.link._buf)
    t0 = time.monotonic()
    while time.monotonic() - t0 < ms / 1000:
        seen += ex.port.read(timeout_ms=50)
    ex.link._buf.clear()
    if seen and seen.rstrip(b"\r\n").endswith(bytes([XON])):
        ex.link._buf.append(XON)


def send_t(ex: Explorer, cmd: str) -> dict:
    t0 = time.monotonic()
    try:
        r = ex.link.send(cmd)
        return {"cmd": cmd, "ack": r.ack, "reply": r.text, "raw_hex": r.raw.hex(" "), "ms": round((time.monotonic() - t0) * 1000)}
    except ProtocolError as e:
        return {"cmd": cmd, "error": str(e), "ms": round((time.monotonic() - t0) * 1000)}


# --- stages -------------------------------------------------------------------------------

def stage_env(R: Report, args):
    S = "env"
    R.add(S, "Python", "PASS" if sys.version_info >= (3, 9) else "FAIL", platform.python_version())
    for mod in ("serial", "usb", "libusb_package"):
        try:
            m = __import__(mod)
            R.add(S, f"{tr('cli.st.package')} {mod}", "PASS", getattr(m, "__version__", "ok"))
        except ImportError:
            need = mod == "serial" or (mod == "libusb_package" and sys.platform == "win32")
            R.add(S, f"{tr('cli.st.package')} {mod}", "FAIL" if need else "INFO", tr("cli.st.not_installed"))
    try:
        from ..serialport import list_ports
        ports = list_ports()
        R.data["ports"] = ports
        pm = [p for p in ports if p["promax"]]
        if args.port == "demo":
            R.add(S, tr("cli.st.com"), "SKIP", tr("cli.st.demo"))
        elif pm:
            R.add(S, tr("cli.st.com"), "PASS", ", ".join(f"{p['device']} ({p['description']}, {p['hwid']})" for p in pm))
        else:
            R.add(S, tr("cli.st.com"), "WARN" if args.port not in ("auto", "usb") else "FAIL",
                  tr("cli.st.com_missing", ports=", ".join(p["device"] for p in ports) or "—"))
            R.recs.append(tr("cli.st.rec.driver"))
    except Exception as e:  # noqa: BLE001
        R.add(S, tr("cli.st.com"), "FAIL", str(e))
    if not args.skip_unittests and (ROOT / "tests").is_dir():
        cmd = [sys.executable, "-m", "unittest", "tests.test_protocol", "tests.test_datalogger", "tests.test_tune",
               "tests.test_scan", "tests.test_serialport"]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
        tail = (p.stderr or p.stdout).strip().splitlines()[-1:] or [""]
        R.add(S, tr("cli.st.unittests"), "PASS" if p.returncode == 0 else "FAIL", tail[0])
        if p.returncode:
            (R.dir / "unittest.txt").write_text(p.stdout + p.stderr, encoding="utf-8")


def stage_link(R: Report, args, ex: Explorer):
    S = "link"
    t0 = time.monotonic()
    data = bytearray()
    while time.monotonic() - t0 < args.listen:
        data += ex.port.read(timeout_ms=100)
    xon = data.count(bytes([XON]))
    rate = xon / args.listen
    R.data["listen"] = {"seconds": args.listen, "bytes": len(data), "xon": xon, "hex": data[:200].hex(" ")}
    if xon == 0:
        R.add(S, tr("cli.st.xon"), "FAIL", tr("cli.st.no_xon", s=args.listen, n=len(data)))
        R.recs.append(tr("cli.st.rec.no_xon"))
    else:
        R.add(S, tr("cli.st.xon"), "PASS" if 0.5 <= rate <= 3 else "WARN", tr("cli.st.xon_rate", n=xon, s=args.listen, r=f"{rate:.2f}"))


def stage_ident(R: Report, ex: Explorer):
    S = "ident"
    for key, cmd in IDENT_COMMANDS.items():
        v = ex.info.get(key)
        # Name and version confirm the link. TV EXPLORER II+ 1.30 does not know *?ID (NAK, verified),
        # which says nothing about the link, so only WARN.
        R.add(S, cmd, "PASS" if v else ("WARN" if cmd == "*?ID" else "FAIL"),
              repr(v) if v or cmd != "*?ID" else tr("cli.st.id_nak"))
    R.data["info"] = ex.info
    name = (ex.info.get("model") or "").upper()
    if name and "EXPLORER" not in name:
        R.add(S, tr("cli.st.model"), "WARN", tr("cli.st.model_other", name=name))


def stage_queries(R: Report, ex: Explorer):
    S = "queries"
    res = []
    for cmd in QUERY_CATALOG:
        r = send_t(ex, cmd)
        r["what"] = QUERY_WHAT[cmd]
        code = cmd[2:4]
        if r.get("ack") and r.get("reply") is not None and code in EXPECT:
            r["format_ok"] = bool(re.match(EXPECT[code], r["reply"].strip()))
        if r.get("ack") and code in ("FR", "LV", "CN", "ME", "CB", "VB"):
            r["parsed"] = parse_measure(cmd, r["reply"])
        res.append(r)
        resync(ex)
    R.data["queries"] = res
    by = {r["cmd"]: r for r in res}
    for cmd in ("*?FR", "*?LV", "*?CN", "*?SA", "*?ST"):
        r = by[cmd]
        if "error" in r:
            R.add(S, cmd, "FAIL", r["error"])
        elif not r["ack"]:
            R.add(S, cmd, "FAIL", tr("cli.st.nak"))
            if cmd in ("*?LV", "*?CN"):
                R.recs.append(tr("cli.st.rec.tv_mode"))
        elif r.get("format_ok") is False:
            R.add(S, cmd, "FAIL", tr("cli.st.bad_format", reply=repr(r["reply"]), fmt=EXPECT[cmd[2:4]]))
            R.recs.append(tr("cli.st.rec.parse", cmd=cmd, reply=repr(r["reply"])))
        else:
            R.add(S, cmd, "PASS", f"{r['reply']!r} → {r.get('parsed', '')} ({r['ms']} ms)")
    for cmd in ("*?ME", "*?CB", "*?VB"):
        r = by[cmd]
        if r.get("ack"):
            # BER format checked against the screen of a TV EXPLORER II+ 1.30: 'CB>+10E-2' = ">1.0E-1".
            ok = r.get("format_ok") if cmd == "*?ME" else (r.get("parsed") or {}).get("value") is not None
            R.add(S, cmd, "PASS" if ok else "WARN", f"{r['reply']!r} → {r.get('parsed')}")
            if cmd != "*?ME" and not ok:
                R.recs.append(tr("cli.st.rec.ber", cmd=cmd, reply=repr(r["reply"])))
        else:
            R.add(S, cmd, "WARN", r.get("error") or "NAK")
    other = [r for r in res if r["cmd"] not in ("*?FR", "*?LV", "*?CN", "*?SA", "*?ST", "*?ME", "*?CB", "*?VB")]
    R.add(S, tr("cli.st.other"), "INFO", "ACK: " + (", ".join(r["cmd"] for r in other if r.get("ack")) or "—") + "; NAK: " +
          (", ".join(r["cmd"] for r in other if r.get("ack") is False) or "—"))
    ms = [r["ms"] for r in res if "ms" in r and r.get("ack")]
    if ms:
        R.add(S, tr("cli.st.latency"), "PASS" if statistics.median(ms) < 400 else "WARN",
              tr("cli.st.latency_v", med=f"{statistics.median(ms):.0f}", max=max(ms)))


def stage_dl(R: Report, args, ex: Explorer):
    S = "dl"
    try:
        files = ex.list_datalogger()
        R.data["datalogger"] = files
        R.add(S, "*?FI0A", "PASS", tr("cli.st.dl_files", n=len(files), names=", ".join(f["name"] for f in files[:8])))
        if args.dl and files:
            from ..dlfile import parse_dl
            path = ex.download_datalogger(files[0]["name"], R.dir)
            try:
                d = parse_dl(path.read_bytes())
                R.add(S, tr("cli.st.dl_get", name=files[0]["name"]), "PASS",
                      tr("cli.st.dl_parsed", b=path.stat().st_size, t=len(d["tests"]), c=len(d["plan"]["channels"])))
            except Exception as e:  # noqa: BLE001
                R.add(S, tr("cli.st.dl_get", name=files[0]["name"]), "WARN", tr("cli.st.dl_unparsed", b=path.stat().st_size, error=e))
                R.recs.append(tr("cli.st.rec.dl", path=path))
    except Exception as e:  # noqa: BLE001
        R.add(S, "*?FI0A", "WARN", str(e))
    resync(ex)


def pick_target(args) -> dict:
    if args.verified is not None:
        from ..scan import load_satellites, load_verified
        c = load_verified()[args.verified]
        sat = load_satellites()[c["pos"]]
        tp = next(t for t in sat["transponders"] if t["pol"] == c["pol"] and t["sr"] == c["sr"]
                  and abs(t["freq_mhz"] - c["freq_mhz"]) < 0.6)
        return {"rf": tp["freq_mhz"], "lo": tp["lo_mhz"], "sr": tp["sr"], "fec": tp["fec"], "lnb": tp["lnb"],
                "std": tp["system"], "mod": tp["modulation"], "pos": c["pos"], "pol": tp["pol"], "name": c["name"]}
    if args.rf is None:
        raise SystemExit(tr("cli.st.need_target"))
    return {"rf": args.rf, "lo": args.lo, "sr": args.sr, "fec": args.fec, "lnb": args.lnb, "std": args.std,
            "mod": args.mod, "pos": args.sat, "pol": args.pol, "name": "manual"}


def stage_tune(R: Report, args, ex: Explorer, tg: dict) -> dict:
    S = "tune"
    if hasattr(ex.port, "point") and tg.get("pos"):
        ex.port.point(tg["pos"])          # demo: point the simulator "antenna" at the target satellite
    before = {k: ex.read([c])[c[2:4]].get("raw") for k, c in (("SA", "*?SA"), ("FR", "*?FR"))}
    R.data["before"] = before
    t0 = time.monotonic()
    r = ex.tune_satellite(tg["rf"], tg["lo"], tg["sr"], tg["fec"], lnb=tg["lnb"], standard=tg["std"],
                          modulation=tg["mod"], force=True)
    total = time.monotonic() - t0
    R.data["tune"] = {"target": tg, "result": r, "total_s": round(total, 2)}
    nak = [c for c in r["sent"] if "(NAK)" in c]
    R.add(S, tr("cli.st.tune_cmds"), "FAIL" if nak else "PASS",
          ("NAK: " + ", ".join(nak) + "; ") * bool(nak) + " ".join(r["sent"]) + f" — {total:.1f} s" +
          (f"; {tr('ui.tune.skipped')}: {' '.join(r['skipped'])}" if r.get("skipped") else ""))
    time.sleep(args.dwell)
    fr = ex.read(["*?FR"])["FR"]
    want = round((tg["rf"] - tg["lo"]) * 1000)
    got = round((fr.get("value") or 0) * 1000)
    R.add(S, tr("cli.st.fr_if"), "PASS" if abs(got - want) <= 1000 else "FAIL", tr("cli.st.fr_v", want=want, got=repr(fr.get("raw"))))
    sa = ex.read(["*?SA"])["SA"].get("raw")
    R.add(S, tr("cli.st.sa_mode"), "PASS" if sa and sa.upper().endswith({"DVB-S": "3", "DVB-S2": "7"}[tg["std"]]) else "WARN", repr(sa))
    stage_lock(R, args, ex, tg)
    return before


def stage_lock(R: Report, args, ex: Explorer, tg: dict):
    """Lock status *?ST (the line at the bottom of the meter's screen) and how it agrees with MER."""
    S = "lock"
    seen = []
    for _ in range(max(4, args.samples // 2)):
        st = ex.lock_status()
        me = ex.read(["*?ME"])["ME"].get("value")
        seen.append({"sa": st["sa"], "st": st["st"], "locked": st["locked"], "text": st["text"], "mer": me})
        time.sleep(0.5)
    R.data["lock_samples"] = seen
    codes = sorted({x["st"] for x in seen if x["st"] is not None})
    locked = [x for x in seen if x["locked"]]
    with_mer = [x for x in seen if x["mer"] is not None]
    if not codes:
        R.add(S, tr("cli.st.st_replies"), "FAIL", tr("cli.st.st_none"))
        R.recs.append(tr("cli.st.rec.st_none"))
        return
    R.add(S, tr("cli.st.st_replies"), "PASS", tr("cli.st.st_codes", codes=codes, sa=seen[-1]["sa"]))
    if locked:
        R.add(S, tr("cli.st.locked"), "PASS", tr("cli.st.locked_v", n=len(locked), total=len(seen), text=locked[-1]["text"]))
    else:
        R.add(S, tr("cli.st.locked"), "WARN", tr("cli.st.never", codes=codes, sa=seen[-1]["sa"], code=ex.lock_status()["lock_code"]))
    agree = sum(1 for x in seen if bool(x["locked"]) == (x["mer"] is not None))
    R.add(S, tr("cli.st.agree"), "PASS" if agree == len(seen) else "WARN",
          tr("cli.st.agree_v", n=agree, total=len(seen), mer=len(with_mer)))
    if agree != len(seen):
        R.recs.append(tr("cli.st.rec.agree"))
    unknown = [c for c in codes if c != ex.lock_status()["lock_code"]]
    if unknown:
        R.recs.append(tr("cli.st.rec.unknown", codes=unknown, sa=seen[-1]["sa"]))


def stage_series(R: Report, args, ex: Explorer, tg: dict):
    S = "series"
    rows = []
    for _ in range(args.samples):
        rows.append(ex.read(MEAS))
        time.sleep(0.5)
    R.data["measure_series"] = rows
    lv = [r["LV"].get("value") for r in rows if r["LV"].get("value") is not None]
    cn = [r["CN"].get("value") for r in rows if r["CN"].get("value") is not None]
    me = [r["ME"].get("value") for r in rows if r["ME"].get("value") is not None]
    R.add(S, tr("ui.col.level"), "PASS" if lv else "FAIL", f"{min(lv):.1f}…{max(lv):.1f} dBµV" if lv else tr("cli.st.no_values"))
    R.add(S, "C/N", "PASS" if cn else "WARN", f"{statistics.mean(cn):.1f} dB (σ {statistics.pstdev(cn):.2f})" if cn else tr("cli.st.no_values"))
    locked = len(me) >= len(rows) // 2 and me
    R.add(S, tr("cli.st.mer_lock"), "PASS" if locked else "WARN", f"MER {statistics.mean(me):.1f} dB" if me else tr("cli.st.no_mer"))
    if not locked:
        R.recs.append(tr("cli.st.rec.no_lock", rf=tg["rf"], pol=tg.get("pol") or "", pos=tg.get("pos") or "?"))
    R.add(S, tr("cli.st.ber_raw"), "INFO", f"CB {[r['CB'].get('raw') for r in rows][:3]}, VB {[r['VB'].get('raw') for r in rows][:3]}")


def stage_settle(R: Report, args, ex: Explorer, tg: dict) -> float:
    S = "settle"
    if_khz = round((tg["rf"] - tg["lo"]) * 1000)
    ex.set_if(if_khz + 40_000, settle=0.5)
    ex.read(["*?LV"])
    ex.set_if(if_khz, settle=0)
    t0 = time.monotonic()
    pts = []
    while time.monotonic() - t0 < 2.5:
        v = ex.read(["*?LV"])["LV"].get("value")
        pts.append((round(time.monotonic() - t0, 3), v))
    vals = [v for _, v in pts if v is not None]
    if not vals:
        R.add(S, tr("cli.st.settle"), "WARN", tr("cli.st.no_values"))
        return 1.0
    final = statistics.median(vals[-5:])
    t_ok = next((t for t, v in pts if v is not None and abs(v - final) <= 0.5), pts[-1][0])
    rec = round(max(0.1, t_ok * 1.3), 2)
    R.data["settle"] = {"points": pts, "final": final, "t_settle": t_ok, "recommended": rec}
    R.add(S, tr("cli.st.settle"), "PASS" if t_ok < 1.5 else "WARN", tr("cli.st.settle_v", t=f"{t_ok:.2f}", rec=rec))
    return rec


def stage_sweep(R: Report, args, ex: Explorer, tg: dict, settle: float):
    S = "sweep"
    from ..scan import Automation
    auto = Automation(ex, R.dir)
    job = auto.start_sweep(tg["rf"], 120, 4, tg["lo"], tg["lnb"], settle=settle)
    while job.running:
        time.sleep(0.2)
    lv = [i["level"] for i in job.items if i["level"] is not None]
    name = tr("cli.st.sweep")
    if job.error or not lv:
        R.add(S, name, "FAIL", job.error or tr("cli.st.no_values"))
        return
    span = max(lv) - min(lv)
    R.data["sweep"] = job.items
    R.add(S, name, "PASS" if span >= 3 else "WARN", tr("cli.st.sweep_v", n=len(lv), lo=f"{min(lv):.1f}", hi=f"{max(lv):.1f}", span=f"{span:.1f}"))
    if span < 3:
        R.recs.append(tr("cli.st.rec.flat"))


def stage_scan(R: Report, args, ex: Explorer, tg: dict):
    S = "scan"
    if not tg.get("pos"):
        R.add(S, tr("cli.st.scan"), "SKIP", tr("cli.st.no_sat"))
        return
    from ..scan import Automation
    auto = Automation(ex, R.dir)
    job = auto.start_scan([{"pos": tg["pos"], "freq_mhz": tg["rf"], "pol": tg["pol"], "sr": tg["sr"]}], dwell=args.dwell)
    while job.running:
        time.sleep(0.2)
    if job.error or not job.items:
        R.add(S, tr("cli.st.scan"), "FAIL", job.error or tr("cli.st.tp_missing"))
        return
    it = job.items[0]
    R.add(S, tr("cli.st.scan"), "PASS" if it["locked"] else "WARN",
          f"LV {it['level']}, C/N {it['cn']}, MER {it['mer']}, {tr('ui.col.margin')} {it['margin']} dB — {tr('status.' + it['status'])}")


def stage_restore(R: Report, ex: Explorer, before: dict):
    S = "restore"
    try:
        sa = before.get("SA") or ""
        fr = before.get("FR") or ""
        sent = []
        if re.match(r"^SA[0-9A-F]{1,2}$", sa.strip()):
            ex.link.send(f"*SA{int(sa.strip()[2:], 16)}")
            sent.append(f"*SA{int(sa.strip()[2:], 16)}")
            ex._wait_ready()
        m = re.match(r"^FR\s*(\d{5,8})$", fr.strip())
        if m:
            ex.link.send(f"*FR{int(m.group(1)):07d}")
            sent.append(f"*FR{int(m.group(1)):07d}")
        R.add(S, tr("cli.st.restore"), "PASS" if sent else "INFO", " ".join(sent) or "—")
        R.notes.append(tr("cli.st.note.lnb"))
    except Exception as e:  # noqa: BLE001
        R.add(S, tr("cli.st.restore"), "WARN", str(e))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tvxlink-selftest", description="TVXLink self-test of a TV EXPLORER II / II+ and of the program")
    ap.add_argument("--port", default="auto", help="auto | COM3 | /dev/ttyUSB0 | usb | demo")
    ap.add_argument("--listen", type=float, default=3.0, help="seconds to listen for XON")
    ap.add_argument("--dl", action="store_true", help="download and parse the first datalogger")
    ap.add_argument("--tune", action="store_true", help="stages 5–10: the meter retunes and powers the LNB")
    ap.add_argument("--verified", "--caught", type=int, dest="verified", help="target from satellites/verified.json by index")
    ap.add_argument("--rf", type=float)
    ap.add_argument("--lo", type=float, default=9750)
    ap.add_argument("--sr", type=int)
    ap.add_argument("--fec")
    ap.add_argument("--lnb", default="13V")
    ap.add_argument("--std", default="DVB-S")
    ap.add_argument("--mod", default="QPSK")
    ap.add_argument("--sat", help="orbital position for the scan stage, e.g. 13E")
    ap.add_argument("--pol", default="V")
    ap.add_argument("--samples", type=int, default=10)
    ap.add_argument("--dwell", type=float, default=2.0)
    ap.add_argument("--skip-unittests", action="store_true")
    add_lang(ap)
    args = ap.parse_args(argv)
    apply_lang(args)

    folder = data_dir() / "selftest" / f"{datetime.now():%Y%m%d_%H%M%S}{'_demo' if args.port == 'demo' else ''}"
    folder.mkdir(parents=True, exist_ok=True)
    R = Report(folder)
    print(f"{tr('cli.st.title')} → {folder}")
    ex = Explorer(folder)
    before = None
    try:
        stage_env(R, args)
        try:
            port = None if args.port == "auto" else args.port
            ex.connect(demo=(port == "demo"), port=None if port == "demo" else port)
            R.add("link", tr("cli.st.open"), "PASS", ex.log_lines[0].split("  ", 1)[-1] if ex.log_lines else "")
        except Exception as e:  # noqa: BLE001
            R.add("link", tr("cli.st.open"), "FAIL", f"{type(e).__name__}: {e}")
        if ex.connected:
            stage_link(R, args, ex)
            stage_ident(R, ex)
            stage_queries(R, ex)
            stage_dl(R, args, ex)
            if args.tune:
                tg = pick_target(args)
                R.notes.append(tr("cli.st.note.target", tg=tg))
                before = stage_tune(R, args, ex, tg)
                stage_series(R, args, ex, tg)
                settle = stage_settle(R, args, ex, tg)
                stage_sweep(R, args, ex, tg, settle)
                stage_scan(R, args, ex, tg)
            else:
                for s in ("tune", "series", "settle", "sweep", "scan"):
                    R.add(s, "—", "SKIP", tr("cli.st.skip_tune"))
    except Exception as e:  # noqa: BLE001
        R.add("error", type(e).__name__, "FAIL", str(e))
        (folder / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        if before is not None and ex.connected:
            stage_restore(R, ex, before)
        if ex.connected:
            ex.disconnect()
    v = R.write(args)
    print(f"\n{tr('cli.st.verdict').upper()}: {tr('cli.st.v.' + v)} — {tr('cli.st.why.' + v)}\n{tr('cli.st.report')}: {folder / 'report.md'}")
    return 0 if v.startswith("go") else 1


if __name__ == "__main__":
    sys.exit(main())
