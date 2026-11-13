"""Lab assistant: measurement protocol, BER(C/N) theory, QPSK↔8PSK pairs, look angles.

Rule: the protocol contains only what the meter reported. Theory (BER curves,
QEF thresholds, look angles) is shown separately and labelled as a calculation.

Formulas (the satellite-communications lab manual this program was built for):
  Look angles:  Δλ = λs − λ;  cos b = cos φ · cos Δλ;  d = √(a² + R² − 2aR·cos b);
                El = atan2(cos b − R/a, √(1 − cos² b));  Az = atan2(sin Δλ, −sin φ · cos Δλ);
                R = 6371 km, a = 42164 km.
  BER:          Eb/N0 = C/N · Bn/Rb,  Rb = k·Rs (k = 2 QPSK, 3 8PSK),  Bn = 1.4·Rs;
                QPSK (Gray, AWGN): Pb = ½·erfc(√(Eb/N0));
                8PSK (Gray, approximation): Pb ≈ ⅓·erfc(√(3·Eb/N0)·sin(π/8)).
"""

from __future__ import annotations

import csv
import json
import math
import re
import threading
from datetime import datetime
from pathlib import Path

from . import satdb
from .i18n import tr

R_EARTH_KM = 6371.0
A_GEO_KM = 42164.0
C_KM_S = 299792.458
# Default station: the dish of the satellite lab of KPI RTF, Kyiv (50°26′51.15″ N, 30°27′28.12″ E).
LAB_LAT = 50 + 26 / 60 + 51.15 / 3600
LAB_LON = 30 + 27 / 60 + 28.12 / 3600
BN_RS = 1.4                                  # Bn = 1.12 · 1.25 · Rs
BITS = {"QPSK": 2, "8PSK": 3}
BER_FLOOR = 1e-12                            # curves are not drawn below this


