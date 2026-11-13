"""Local web interface (127.0.0.1 only) for the TV EXPLORER II / II+."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, i18n, lab
from .device import Explorer, MEASURE_COMMANDS
from .dlfile import DLFormatError, parse_dl, to_csv
from .foundlog import FoundLog
from .i18n import tr
from .paths import data_dir
from .protocol import ProtocolError
from .scan import LOCK_QUERIES, Automation, load_verified
from .tusb3410 import DeviceNotFound, list_usb_devices

UI_FILE = Path(__file__).resolve().parent / "ui.html"
DATA_DIR = data_dir()
DL_DIR = DATA_DIR / "datalogger"
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
MAX_BODY = 1 << 20


def origin_ok(origin: str | None, port: int) -> bool:
    """POST is accepted only from our own page: http://127.0.0.1:<port> or http://localhost:<port>.

    No Origin header — a non-browser client (tvxlink-lab, curl). A prefix check is not enough:
    'http://localhost.evil.com' starts with 'http://localhost'.
    """
    if origin is None:
        return True
    try:
        u = urlparse(origin)
        return u.scheme == "http" and u.hostname in ALLOWED_HOSTS and u.port == port
    except ValueError:
        return False


def open_folder(path: Path):
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 — Windows Explorer
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def local_dl_files():
    DL_DIR.mkdir(parents=True, exist_ok=True)
    return sorted((p.name for p in DL_DIR.glob("*.dl")), key=str.lower)


def open_dl(name: str) -> dict:
    path = (DL_DIR / Path(name).name)
    try:
        parsed = parse_dl(path.read_bytes())
    except IndexError:
        raise DLFormatError(tr("err.dl_short"))
    csv_path = path.with_suffix(".csv")
    to_csv(parsed, csv_path)
    parsed["csv"] = str(csv_path)
    return parsed


explorer = Explorer(DATA_DIR / "logs")
auto = Automation(explorer, DATA_DIR)
found = FoundLog(DATA_DIR)
auto.found = found
protocol = lab.LabProtocol(DATA_DIR)
journal = lab.LabJournal(DATA_DIR)
sat_update = {"running": False, "pos": None, "i": 0, "n": 0, "step": "", "report": None, "finished": None}


def run_sat_update():
    """In the background: download KingOfSat for every position, rebuild, check, reload."""
    from . import satdb
    from .scan import load_satellites, user_sat_dir

    def progress(pos, i, n, step):
        sat_update.update(pos=pos, i=i, n=n, step=step)

    try:
        rep = satdb.update_all(user_sat_dir(), old=auto.sats, progress=progress)
        auto.sats = load_satellites()
        sat_update.update(report=rep, step="done")
    except Exception as e:  # noqa: BLE001
        sat_update.update(report=[{"pos": "—", "ok": False, "error": str(e)}], step="error")
    finally:
        sat_update.update(running=False, finished=datetime.now().isoformat(timespec="seconds"))


def list_sessions(kind: str | None = None) -> list[dict]:
    """Saved jobs in <data>/sessions (newest first): name, kind, number of items."""
    out = []
    root = DATA_DIR / "sessions"
    for d in sorted(root.glob("*_*"), reverse=True) if root.exists() else []:
        k = d.name.split("_", 2)[-1]
        if not d.is_dir() or (kind and k != kind) or not (d / "result.json").exists():
            continue
        try:
            n = len(json.loads((d / "result.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        out.append({"name": d.name, "kind": k, "count": n})
    return out


def load_session(name: str) -> dict:
    d = DATA_DIR / "sessions" / Path(name).name          # no path traversal
    items = json.loads((d / "result.json").read_text(encoding="utf-8"))
    noise = None
    if (d / "noise.json").exists():
        noise = json.loads((d / "noise.json").read_text(encoding="utf-8")).get("noise_floor")
    return {"name": d.name, "kind": d.name.split("_", 2)[-1], "items": items, "noise_floor": noise, "dir": str(d)}


def find_tp(pos, freq_mhz, pol, sr=None):
    """A transponder of the database by position/frequency/polarisation (±1 MHz), if known."""
    sat = auto.sats.get(pos or "")
    if not sat or freq_mhz is None:
        return None
    c = [t for t in sat["transponders"] if t["pol"] == pol and abs(t["freq_mhz"] - float(freq_mhz)) <= 1.0
         and (sr is None or abs(t["sr"] - int(sr)) <= 100)]
    return min(c, key=lambda t: abs(t["freq_mhz"] - float(freq_mhz))) if c else None


def log_live(status: dict, values: dict, source: str):
    """Auto-log: the meter reports "locked" — put the current transponder into the found log."""
    if not status or not status.get("locked") or not explorer.current.get("freq_mhz"):
        return
    cur = explorer.current
    tp = find_tp(cur.get("pos"), cur["freq_mhz"], cur.get("pol"), cur.get("sr"))
    v = lambda k: (values.get(k) or {}).get("value")  # noqa: E731
    found.record({
        "pos": cur.get("pos"), "sat_name": auto.sats.get(cur.get("pos") or "", {}).get("name"),
        "freq_mhz": tp["freq_mhz"] if tp else cur["freq_mhz"], "pol": cur.get("pol"), "sr": cur.get("sr"),
        "system": cur.get("system"), "modulation": cur.get("modulation"), "fec": cur.get("fec"),
        "lo_mhz": cur.get("lo_mhz"), "lnb": cur.get("lnb"), "device_status": status.get("text"), "st": status.get("st"),
        "level": v("LV"), "cn": v("CN"), "mer": v("ME"),
        "cber": (values.get("CB") or {}).get("raw"), "vber": (values.get("VB") or {}).get("raw"),
        "channels": [c["name"] for c in tp["channels"]] if tp else None, "video": tp["video"] if tp else None,
        "verified": tp.get("verified") if tp else None, "source": source})


# --- lab assistant ----------------------------------------------------------------------
def channel_params(src: dict | None = None) -> dict:
    """Channel parameters for a protocol row: from a reading (src) or the current tuning."""
    src, cur = src or {}, explorer.current
    g = lambda k: src.get(k) if src.get(k) is not None else cur.get(k)  # noqa: E731
    pos, freq, lo = g("pos"), g("freq_mhz"), g("lo_mhz")
    tp = find_tp(pos, freq, g("pol"), g("sr")) if freq else None
    return {"pos": pos, "sat_name": auto.sats.get(pos or "", {}).get("name"), "freq_mhz": freq, "pol": g("pol"),
            "sr": g("sr"), "system": g("system"), "modulation": g("modulation") or ("QPSK" if g("system") else None),
            "fec": g("fec"), "lo_mhz": lo, "lnb": g("lnb"),
            "if_mhz": round(float(freq) - float(lo), 3) if freq and lo else None,
            "channels": [c["name"] for c in tp["channels"]][:8] if tp else src.get("channels")}


def flat_values(item: dict) -> dict:
    """One reading of a scan or threshold run → protocol fields."""
    out = {"n": 1, "level": item.get("level"), "locked": item.get("locked"),
           "device_status": item.get("device_status"), "st": item.get("st")}
    for k in ("cn", "mer"):
        out[k] = out[k + "_min"] = out[k + "_max"] = item.get(k)
    for k in ("cber", "vber"):
        out[k] = item.get(k)
        out[k + "_v"] = lab.ber_value(item.get(k))
    return out


def lab_record(kind: str, note: str, n: int, interval: float) -> dict:
    if auto.job and auto.job.running:
        raise ProtocolError(tr("err.job_running"))
    if not explorer.connected:
        raise ProtocolError(tr("err.not_connected"))
    samples = []
    for i in range(n):
        s = explorer.read(LOCK_QUERIES)
        try:
            s["status"] = explorer.lock_status()
        except ProtocolError as e:
            s["status"] = {"locked": None, "text": str(e), "st": None}
        samples.append(s)
        if i < n - 1:
            time.sleep(interval)
    log_live(samples[-1]["status"], samples[-1], "lab")
    return protocol.add({**channel_params(), **lab.aggregate(samples), "kind": kind, "note": note,
                         "source": "measure", "demo": explorer.demo})


AIM_QUERIES = ["*?LV", "*?CN", "*?ME"]
recording = {"file": None, "writer": None, "path": None}


def start_recording():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"measure_{datetime.now():%Y%m%d_%H%M%S}.csv"
    f = open(path, "w", newline="", encoding="utf-8-sig")   # BOM so that Excel on Windows reads Cyrillic
    w = csv.writer(f, delimiter=";")
    w.writerow(["time"] + [f"{tr(label)} ({cmd})" for cmd, label in MEASURE_COMMANDS]
               + [tr("col.device_status"), tr("col.st_code")])
    recording.update(file=f, writer=w, path=path)
    return path


def stop_recording():
    if recording["file"]:
        recording["file"].close()
    path = recording["path"]
    recording.update(file=None, writer=None, path=None)
    return path


class Handler(BaseHTTPRequestHandler):
    server_version = f"TVXLink/{__version__}"

    def log_message(self, *_args):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 <= n <= MAX_BODY:
            raise ValueError(tr("err.bad_body"))
        body = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(body, dict):
            raise ValueError(tr("err.bad_body"))
        return body

    def _host_ok(self) -> bool:
        """Only the local page may talk to the server (DNS-rebinding protection)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in ALLOWED_HOSTS

    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "forbidden"}, 403)
        url = urlparse(self.path)
        q = parse_qs(url.query)
        p = url.path
        if p == "/":
            body = UI_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif p == "/api/i18n":
            lang = (q.get("lang", [""])[0] or i18n.get_lang())[:2]
            lang = lang if lang in i18n.LANGS else i18n.DEFAULT
            self._json({"lang": lang, "langs": list(i18n.LANGS), "dict": i18n.catalog(lang)})
        elif p == "/api/lang":
            self._json({"lang": i18n.get_lang(), "langs": list(i18n.LANGS)})
        elif p == "/api/status":
            self._json({"connected": explorer.connected, "info": explorer.info, "demo": explorer.demo,
                        "version": __version__, "lang": i18n.get_lang(),
                        "recording": str(recording["path"]) if recording["path"] else None,
                        "measures": [{"cmd": c, "label": tr(l)} for c, l in MEASURE_COMMANDS]})
        elif p == "/api/sats":
            self._json({"sats": [{"pos": k, "name": v["name"], "built": v["built"], "source": v["source"],
                                  "count": len(v["transponders"]), "file": v.get("_file")} for k, v in auto.sats.items()]})
        elif p == "/api/sat":
            pos = q["pos"][0]
            if explorer.demo and hasattr(explorer.port, "point"):
                explorer.port.point(pos)   # in demo mode the "antenna" looks at the chosen satellite
            self._json(auto.sats[pos])
        elif p == "/api/sats/update":
            self._json(sat_update)
        elif p in ("/api/verified", "/api/caught"):     # /api/caught — old name used by the current page
            self._json({"items": load_verified()})
        elif p == "/api/lab/protocol":
            self._json({"rows": protocol.list(), "csv": str(protocol.csv), "kinds": list(lab.KINDS),
                        "current": channel_params() if explorer.current.get("freq_mhz") else None})
        elif p == "/api/lab/theory":
            self._json(lab.theory(float(q.get("bn", [lab.BN_RS])[0]), explorer.current))
        elif p == "/api/lab/angles":
            try:
                lat = lab.parse_coord(q["lat"][0]) if q.get("lat") else lab.LAB_LAT
                lon = lab.parse_coord(q["lon"][0]) if q.get("lon") else lab.LAB_LON
                decl = float(q["decl"][0].replace(",", ".")) if q.get("decl") and q["decl"][0].strip() else None
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            self._json({"lat": lat, "lon": lon, "decl": decl, "rows": lab.angles_table(auto.sats, lat, lon, decl)})
        elif p == "/api/lab/journal":
            self._json({"items": journal.list(), "txt": str(journal.txt)})
        elif p == "/api/lab/recommend":
            self._json({"pos": q["pos"][0], **lab.recommend(auto.sats.get(q["pos"][0], {}))})
        elif p == "/api/lab/pairs":
            self._json({"pos": q["pos"][0], "pairs": lab.pairs(auto.sats.get(q["pos"][0], {}))})
        elif p == "/api/ports":
            try:
                from .serialport import list_ports
                ports = list_ports()
            except ImportError:
                ports = []
            self._json({"ports": ports, "os": os.name})
        elif p == "/api/datadir":
            self._json({"dir": str(DATA_DIR)})
        elif p == "/api/found":
            self._json({"items": found.list(), "csv": str(found.csv)})
        elif p == "/api/sessions":
            self._json({"sessions": list_sessions(q.get("kind", [None])[0])})
        elif p == "/api/session":
            try:
                self._json(load_session(q["name"][0]))
            except (OSError, ValueError, KeyError) as e:
                self._json({"error": str(e)}, 404)
        elif p == "/api/job":
            since = int(q.get("since", ["0"])[0])
            self._json(auto.job.state(since) if auto.job else {"kind": None, "running": False})
        elif p == "/api/log":
            self._json({"lines": explorer.log_lines[-400:]})
        elif p == "/api/dl/local":
            self._json({"files": local_dl_files(), "dir": str(DL_DIR)})
        elif p == "/api/usb":
            self._json({"devices": [{"vid": f"{v:04x}", "pid": f"{p_:04x}", "name": n}
                                    for v, p_, n in list_usb_devices()]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        # Requests are accepted only from our own page.
        if not self._host_ok() or not origin_ok(self.headers.get("Origin"), self.server.server_address[1]):
            return self._json({"error": "forbidden"}, 403)
        try:
            self._post(self.path, self._body())
        except DLFormatError as e:
            self._json({"error": tr("err.dl_parse", error=e)}, 422)
        except DeviceNotFound as e:
            self._json({"error": str(e)}, 404)
        except (ProtocolError, OSError, ValueError) as e:
            self._json({"error": str(e)}, 500)
        except Exception as e:   # noqa: BLE001 — a missing field etc.: answer instead of dropping the connection
            traceback.print_exc()
            self._json({"error": tr("err.internal", error=f"{type(e).__name__}: {e}")}, 500)

    def _post(self, p: str, b: dict):
        if p == "/api/lang":
            self._json({"lang": i18n.set_lang(b.get("lang", ""), save=True)})
        elif p == "/api/connect":
            self._json({"info": explorer.connect(demo=bool(b.get("demo")), port=b.get("port") or None),
                        "demo": explorer.demo})
        elif p == "/api/disconnect":
            stop_recording()
            auto.stop()
            explorer.disconnect()
            self._json({"ok": True})
        elif p == "/api/command":
            if auto.job and auto.job.running:
                raise ProtocolError(tr("err.job_running"))
            self._json(explorer.command(b.get("cmd", ""), expert=bool(b.get("expert"))))
        elif p == "/api/measure":
            m = explorer.measure()
            try:
                m["status"] = explorer.lock_status()
            except ProtocolError as e:
                m["status"] = {"locked": None, "text": str(e), "st": None}
            log_live(m["status"], {v["cmd"][2:4]: v for v in m["values"]}, "measure")
            if recording["writer"]:
                recording["writer"].writerow([m["time"]] + [v.get("raw") or "" for v in m["values"]] +
                                             [m["status"]["text"], m["status"].get("st")])
                recording["file"].flush()
            self._json(m)
        elif p == "/api/record":
            on = bool(b.get("on"))
            path = start_recording() if on else stop_recording()
            self._json({"recording": on, "path": str(path) if path else None})
        elif p == "/api/tune":
            if auto.job and auto.job.running:
                raise ProtocolError(tr("err.job_running"))
            if b.get("pos"):
                explorer.current = {**explorer.current, "pos": b["pos"]}
                if explorer.demo and hasattr(explorer.port, "point"):
                    explorer.port.point(b["pos"])     # in demo mode the "antenna" looks at this satellite
            r = explorer.tune_satellite(
                rf_mhz=float(b["rf_mhz"]), lo_mhz=float(b["lo_mhz"]), symbol_rate=int(b["sr"]),
                fec=b["fec"], lnb=b["lnb"], standard=b["standard"],
                modulation=b.get("modulation", "QPSK"), inversion=bool(b.get("inversion")))
            if r["locked"]:
                log_live({"locked": True, "text": r["status"], "st": r["st"]}, explorer.read(LOCK_QUERIES), "tune")
            self._json(r)
        elif p == "/api/goto":
            if auto.job and auto.job.running:
                raise ProtocolError(tr("err.job_running"))
            self._json(explorer.goto_frequency(float(b["rf_mhz"]), float(b["lo_mhz"]), b["lnb"]))
        elif p == "/api/aim":
            if auto.job and auto.job.running:
                raise ProtocolError(tr("err.job_running"))
            r = explorer.read(AIM_QUERIES)
            try:
                r["status"] = explorer.lock_status()
            except ProtocolError as e:
                r["status"] = {"locked": None, "text": str(e), "st": None}
            log_live(r["status"], r, "aim")
            self._json(r)
        elif p == "/api/lab/record":
            self._json(lab_record(b.get("kind", "measure"), str(b.get("note", ""))[:300],
                                  max(1, min(20, int(b.get("n", 5)))),
                                  max(0.2, min(5.0, float(b.get("interval", 0.5))))))
        elif p == "/api/lab/add":
            rows = [protocol.add({**channel_params(it), **flat_values(it), "kind": it.get("kind") or b.get("kind", "measure"),
                                  "note": str(it.get("note") or b.get("note", ""))[:300], "time": it.get("time"),
                                  "source": b.get("source", "measure"), "demo": explorer.demo})
                    for it in b.get("items", [])]
            self._json({"rows": rows})
        elif p == "/api/lab/note":
            self._json(journal.add(b.get("text", ""), b.get("source", "window"), explorer.current))
        elif p == "/api/lab/delete":
            protocol.delete(int(b["id"]))
            self._json({"ok": True})
        elif p == "/api/lab/edge":
            self._json(auto.start_edge(float(b.get("interval", 1.0))).state())
        elif p == "/api/sats/update":
            if not sat_update["running"]:
                sat_update.update(running=True, report=None, step="start", i=0, n=0, pos=None)
                threading.Thread(target=run_sat_update, daemon=True).start()
            self._json(sat_update)
        elif p == "/api/scan":
            self._json(auto.start_scan(b["picks"], dwell=float(b.get("dwell", 2.0)),
                                       stop_on_lock=bool(b.get("stop_on_lock")),
                                       keep_order=bool(b.get("keep_order")),
                                       kind="pair" if b.get("kind") == "pair" else None).state())
        elif p == "/api/sweep":
            self._json(auto.start_sweep(float(b["center"]), float(b["span"]), float(b["step"]),
                                        float(b["lo"]), b["lnb"], settle=float(b.get("settle", 1.0))).state())
        elif p == "/api/job/stop":
            auto.stop()
            self._json({"ok": True})
        elif p == "/api/open-data":
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            open_folder(DATA_DIR)
            self._json({"dir": str(DATA_DIR)})
        elif p == "/api/report":
            target = auto.job.dir if auto.job and auto.job.dir else DATA_DIR / "sessions"
            target.mkdir(parents=True, exist_ok=True)
            path = target / f"report_{datetime.now():%H%M%S}.html"
            path.write_text(b.get("html", ""), encoding="utf-8")
            self._json({"path": str(path)})
        elif p == "/api/dl/list":
            self._json({"files": explorer.list_datalogger()})
        elif p == "/api/dl/download":
            path = explorer.download_datalogger(b.get("name", ""), DL_DIR)
            self._json({"file": path.name, "parsed": open_dl(path.name)})
        elif p == "/api/dl/open":
            self._json({"parsed": open_dl(b.get("file", ""))})
        else:
            self._json({"error": "not found"}, 404)


def _running_here(port: int) -> bool:
    """Is a TVXLink window already serving this port?"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1.5) as r:
            return "version" in json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return False


def main(port: int = 8765, open_browser: bool = True, lang: str | None = None):
    if lang:
        i18n.set_lang(lang)
    srv = None
    for p in range(port, port + 10):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            if _running_here(p):            # the program is already open: just show it again
                print(tr("cli.already_running", url=f"http://127.0.0.1:{p}/"), flush=True)
                if open_browser:
                    webbrowser.open(f"http://127.0.0.1:{p}/")
                return
    if srv is None:
        raise SystemExit(tr("cli.no_port", port=port))
    url = f"http://127.0.0.1:{port}/"
    print(tr("cli.server_started", version=__version__, url=url), flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_recording()
        if explorer.connected:
            explorer.disconnect()
