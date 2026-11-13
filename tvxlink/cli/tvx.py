"""tvx — direct console access to a TV EXPLORER II / II+ (for people and AI agents).

Uses the same code as the window (tvxlink.device / protocol), so whatever works
here works there too. Every call opens the port, does its job and closes it; the
exchange log (hex of every byte) goes to <data>/logs/session_*.log.

  tvx ports                     COM ports, ★ = the meter
  tvx ident                     *?NA / *?VE / *?ID
  tvx status -n 10              is the channel locked (*?ST: "MPEG-2 TS DVB-S" etc.)
  tvx q LV CN ME                queries *?LV *?CN *?ME: reply, ACK/NAK, time, parsed value
  tvx listen 3                  raw bytes for 3 s (the meter sends XON once a second)
  tvx tune 10992.1 --sr 27500 --fec 2/3 --lnb 13V                    13E, Camera dei Deputati (DVB-S)
  tvx tune 11670.75 --sr 22000 --fec 2/3 --std DVB-S2 --mod 8PSK --lnb 18V   19.2E, 8PSK
  tvx measure -n 5 -p 1         5 readings of level/C/N/MER/BER, one a second
  tvx goto 11500 --lnb 13V      frequency only (like the mouse wheel in the window)
  tvx sweep 10700 11700 4 --lnb 13V
  tvx set "*FR1721000"          one tuning command (safe ones only)
  tvx dl list | dl get NAME     datalogger

Common options: --port auto|COM3|usb|demo (demo = simulator, no meter needed),
                --json (machine-readable output for AI agents), --lang uk|en.
Firmware and delete commands (*BO, *3141&, *SB, *FD, …) are never sent.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from ..device import Explorer, IDENT_COMMANDS, LNB_CODES, is_dangerous, parse_measure
from ..i18n import tr
from ..paths import data_dir
from ..protocol import ProtocolError, XON
from . import add_lang, apply_lang

MEASURE = ["*?FR", "*?LV", "*?CN", "*?ME", "*?CB", "*?VB"]
# Setting commands this tool may send (reception tuning, never memory or firmware).
SAFE_SET_PREFIXES = ("*SA", "*LB", "*CR0", "*RA", "*CO0", "*SI", "*FR", "*SY", "*GI", "*MO", "*BW")


def out(args, data, text=None):
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        print(text if text is not None else json.dumps(data, ensure_ascii=False, indent=1))


def open_explorer(args) -> Explorer:
    ex = Explorer(data_dir() / "logs")
    port = None if args.port in (None, "", "auto") else args.port
    ex.connect(demo=(port == "demo"), port=None if port == "demo" else port)
    if args.verbose:
        print("\n".join(ex.log_lines), file=sys.stderr)
    return ex


def timed_send(ex: Explorer, cmd: str) -> dict:
    t0 = time.monotonic()
    try:
        with ex.lock:
            r = ex.link.send(cmd)
        res = {"cmd": cmd, "ack": r.ack, "reply": r.text, "raw_hex": r.raw.hex(" "),
               "ms": round((time.monotonic() - t0) * 1000)}
        if r.ack and r.text and cmd.startswith("*?") and cmd[2:4] in ("FR", "LV", "CN", "ME", "CB", "VB"):
            res["parsed"] = parse_measure(cmd, r.text)
        return res
    except ProtocolError as e:
        return {"cmd": cmd, "error": str(e), "ms": round((time.monotonic() - t0) * 1000)}


def fmt_res(r: dict) -> str:
    if "error" in r:
        return f"{r['cmd']:<14} {tr('cli.error')} {r['error']}" + (f"  ({r['ms']} ms)" if "ms" in r else "")
    p = r.get("parsed")
    return f"{r['cmd']:<14} {'ACK' if r['ack'] else 'NAK'}  {r['reply']!r}{f'  → {p}' if p else ''}  ({r['ms']} ms)"


# --- commands ---------------------------------------------------------------------------

def c_ports(args):
    try:
        from ..serialport import list_ports
        ports = list_ports()
    except ImportError:
        ports = []
    from ..tusb3410 import list_usb_devices
    try:
        usb = [{"vid": f"{v:04x}", "pid": f"{p:04x}", "name": n} for v, p, n in list_usb_devices()]
    except Exception as e:  # noqa: BLE001
        usb = [{"error": str(e)}]
    text = "\n".join(f"{'★' if p['promax'] else ' '} {p['device']:<10} {p['description']}  [{p['hwid']}]" for p in ports)
    text = (text or tr("cli.tvx.no_com")) + "\nUSB (libusb): " + (", ".join(
        f"{u.get('vid')}:{u.get('pid')} {u.get('name', u.get('error', ''))}" for u in usb) or "—")
    out(args, {"ports": ports, "usb": usb}, text)


def c_ident(args):
    ex = open_explorer(args)
    try:
        res = [timed_send(ex, c) for c in IDENT_COMMANDS.values()]
        out(args, {"info": ex.info, "results": res}, "\n".join(map(fmt_res, res)))
    finally:
        ex.disconnect()


def c_query(args):
    ex = open_explorer(args)
    try:
        res = []
        for c in args.cmds:
            c = c.strip().upper()
            if c.isalnum():                      # short form: LV → *?LV
                c = "*?" + c
            c = c if c.startswith("*") else "*" + c
            if not c.startswith("*?"):
                res.append({"cmd": c, "error": tr("cli.tvx.not_query")})
                continue
            if c.startswith(("*?FL", "*?RD", "*?PP", "*?DIR", "*?FILE")):
                res.append({"cmd": c, "error": tr("cli.tvx.file_query")})
                continue
            res.append(timed_send(ex, c))
            time.sleep(args.gap)
        out(args, {"results": res}, "\n".join(map(fmt_res, res)))
    finally:
        ex.disconnect()


def c_set(args):
    c = args.cmd if args.cmd.startswith("*") else "*" + args.cmd
    if is_dangerous(c) or not c.startswith(SAFE_SET_PREFIXES):
        out(args, {"error": tr("cli.tvx.unsafe", cmd=c, allowed=" ".join(SAFE_SET_PREFIXES))})
        return 2
    ex = open_explorer(args)
    try:
        r = timed_send(ex, c)
        if args.wait:
            ex._settle(args.wait)
        r["status"] = ex.lock_status()
        out(args, r, fmt_res(r) + "\n" + tr("cli.tvx.status", text=r["status"]["text"], st=r["status"]["st"]))
    finally:
        ex.disconnect()


def c_status(args):
    ex = open_explorer(args)
    try:
        rows = []
        for i in range(args.n):
            st = ex.lock_status()
            st["mer"] = ex.read(["*?ME"])["ME"].get("value")
            rows.append(st)
            if not args.json:
                print(f"{'✔' if st['locked'] else '✘'} {st['text']:<28} *?ST={st['st']}  *?SA={st['sa']} ({st['mode']})  MER={st['mer']}")
            if i < args.n - 1:
                time.sleep(args.period)
        if args.json:
            out(args, {"rows": rows})
    finally:
        ex.disconnect()


def c_listen(args):
    ex = open_explorer(args)
    try:
        t0 = time.monotonic()
        data = bytearray()
        events = []
        while time.monotonic() - t0 < args.seconds:
            chunk = ex.port.read(timeout_ms=100)
            if chunk:
                events.append({"t": round(time.monotonic() - t0, 3), "hex": chunk.hex(" ")})
                data += chunk
        xon = data.count(bytes([XON]))
        rate = xon / args.seconds
        res = {"seconds": args.seconds, "bytes": len(data), "xon_count": xon, "xon_per_s": round(rate, 2),
               "other_bytes": len(data) - xon, "events": events[:200]}
        verdict = tr("cli.tvx.xon_ok") if 0.5 <= rate <= 3 else tr("cli.tvx.xon_none") if xon == 0 else tr("cli.tvx.xon_odd")
        out(args, res, tr("cli.tvx.listen", n=len(data), s=args.seconds, x=xon, r=f"{rate:.2f}", v=verdict) + "\n" +
            "\n".join(f"  {e['t']:>7} s  {e['hex']}" for e in events[:40]))
    finally:
        ex.disconnect()


def c_tune(args):
    ex = open_explorer(args)
    try:
        t0 = time.monotonic()
        r = ex.tune_satellite(args.rf, args.lo, args.sr, args.fec, lnb=args.lnb, standard=args.std,
                              modulation=args.mod, inversion=args.inv)
        r["seconds"] = round(time.monotonic() - t0, 2)
        time.sleep(args.dwell)
        r["after"] = ex.read(MEASURE)
        out(args, r, tr("cli.tvx.tuned", sent=" ".join(r["sent"]) or "—", ifm=r["if_mhz"], s=r["seconds"]) + "\n" +
            tr("cli.tvx.status", text=r["status"], st=r["st"]) +
            (f"; {tr('ui.tune.skipped')}: {' '.join(r['skipped'])}" if r.get("skipped") else "") + "\n" +
            "\n".join(f"  {k}: {v.get('raw')!r} → {v.get('value')} {v.get('note') or ''}" for k, v in r["after"].items()))
    finally:
        ex.disconnect()


def c_goto(args):
    ex = open_explorer(args)
    try:
        r = ex.goto_frequency(args.rf, args.lo, args.lnb)
        time.sleep(args.dwell)
        r["after"] = ex.read(["*?FR", "*?LV", "*?CN", "*?ME"])
        out(args, r)
    finally:
        ex.disconnect()


def c_measure(args):
    ex = open_explorer(args)
    try:
        rows = []
        for i in range(args.n):
            m = ex.read(MEASURE)
            m["t"] = round(i * args.period, 2)
            rows.append(m)
            if not args.json:
                print("  ".join(f"{k}={v.get('value') if v.get('value') is not None else v.get('raw')}"
                                for k, v in m.items() if k != "t"))
            if i < args.n - 1:
                time.sleep(args.period)
        if args.json:
            out(args, {"rows": rows})
    finally:
        ex.disconnect()


def c_sweep(args):
    ex = open_explorer(args)
    try:
        with ex.lock:
            ex.link.send(f"*LB{LNB_CODES[args.lnb]}")
        ex._wait_ready()
        pts = []
        f = args.start
        while f <= args.stop + 1e-9:
            ex.set_if(round((f - args.lo) * 1000), settle=args.settle)
            v = ex.read(["*?LV"])["LV"]
            pts.append({"rf_mhz": round(f, 3), "level": v.get("value"), "raw": v.get("raw")})
            f += args.step
        lv = [p["level"] for p in pts if p["level"] is not None]
        summ = {"points": len(pts), "valid": len(lv), "min": min(lv) if lv else None, "max": max(lv) if lv else None}
        out(args, {"summary": summ, "points": pts}, f"{summ}\n" + "\n".join(f"  {p['rf_mhz']:>9.1f}  {p['level']}" for p in pts))
    finally:
        ex.disconnect()


def c_dl(args):
    ex = open_explorer(args)
    try:
        if args.action == "list":
            files = ex.list_datalogger()
            out(args, {"files": files}, "\n".join(f"  {f['index']}: {f['name']}  raw={f['raw']!r}" for f in files) or tr("ui.dl.empty"))
        else:
            from ..dlfile import parse_dl
            path = ex.download_datalogger(args.name, data_dir() / "datalogger")
            res = {"file": str(path), "bytes": path.stat().st_size}
            try:
                d = parse_dl(path.read_bytes())
                res.update(name=d["name"], tests=len(d["tests"]), channels=len(d["plan"]["channels"]))
            except Exception as e:  # noqa: BLE001
                res["parse_error"] = str(e)
            out(args, res)
    finally:
        ex.disconnect()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tvx", description="TVXLink: direct console access to a TV EXPLORER II / II+")
    ap.add_argument("--port", default="auto", help="auto | COM3 | /dev/ttyUSB0 | usb | demo")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("-v", "--verbose", action="store_true", help="connection log to stderr")
    add_lang(ap)
    sub = ap.add_subparsers(dest="cmd_name", required=True)

    sub.add_parser("ports", help="COM ports and USB devices").set_defaults(f=c_ports)
    sub.add_parser("ident", help="*?NA / *?VE / *?ID").set_defaults(f=c_ident)
    q = sub.add_parser("q", help="queries *?..")
    q.add_argument("cmds", nargs="+")
    q.add_argument("--gap", type=float, default=0.05)
    q.set_defaults(f=c_query)
    s = sub.add_parser("set", help="one safe tuning command")
    s.add_argument("cmd")
    s.add_argument("--wait", type=float, default=1.5, help="pause after the command, s (shorter once locked)")
    s.set_defaults(f=c_set)
    stt = sub.add_parser("status", help="is the channel locked: *?ST (status line of the meter)")
    stt.add_argument("-n", type=int, default=1)
    stt.add_argument("-p", "--period", type=float, default=1.0)
    stt.set_defaults(f=c_status)
    li = sub.add_parser("listen", help="listen to raw bytes")
    li.add_argument("seconds", type=float, nargs="?", default=3)
    li.set_defaults(f=c_listen)
    t = sub.add_parser("tune", help="tune to a satellite transponder")
    t.add_argument("rf", type=float, help="RF, MHz")
    t.add_argument("--lo", type=float, default=9750)
    t.add_argument("--sr", type=int, required=True)
    t.add_argument("--fec", required=True)
    t.add_argument("--lnb", default="13V", choices=list(LNB_CODES))
    t.add_argument("--std", default="DVB-S", choices=["DVB-S", "DVB-S2"])
    t.add_argument("--mod", default="QPSK", choices=["QPSK", "8PSK"])
    t.add_argument("--inv", action="store_true")
    t.add_argument("--dwell", type=float, default=2.0)
    t.set_defaults(f=c_tune)
    g = sub.add_parser("goto", help="frequency only")
    g.add_argument("rf", type=float)
    g.add_argument("--lo", type=float, default=9750)
    g.add_argument("--lnb", default="13V", choices=list(LNB_CODES))
    g.add_argument("--dwell", type=float, default=0.5)
    g.set_defaults(f=c_goto)
    m = sub.add_parser("measure", help="a series of readings")
    m.add_argument("-n", type=int, default=5)
    m.add_argument("-p", "--period", type=float, default=1.0)
    m.set_defaults(f=c_measure)
    sw = sub.add_parser("sweep", help="frequency sweep")
    sw.add_argument("start", type=float)
    sw.add_argument("stop", type=float)
    sw.add_argument("step", type=float)
    sw.add_argument("--lo", type=float, default=9750)
    sw.add_argument("--lnb", default="13V", choices=list(LNB_CODES))
    sw.add_argument("--settle", type=float, default=1.0)
    sw.set_defaults(f=c_sweep)
    d = sub.add_parser("dl", help="datalogger")
    d.add_argument("action", choices=["list", "get"])
    d.add_argument("name", nargs="?")
    d.set_defaults(f=c_dl)

    args = ap.parse_args(argv)
    apply_lang(args)
    try:
        return args.f(args) or 0
    except Exception as e:  # noqa: BLE001 — a readable error instead of a traceback
        out(args, {"error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
