"""tvxlink-lab — run a lab session by chat through the open TVXLink window (its HTTP API).

The user writes to an AI agent "aimed the dish", "photographed the constellation",
"moving the dish away" — the agent answers with these commands. The window must be
running (Start-Windows.bat / Start-Mac.command): it holds the meter's COM port, so
tvx and tvxlink-selftest are not used meanwhile.

  tvxlink-lab status                 connection, channel, lock, C/N, MER, BER right now
  tvxlink-lab connect [--port COM3]  connect the meter (demo = simulator)
  tvxlink-lab rec 13E                current channels for the lab (QPSK and 8PSK)
  tvxlink-lab tune 13E 1             tune to QPSK #1 of the recommendations
  tvxlink-lab tune 13E 1 --8psk      … to 8PSK #1
  tvxlink-lab record optimum --note "picture is fine"   a reading into the protocol (5 polls)
  tvxlink-lab note "photographed the constellation, 4 clouds"   an entry in the session journal
  tvxlink-lab edge start | show | save | stop      decoding threshold (EDGE)
  tvxlink-lab pair 13E [N] [--save]  measure QPSK↔8PSK pair #N
  tvxlink-lab list                   protocol and journal
  tvxlink-lab angles [13E]           look angles from the lab coordinates

Kinds: optimum, edge, qpsk, 8psk, measure. --json prints the raw answer.
Only the meter writes numbers into the protocol; the user's words go to the journal.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from ..i18n import tr
from ..lab import KINDS, normalize_kind
from . import add_lang, apply_lang

URL = "http://127.0.0.1:8765"


class Api:
    def __init__(self, url: str):
        self.url = url.rstrip("/")

    def call(self, path: str, body=None, timeout: float = 120):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read().decode("utf-8")).get("error")
            except ValueError:
                msg = None
            raise SystemExit(f"{tr('cli.error')}: {msg or e}")
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            raise SystemExit(tr("cli.lab.no_window", url=self.url))


def f1(v, d=1):
    return "—" if v is None else f"{float(v):.{d}f}"


def ber(v, raw):
    if v is not None:
        return f"{v:.1E}"
    return "—" if not raw or str(raw).strip().endswith("!") else raw   # "!" — the meter gave no value


def ch(c):
    if not c:
        return tr("ui.prot.notuned")
    return (f"{c.get('pos') or '?'} {c.get('freq_mhz')} {c.get('pol') or ''}, SR {c.get('sr')}, "
            f"{c.get('system') or ''} {c.get('modulation') or ''} {c.get('fec') or ''}").replace("  ", " ")


def post_name(system):
    return "LBER" if system == "DVB-S2" else "VBER"


# --- commands ------------------------------------------------------------------------
def cmd_status(api, a):
    st = api.call("/api/status")
    cur = api.call("/api/lab/protocol").get("current")
    out = {"connected": st["connected"], "demo": st["demo"], "channel": cur}
    job = api.call("/api/job")
    if job.get("running"):
        out["job"] = {k: job.get(k) for k in ("kind", "done", "total", "current")}
    elif st["connected"]:
        m = api.call("/api/measure", {})
        out["status"] = m.get("status")
        out["values"] = {v["cmd"][2:4]: {"value": v.get("value"), "raw": v.get("raw")} for v in m["values"]}
    if a.json:
        return out
    conn = (tr("ui.state.demo") if st["demo"] else tr("ui.state.on")) if st["connected"] else tr("ui.state.off")
    lines = [f"{tr('ui.dev.title')}: {conn}", f"{tr('ui.channel')} {ch(cur)}"]
    if "job" in out:
        lines.append(tr("cli.lab.job", kind=out["job"]["kind"], cur=out["job"]["current"]))
    if "status" in out:
        s, v = out["status"] or {}, out["values"]
        lines.append(f"{'✔' if s.get('locked') else '✘'} {s.get('text')}")
        lines.append(f"LV {f1(v['LV']['value'])} dBµV · C/N {f1(v['CN']['value'])} dB · MER {f1(v['ME']['value'])} dB · "
                     f"CBER {ber(None, v['CB']['raw'])} · {post_name((cur or {}).get('system'))} {ber(None, v['VB']['raw'])}")
    return "\n".join(lines)


def cmd_connect(api, a):
    r = api.call("/api/connect", {"demo": a.port == "demo", "port": None if a.port in (None, "demo") else a.port})
    return r if a.json else tr("cli.lab.connected", demo=" (DEMO)" if r.get("demo") else "", info=r.get("info"))


def why_text(w):
    return tr("why." + w["k"], **{k: v for k, v in w.items() if k != "k"})


def cmd_rec(api, a):
    r = api.call("/api/lab/recommend?pos=" + urllib.parse.quote(a.pos))
    if a.json:
        return r
    lines = []
    for key, title in (("qpsk", tr("ui.rec.qpsk")), ("psk8", tr("ui.rec.psk8"))):
        lines.append(title + ":")
        for i, t in enumerate(r[key], 1):
            lines.append(f"  {i}. {t['freq_mhz']} {t['pol']} · SR {t['sr']} · {t['system']} {t['modulation']} {t['fec']} · "
                         f"LNB {t['lnb']} · {', '.join(t['channels'][:3])}")
            lines.append("     " + " · ".join(why_text(w) for w in t["why"]))
    return "\n".join(lines)


def cmd_tune(api, a):
    r = api.call("/api/lab/recommend?pos=" + urllib.parse.quote(a.pos))
    lst = r["psk8" if a.psk8 else "qpsk"]
    if not 1 <= a.n <= len(lst):
        raise SystemExit(tr("cli.lab.out_of_list", n=len(lst), i=a.n))
    t = lst[a.n - 1]
    res = api.call("/api/tune", {"rf_mhz": t["freq_mhz"], "lo_mhz": t["lo_mhz"], "sr": t["sr"], "fec": t["fec"],
                                 "lnb": t["lnb"], "standard": t["system"], "modulation": t["modulation"], "pos": a.pos})
    if a.json:
        return {"target": t, "result": res}
    return (f"{'✔' if res['locked'] else '✘'} {res['status']} · {a.pos} {t['freq_mhz']} {t['pol']} SR {t['sr']} "
            f"{t['system']} {t['modulation']} {t['fec']} · IF {res['if_mhz']} · {' '.join(res['sent']) or '—'}")


def cmd_record(api, a):
    r = api.call("/api/lab/record", {"kind": normalize_kind(a.kind), "note": a.note or "", "n": a.n})
    if a.json:
        return r
    lock = {True: tr("cli.lab.lock_all"), False: tr("cli.lab.lock_none"), "partial": tr("ui.prot.partial")}.get(r["locked"], "?")
    return (tr("ui.prot.saved", id=r["id"], kind=tr("kind." + r["kind"]), cn=f1(r["cn"]), mer=f1(r["mer"]),
               cber=ber(r["cber_v"], r["cber"])) + f" — {ch(r)}\n"
            f"C/N {f1(r['cn_min'])}…{f1(r['cn_max'])} · {r['post_name']} {ber(r['vber_v'], r['vber'])} · {lock}"
            + (" · DEMO" if r.get("demo") else ""))


def cmd_note(api, a):
    r = api.call("/api/lab/note", {"text": " ".join(a.text), "source": "agent"})
    return r if a.json else f"{tr('ui.jr.title')}: {r['time'][11:]} [{r.get('channel', '—')}] {r['text']}"


def cmd_edge(api, a):
    if a.action == "start":
        r = api.call("/api/lab/edge", {"interval": a.interval})
        return r if a.json else tr("cli.lab.edge_started")
    if a.action == "stop":
        api.call("/api/job/stop", {})
        time.sleep(1.5)
    job = api.call("/api/job")
    if job.get("kind") != "edge":
        raise SystemExit(tr("cli.lab.edge_none"))
    x, items = job.get("extra") or {}, job.get("items") or []
    if a.action == "save":
        e = x.get("edge") or x.get("last_locked")
        if not e:
            raise SystemExit(tr("cli.lab.edge_nolock"))
        r = api.call("/api/lab/add", {"items": [{**e, "kind": "edge", "note": a.note or ""}], "source": "edge"})
        row = r["rows"][0]
        return r if a.json else (tr("ui.edge.saved", id=row["id"], kind=tr("kind.edge")) +
                                 f": C/N {f1(row['cn'])}, MER {f1(row['mer'])}, CBER {ber(row['cber_v'], row['cber'])}, "
                                 f"{row['post_name']} {ber(row['vber_v'], row['vber'])}" +
                                 ("" if x.get("edge") else " " + tr("cli.lab.edge_noloss")))
    if a.json:
        return {"running": job["running"], "count": len(items), "last": items[-1] if items else None, **x}
    last = items[-1] if items else None
    lines = [tr("ui.edge.watching", n=len(items), losses=x.get("losses", 0)) if job["running"]
             else tr("ui.edge.stopped", n=len(items), dir=job.get("dir"))]
    if last:
        lines.append(f"{'✔' if last['locked'] else '✘'} {last['device_status']} · C/N {f1(last['cn'])} · MER {f1(last['mer'])} · "
                     f"CBER {ber(last.get('cber_v'), last.get('cber'))}")
    if x.get("edge"):
        e = x["edge"]
        lines.append(tr("ui.edge.candidate", time=e["time"][11:], cn=f1(e["cn"]), mer=f1(e["mer"]), cber=ber(e.get("cber_v"), e.get("cber")),
                        post="VBER/LBER", pber=ber(e.get("vber_v"), e.get("vber")), run=e["run"]) + " → tvxlink-lab edge save")
    if x.get("min_locked_cn") is not None:
        lines.append(tr("ui.edge.mincn", min=f1(x["min_locked_cn"]), best=f1(x["best_cn"])))
    return "\n".join(lines)


def diff_text(ds):
    return ", ".join(tr("diff." + d["k"], **{k: v for k, v in d.items() if k != "k"}) for d in ds) or tr("ui.pair.onlymod")


def cmd_pair(api, a):
    pairs = api.call("/api/lab/pairs?pos=" + urllib.parse.quote(a.pos))["pairs"]
    if a.n is None:
        if a.json:
            return pairs[:15]
        lines = []
        for i, p in enumerate(pairs[:15], 1):
            e, o = p["psk8"], p["options"][0]
            lines.append(f"{i}. 8PSK {e['freq_mhz']} {e['pol']} SR {e['sr']} {e['fec']} ↔ QPSK {o['qpsk']['freq_mhz']} {o['qpsk']['pol']} "
                         f"SR {o['qpsk']['sr']} {o['qpsk']['system']} {o['qpsk']['fec']} · {diff_text(o['diffs'])}")
        return "\n".join(lines) + "\n→ tvxlink-lab pair POS N --save"
    p = pairs[a.n - 1]
    e, o = p["psk8"], p["options"][a.option - 1]
    q = o["qpsk"]
    api.call("/api/scan", {"picks": [{"pos": a.pos, "freq_mhz": q["freq_mhz"], "pol": q["pol"], "sr": q["sr"]},
                                     {"pos": a.pos, "freq_mhz": e["freq_mhz"], "pol": e["pol"], "sr": e["sr"]}],
                           "dwell": a.dwell, "keep_order": True, "kind": "pair"})
    job = api.call("/api/job")
    while job.get("running"):
        time.sleep(0.5)
        job = api.call("/api/job")
    items = job.get("items") or []
    if a.save and items:
        api.call("/api/lab/add", {"items": [{**i, "kind": "8psk" if i["modulation"] == "8PSK" else "qpsk"} for i in items],
                                  "source": "pair"})
    if a.json:
        return {"items": items, "diffs": o["diffs"], "saved": bool(a.save)}
    lines = [f"{i['modulation']}: {i['freq_mhz']} {i['pol']} SR {i['sr']} {i['system']} {i['fec']} · "
             f"{'✔' if i['locked'] else '✘'} {i['device_status']} · C/N {f1(i['cn'])} · MER {f1(i['mer'])} · "
             f"CBER {ber(i.get('cber_v'), i.get('cber'))} · {post_name(i['system'])} {ber(i.get('vber_v'), i.get('vber'))}" for i in items]
    if o["diffs"]:
        lines.append("⚠ " + tr("ui.pair.also") + " " + diff_text(o["diffs"]))
    lines.append(tr("cli.lab.pair_saved") if a.save else tr("cli.lab.pair_unsaved"))
    return "\n".join(lines)


def cmd_list(api, a):
    pr, jr = api.call("/api/lab/protocol"), api.call("/api/lab/journal")
    if a.json:
        return {"protocol": pr["rows"], "journal": jr["items"], "csv": pr["csv"], "txt": jr["txt"]}
    lines = [tr("ui.prot.title") + ":"] + [
        f"  #{r['id']} {r['time'][11:16]} {tr('kind.' + r['kind'])}: {ch(r)} · C/N {f1(r.get('cn'))} · MER {f1(r.get('mer'))} · "
        f"CBER {ber(r.get('cber_v'), r.get('cber'))} · {r.get('post_name')} {ber(r.get('vber_v'), r.get('vber'))}"
        + (" · DEMO" if r.get("demo") else "") + (f" · {r['note']}" if r.get("note") else "") for r in pr["rows"]]
    lines += [tr("ui.jr.title") + ":"] + [f"  {i['time'][11:16]} [{i.get('channel', '—')}] {i['text']}" for i in jr["items"]]
    lines += [f"{pr['csv']} · {jr['txt']}"]
    return "\n".join(lines)


def cmd_angles(api, a):
    r = api.call("/api/lab/angles")
    rows = [x for x in r["rows"] if not a.pos or x["pos"] == a.pos]
    if a.json:
        return rows
    return "\n".join(f"{x['pos']:>6}  Az {x['az']:.2f}°  El {x['el']:.2f}°  LNB {x['skew']:+.1f}°  ({x['name']})" for x in rows)


def main(argv=None):
    p = argparse.ArgumentParser(prog="tvxlink-lab", description="TVXLink: run a lab session by chat through the open window")
    p.add_argument("--url", default=URL)
    p.add_argument("--json", action="store_true")
    add_lang(p)
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("status")
    s = sp.add_parser("connect"); s.add_argument("--port")
    s = sp.add_parser("rec"); s.add_argument("pos")
    s = sp.add_parser("tune"); s.add_argument("pos"); s.add_argument("n", type=int, nargs="?", default=1)
    s.add_argument("--8psk", dest="psk8", action="store_true")
    s = sp.add_parser("record"); s.add_argument("kind", help="/".join(KINDS))
    s.add_argument("--note"); s.add_argument("-n", type=int, default=5)
    s = sp.add_parser("note"); s.add_argument("text", nargs="+")
    s = sp.add_parser("edge"); s.add_argument("action", choices=["start", "show", "save", "stop"])
    s.add_argument("--interval", type=float, default=1.0); s.add_argument("--note")
    s = sp.add_parser("pair"); s.add_argument("pos"); s.add_argument("n", type=int, nargs="?")
    s.add_argument("--option", type=int, default=1); s.add_argument("--dwell", type=float, default=3.0)
    s.add_argument("--save", action="store_true")
    sp.add_parser("list")
    s = sp.add_parser("angles"); s.add_argument("pos", nargs="?")
    a = p.parse_args(argv)
    apply_lang(a)
    result = globals()["cmd_" + a.cmd](Api(a.url), a)
    print(json.dumps(result, ensure_ascii=False, indent=1) if a.json and not isinstance(result, str) else result)


if __name__ == "__main__":
    main()