# --- coordinates and look angles ----------------------------------------------------
_DMS = re.compile(r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*[°º d]?\s*(?:(\d+(?:[.,]\d+)?)\s*['′’m]?\s*)?"
                  r"(?:(\d+(?:[.,]\d+)?)\s*(?:\"|″|”|''|′′|s)?\s*)?(ПН|ПД|СХ|ЗХ|N|S|E|W)?\s*$", re.I)
_SOUTH_WEST = ("S", "W", "ПД", "ЗХ")


def parse_coord(text) -> float:
    """'50.4475', '50,4475', '50°26′51,15″ N', '30 27 28.12 E' → degrees (S/W negative)."""
    if isinstance(text, (int, float)):
        return float(text)
    m = _DMS.match(str(text).strip())
    if not m:
        raise ValueError(tr("err.bad_coord", text=text))
    d, mi, s, hemi = m.groups()
    f = lambda x: float(x.replace(",", ".")) if x else 0.0  # noqa: E731
    deg = abs(f(d)) + f(mi) / 60 + f(s) / 3600
    neg = d.strip().startswith("-") or (hemi or "").upper() in _SOUTH_WEST
    return -deg if neg else deg


def sat_longitude(pos: str) -> float:
    """'16E' → 16, '5W' → −5, '19.2E' → 19.2."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([EW])\s*$", pos, re.I)
    if not m:
        raise ValueError(tr("err.bad_position", pos=pos))
    v = float(m.group(1))
    return -v if m.group(2).upper() == "W" else v


def look_angles(lat: float, lon: float, sat_lon: float) -> dict:
    """Direction to a geostationary satellite (spherical Earth)."""
    phi = math.radians(lat)
    dl = math.radians(sat_lon - lon)
    cos_b = math.cos(phi) * math.cos(dl)
    d = math.sqrt(A_GEO_KM ** 2 + R_EARTH_KM ** 2 - 2 * A_GEO_KM * R_EARTH_KM * cos_b)
    el = math.degrees(math.atan2(cos_b - R_EARTH_KM / A_GEO_KM, math.sqrt(max(0.0, 1 - cos_b ** 2))))
    az = math.degrees(math.atan2(math.sin(dl), -math.sin(phi) * math.cos(dl))) % 360
    # Tilt of the polarisation plane against the local vertical (LNB skew).
    # Sign: "−" — the satellite is west of the station, "+" — east. Fine-tune by maximum MER.
    skew = math.degrees(math.atan2(math.sin(dl), math.tan(phi)))
    return {"az": round(az, 5), "el": round(el, 5), "d_km": round(d, 2), "b_deg": round(math.degrees(math.acos(cos_b)), 5),
            "skew": round(skew, 2), "delay_ms": round(d / C_KM_S * 1000, 3), "visible": el > 0}


def angles_table(sats: dict, lat: float = LAB_LAT, lon: float = LAB_LON, declination: float | None = None) -> list[dict]:
    rows = []
    for pos, s in sats.items():
        try:
            a = look_angles(lat, lon, sat_longitude(pos))
        except ValueError:
            continue
        if declination is not None:
            a["az_mag"] = round((a["az"] - declination) % 360, 2)   # east declination is "+"
        rows.append({"pos": pos, "name": s.get("name", pos), **a})
    return rows


# --- BER theory -------------------------------------------------------------------------
def ebn0_db(cn_db: float, mod: str = "QPSK", bn_rs: float = BN_RS) -> float:
    """C/N (in the noise bandwidth Bn) → Eb/N0 of a coded bit."""
    return cn_db + 10 * math.log10(bn_rs / BITS[mod])


def ber_theory(cn_db: float, mod: str = "QPSK", bn_rs: float = BN_RS) -> float:
    """Ideal BER before the decoder (AWGN, Gray) — a model, not an expected VBER/LBER."""
    g = 10 ** (ebn0_db(cn_db, mod, bn_rs) / 10)
    if mod == "QPSK":
        return 0.5 * math.erfc(math.sqrt(g))
    if mod == "8PSK":
        return min(0.5, math.erfc(math.sqrt(3 * g) * math.sin(math.pi / 8)) / 3)
    raise ValueError(mod)


def ber_curve(mod: str, bn_rs: float = BN_RS, cn_from: float = -2.0, cn_to: float = 22.0, step: float = 0.25) -> list:
    out = []
    n = int(round((cn_to - cn_from) / step))
    for i in range(n + 1):
        cn = round(cn_from + i * step, 3)
        b = ber_theory(cn, mod, bn_rs)
        if b < BER_FLOOR:
            break
        out.append([cn, b])
    return out


def qef_cn(system: str, mod: str, fec: str, bn_rs: float = BN_RS):
    """ETSI QEF threshold on the C/N scale (bandwidth Bn): Es/N0 − 10·lg(Bn/Rs). No margin."""
    esn0 = satdb.qef_esn0(system, mod, fec)
    if esn0 is None:
        return None
    return {"esn0": round(esn0, 2), "cn": round(esn0 - 10 * math.log10(bn_rs), 2)}


def theory(bn_rs: float = BN_RS, current: dict | None = None) -> dict:
    out = {"bn_rs": bn_rs, "curves": {m: ber_curve(m, bn_rs) for m in BITS}}
    cur = current or {}
    if cur.get("system") and cur.get("fec"):
        mod = cur.get("modulation") or "QPSK"
        out["qef"] = {"system": cur["system"], "modulation": mod, "fec": cur["fec"],
                      **(qef_cn(cur["system"], mod, cur["fec"], bn_rs) or {})}
    return out


# --- BER from a meter reply ----------------------------------------------------------
_NUM_E = re.compile(r"([+-]?\d+(?:[.,]\d+)?[eE][+-]?\d+)")
_MANT_EXP = re.compile(r"^[ <>+]?(\d)(\d)-(\d{1,2})$")


def _ber_body(raw) -> str:
    s = str(raw).strip().lstrip("*")
    if s[:2].upper() in ("CB", "VB", "LB"):
        s = s[2:]
    return s.strip()


def ber_value(raw) -> float | None:
    """BER from a *CB…/*VB… reply. The meter sends [flag][sign][2 digits]E[exponent]:
    'CB +38E-3' = 3.8·10⁻², 'VB<+10E-9' = <1.0·10⁻⁸ (verified). The older guess
    '23-4' (mantissa 2.3, exponent −4) is still accepted. Otherwise None."""
    if raw is None:
        return None
    s = _ber_body(raw)
    if not s or s.startswith("!"):
        return None
    m = _NUM_E.search(s)
    if m:
        return float(m.group(1).replace(",", "."))
    m = _MANT_EXP.match(s.replace(" ", ""))
    if m:
        return float(f"{m.group(1)}.{m.group(2)}e-{m.group(3)}")
    return None


def ber_bound(raw) -> str | None:
    """The sign before a BER: '<' — below the measurement limit (screen "<1.0E-7"),
    '>' — above it. Otherwise None. With '<' the value of ber_value() is an upper bound."""
    if raw is None:
        return None
    s = _ber_body(raw)
    return s[0] if s[:1] in ("<", ">") else None


def post_ber_name(system) -> str:
    return "LBER" if system == "DVB-S2" else "VBER"


# --- QPSK ↔ 8PSK pairs -------------------------------------------------------------------
def _brief(t: dict) -> dict:
    keys = ("freq_mhz", "pol", "sr", "system", "modulation", "fec", "lo_mhz", "lnb", "if_mhz", "band",
            "required_cn_db", "video", "mpeg2_sd", "verified")
    b = {k: t.get(k) for k in keys}
    b["channels"] = [c["name"] for c in t.get("channels", [])][:6]
    return b


def _diffs(a: dict, b: dict) -> list[dict]:
    """What else differs in a pair besides the modulation (codes for the page to translate)."""
    out = []
    if a["system"] != b["system"]:
        out.append({"k": "standard", "a": a["system"], "b": b["system"]})
    if a["sr"] != b["sr"]:
        out.append({"k": "sr", "a": a["sr"], "b": b["sr"]})
    if a["fec"] != b["fec"]:
        out.append({"k": "fec", "a": a["fec"], "b": b["fec"]})
    if a["pol"] != b["pol"]:
        out.append({"k": "pol", "a": a["pol"], "b": b["pol"]})
    if a.get("band") != b.get("band"):
        out.append({"k": "band"})
    return out


def pairs(sat: dict, alts: int = 4) -> list[dict]:
    """For every 8PSK transponder — the nearest QPSK ones at the same position.
    Best is the same polarisation and band, a close frequency and the same SR/FEC:
    fewer differences besides the modulation."""
    tps = [t for t in sat.get("transponders", []) if t.get("supported")]
    eight = [t for t in tps if t["modulation"] == "8PSK"]
    qpsk = [t for t in tps if t["modulation"] == "QPSK"]

    def score(e, q):
        return (q["pol"] != e["pol"]) * 1000 + (q.get("band") != e.get("band")) * 500 \
            + abs(q["freq_mhz"] - e["freq_mhz"]) / 10 + (q["sr"] != e["sr"]) * 5 + (q["fec"] != e["fec"]) * 2 \
            + (not q.get("channels")) * 50

    out = []
    for e in eight:
        cand = sorted(qpsk, key=lambda q: score(e, q))[:alts]
        if not cand:
            continue
        eb = _brief(e)
        options = []
        for q in cand:
            qb = _brief(q)
            options.append({"qpsk": qb, "df_mhz": round(q["freq_mhz"] - e["freq_mhz"], 2), "diffs": _diffs(qb, eb),
                            "d_req_cn": (round(e["required_cn_db"] - q["required_cn_db"], 1)
                                         if e.get("required_cn_db") is not None and q.get("required_cn_db") is not None else None)})
        out.append({"psk8": eb, "options": options})
    # Verified, MPEG-2 SD and channel-carrying 8PSK first, then by frequency.
    out.sort(key=lambda p: (not p["psk8"].get("verified"), not p["psk8"]["mpeg2_sd"], not p["psk8"]["channels"],
                            p["psk8"]["freq_mhz"]))
    return out


# --- channel recommendations for a lab session ------------------------------------------
# Transponder tables in lab manuals age quickly (a 2013 table is mostly wrong in 2026),
# so channels are picked from the fresh KingOfSat database: what the meter can demodulate
# and, preferably, what shows a free-to-air picture.
def _why_score(t: dict, mod: str, ref: dict | None = None) -> tuple[float, list[dict]]:
    s, why = 0.0, []
    if t.get("verified"):
        s += 100
        why.append({"k": "verified", "names": ", ".join(t["verified"])})
    fta_sd = [c["name"] for c in t.get("channels", []) if c.get("fta") and c.get("video") == "MPEG-2"]
    if fta_sd:
        s += 30
        why.append({"k": "fta_mpeg2", "names": ", ".join(fta_sd[:2])})
    elif t.get("fta_count"):
        s += 12
        why.append({"k": "fta_count", "n": t["fta_count"]})
    if mod == "QPSK" and t["system"] == "DVB-S":
        s += 15
        why.append({"k": "dvbs"})
    req = t.get("required_cn_db")
    if req is not None:
        s -= req * 2                      # the lower the C/N threshold, the easier the lock
        why.append({"k": "threshold", "db": req})
    sr = t.get("sr") or 0
    if sr >= 20000:
        s += 8                            # wide carriers: strong multiplexes, easy to spot in the spectrum
    elif sr < 5000:
        s -= 20
        why.append({"k": "narrow", "sr": sr})
    elif sr < 10000:
        s -= 8
    if t.get("beam") in ("U.K.", "Germany") or "Turkey" in (t.get("beam") or ""):
        s -= 40
        why.append({"k": "beam", "beam": t["beam"]})
    if ref is not None:
        if t["pol"] == ref["pol"] and t.get("band") == ref.get("band"):
            s += 10
            why.append({"k": "same_pol"})
        if t["sr"] == ref["sr"]:
            s += 3
    return s, why


def recommend(sat: dict, top: int = 5) -> dict:
    """Best candidates at a position: QPSK (optimum, constellation, EDGE) and 8PSK (comparison)."""
    tps = [t for t in sat.get("transponders", []) if t.get("supported") and t.get("channels")]
    out = {}
    q = sorted(((*_why_score(t, "QPSK"), t) for t in tps if t["modulation"] == "QPSK"), key=lambda x: -x[0])
    out["qpsk"] = [{**_brief(t), "score": round(s, 1), "why": w, "fta_count": t.get("fta_count")} for s, w, t in q[:top]]
    ref = q[0][2] if q else None
    e = sorted(((*_why_score(t, "8PSK", ref), t) for t in tps if t["modulation"] == "8PSK"), key=lambda x: -x[0])
    out["psk8"] = [{**_brief(t), "score": round(s, 1), "why": w, "fta_count": t.get("fta_count")} for s, w, t in e[:top]]
    return out


# --- measurement protocol -------------------------------------------------------------
KINDS = ("optimum", "edge", "qpsk", "8psk", "measure")
CSV_COLS = [("id", "col.no"), ("time", "col.time"), ("kind", "col.kind"), ("note", "col.note"), ("pos", "col.pos"),
            ("freq_mhz", "col.freq"), ("pol", "col.pol"), ("sr", "col.sr"), ("system", "col.system"),
            ("modulation", "col.modulation"), ("fec", "col.fec"), ("lo_mhz", "col.lo"), ("if_mhz", "col.if"), ("lnb", "col.lnb"),
            ("device_status", "col.device_status"), ("locked", "col.locked"), ("n", "col.n"), ("level", "col.level"),
            ("cn", "col.cn"), ("cn_min", "col.cn_min"), ("cn_max", "col.cn_max"), ("mer", "col.mer"), ("mer_min", "col.mer_min"),
            ("mer_max", "col.mer_max"), ("cber", "col.cber_raw"), ("cber_v", "col.cber"), ("post_name", "col.post_name"),
            ("vber", "col.post_ber_raw"), ("vber_v", "col.post_ber"), ("source", "col.source"), ("demo", "col.demo"),
            ("channels", "col.channels")]


def normalize_kind(kind) -> str:
    kind = (kind or "").lower() if isinstance(kind, str) else ""
    return kind if kind in KINDS else "measure"


def aggregate(samples: list[dict]) -> dict:
    """A series of readings (each: status + read()) → one row: mean/min/max, last BER."""
    def vals(k):
        return [s[k]["value"] for s in samples if (s.get(k) or {}).get("value") is not None]

    out = {"n": len(samples)}
    for k, name in (("LV", "level"), ("CN", "cn"), ("ME", "mer")):
        v = vals(k)
        out[name] = round(sum(v) / len(v), 2) if v else None
        if name != "level":
            out[name + "_min"] = min(v) if v else None
            out[name + "_max"] = max(v) if v else None
    known = [s["status"]["locked"] for s in samples if (s.get("status") or {}).get("locked") is not None]
    out["locked"] = None if not known else True if all(known) else False if not any(known) else "partial"
    last = samples[-1] if samples else {}
    out["device_status"] = (last.get("status") or {}).get("text")
    out["st"] = (last.get("status") or {}).get("st")
    for k, name in (("CB", "cber"), ("VB", "vber")):
        raw = (last.get(k) or {}).get("raw")
        out[name] = raw
        out[name + "_v"] = ber_value(raw)
    return out


class LabProtocol:
    """<data>/lab/protocol.jsonl (append-only, nothing is erased) + protocol.csv."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "lab"
        self.jsonl = self.dir / "protocol.jsonl"
        self.csv = self.dir / "protocol.csv"
        self.rows: dict[int, dict] = {}
        self.lock = threading.Lock()
        if self.jsonl.exists():
            for line in self.jsonl.read_text(encoding="utf-8").splitlines():
                try:
                    self._apply(json.loads(line))
                except (ValueError, KeyError, TypeError):
                    continue

    def _apply(self, ev: dict):
        if ev.get("deleted"):
            self.rows.pop(int(ev["id"]), None)
        else:
            ev["kind"] = normalize_kind(ev.get("kind"))
            self.rows[int(ev["id"])] = ev

    def _append(self, ev: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self._apply(ev)
        from .foundlog import write_csv_copy
        write_csv_copy(self._write_csv, self.csv, self.jsonl)

    def add(self, row: dict) -> dict:
        with self.lock:
            row = dict(row)
            row["id"] = max(self.rows, default=0) + 1
            row["time"] = row.get("time") or datetime.now().isoformat(timespec="seconds")
            row["kind"] = normalize_kind(row.get("kind"))
            row["post_name"] = post_ber_name(row.get("system"))
            self._append(row)
            return row

    def delete(self, row_id: int):
        """Hide a row: a marker is appended to the jsonl, the original record stays."""
        with self.lock:
            if int(row_id) in self.rows:
                self._append({"id": int(row_id), "deleted": True, "time": datetime.now().isoformat(timespec="seconds")})

    def list(self) -> list[dict]:
        with self.lock:
            return [self.rows[k] for k in sorted(self.rows)]

    def _write_csv(self):
        def cell(r, c):
            v = r.get(c)
            if c == "channels":
                return ", ".join(v or [])
            if c == "kind":
                return tr("kind." + v) if v else ""
            if c in ("locked", "demo"):
                return tr("yes") if v is True else tr("no") if v is False else tr("partial") if v == "partial" else v
            return v

        with open(self.csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow([tr(h) for _, h in CSV_COLS])
            for k in sorted(self.rows):
                w.writerow([cell(self.rows[k], c) for c, _ in CSV_COLS])


class LabJournal:
    """The course of a lab session in the user's own words ("photographed the
    constellation", "picture is fine", "moved the dish back"). <data>/lab/journal.jsonl
    (append-only) + journal.txt for reading. No meter readings here — they belong to
    the protocol; only what the user said or did."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "lab"
        self.jsonl = self.dir / "journal.jsonl"
        self.txt = self.dir / "journal.txt"
        self.lock = threading.Lock()

    def add(self, text: str, source: str = "window", current: dict | None = None) -> dict:
        text = " ".join(str(text).split())[:1000]
        if not text:
            raise ValueError(tr("err.empty_note"))
        ev = {"time": datetime.now().isoformat(timespec="seconds"), "text": text, "source": source}
        if current and current.get("freq_mhz"):
            ev["channel"] = f"{current.get('pos') or '?'} {current['freq_mhz']} {current.get('pol') or ''}".strip()
        with self.lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            with open(self.txt, "a", encoding="utf-8") as f:
                f.write(f"{ev['time'].replace('T', ' ')}  [{ev.get('channel', '—')}]  {text}\n")
        return ev

    def list(self) -> list[dict]:
        if not self.jsonl.exists():
            return []
        out = []
        for line in self.jsonl.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
