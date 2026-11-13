"""Transponder database from KingOfSat: parsing and updating (used by the window and tools/build_satellites.py).

Two KingOfSat files are used per orbital position:
  dl.php?pos=16E — transponder list (frequency, polarisation, SR, FEC, system, modulation);
  pos-16E        — the page with channels (name, video MPEG-2/MPEG-4/HEVC, encryption).
The data is dated by the build day.

Updating from the window (update_all) writes to <data>/satellites/ — the shipped
databases in tvxlink/satellites/ stay as a fallback. A suspiciously small new
database (broken parsing, changed site layout) is not saved.
"""

from __future__ import annotations

import html
import json
import math
import re
import time
import urllib.request
from datetime import date
from pathlib import Path

from .i18n import tr

PACKAGE_DIR = Path(__file__).resolve().parent / "satellites"

POSITIONS = {
    "9E": "Eutelsat 9B · 9°E",
    "13E": "Hot Bird 13F/13G · 13°E",
    "16E": "Eutelsat 16D · 16°E",
    "19.2E": "Astra 1KR/1L/1M/1N · 19,2°E",
    "23.5E": "Astra 3B/3C · 23,5°E",
    "28.2E": "Astra 2E/2F/2G · 28,2°E",
    "42E": "Türksat 6A/5A/5B · 42°E",
}

FEC_CODES = {"12": "1/2", "23": "2/3", "34": "3/4", "35": "3/5", "45": "4/5", "56": "5/6",
             "67": "6/7", "78": "7/8", "89": "8/9", "910": "9/10", "14": "1/4", "13": "1/3", "25": "2/5"}

# QEF threshold Es/N0, dB. DVB-S2: ETSI EN 302 307-1, table 13 (ideal channel).
ESN0_DVBS2 = {
    ("QPSK", "1/4"): -2.35, ("QPSK", "1/3"): -1.24, ("QPSK", "2/5"): -0.30, ("QPSK", "1/2"): 1.00,
    ("QPSK", "3/5"): 2.23, ("QPSK", "2/3"): 3.10, ("QPSK", "3/4"): 4.03, ("QPSK", "4/5"): 4.68,
    ("QPSK", "5/6"): 5.18, ("QPSK", "8/9"): 6.20, ("QPSK", "9/10"): 6.42,
    ("8PSK", "3/5"): 5.50, ("8PSK", "2/3"): 6.62, ("8PSK", "3/4"): 7.91, ("8PSK", "5/6"): 9.35,
    ("8PSK", "8/9"): 10.69, ("8PSK", "9/10"): 11.03,
}
# DVB-S: ETSI EN 300 421, Eb/N0 after Viterbi+RS (QEF); Es/N0 = Eb/N0 + 10·lg(2·R·188/204).
EBN0_DVBS = {"1/2": 4.5, "2/3": 5.0, "3/4": 5.5, "5/6": 6.0, "7/8": 6.4}
IMPLEMENTATION_MARGIN_DB = 1.0   # margin for a real demodulator
HD_RE = re.compile(r"\b(HD|UHD|FHD|4K|8K)\b", re.I)

# TV EXPLORER II / II+ capabilities (user manual, specifications).
LIMITS = {("DVB-S", "QPSK"): (2000, 45000), ("DVB-S2", "QPSK"): (2000, 33000), ("DVB-S2", "8PSK"): (2000, 30000)}
FEC_ALLOWED = {
    ("DVB-S", "QPSK"): {"1/2", "2/3", "3/4", "5/6", "7/8"},
    ("DVB-S2", "QPSK"): {"1/4", "1/3", "2/5", "1/2", "3/5", "2/3", "3/4", "4/5", "5/6", "8/9", "9/10"},
    ("DVB-S2", "8PSK"): {"3/5", "2/3", "3/4", "5/6", "8/9", "9/10"},
}


def qef_esn0(system: str, mod: str, fec: str):
    """ETSI QEF threshold Es/N0, dB, without margin; None if not in the tables."""
    if system == "DVB-S":
        if fec not in EBN0_DVBS:
            return None
        num, den = (int(x) for x in fec.split("/"))
        return EBN0_DVBS[fec] + 10 * math.log10(2 * num / den * 188 / 204)
    return ESN0_DVBS2.get((mod, fec))


def required_cn(system: str, mod: str, fec: str):
    esn0 = qef_esn0(system, mod, fec)
    return None if esn0 is None else round(esn0 + IMPLEMENTATION_MARGIN_DB, 1)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (TVXLink; +https://github.com/RE22GDV/tvxlink)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_ini(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        m = re.match(r"^\d+=(\d+),([HVLR]),(\d+),(\d+),([^,]+),([^,\s]+)", line.strip())
        if not m:
            continue
        f, pol, sr, fec, system, mod = m.groups()
        system = "DVB-S2" if system.upper().startswith("S2") else "DVB-S"
        out.append({"freq_mhz": int(f), "pol": pol, "sr": int(sr), "fec": FEC_CODES.get(fec, fec),
                    "system": system, "modulation": mod.upper()})
    return out


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_page(text: str) -> list[dict]:
    """Transponders of a pos-XX page: frequency, polarisation, SR, standard, channels."""
    result = []
    blocks = re.split(r'<table class="frequencies-table', text)[1:]
    for b in blocks:
        m = re.search(r'<td class="n?bld">\s*([\d.]+)\s*([HVLR])', b)
        if not m:
            continue
        head = b.split("</tr>", 1)[0]
        sr = re.search(r'</td><td>[^<]*</td><td><a class="bld">(\d+)</a>', head)
        sat = re.search(r'<a class="bld">([^<]+)</a>', head)
        std = re.search(r'kos-standard-(dvbs2x|dvbs2|dvbs)', head)
        beam = re.search(r'href="beams\.php[^"]*">([^<]+)</a>', head)
        channels = []
        for row in re.findall(r'<tr data-channel-id="[^"]*">(.*?)</tr>', b, re.S):
            name_m = re.search(r'<td class="ch">(.*?)</td>', row, re.S)
            if not name_m:
                continue
            name = _clean(re.sub(r"<p>.*", "", name_m.group(1), flags=re.S))
            vp = re.search(r'class="w3-hide-small vpid">(.*?)</td>', row, re.S)
            vcell = vp.group(1) if vp else ""
            if "hevc" in vcell:
                video = "HEVC"
            elif "mpeg4" in vcell:
                video = "MPEG-4"
            elif re.search(r"\d", _clean(vcell)):
                video = "MPEG-2"
            else:
                video = "radio/data"
            enc = re.search(r'encryption-label">(.*?)</span>', row, re.S)
            # KingOfSat writes 'Clear' for free-to-air channels and CA system names otherwise.
            enc_text = _clean(enc.group(1)) if enc else ""
            channels.append({"name": name, "video": video, "fta": enc_text.lower() in ("", "clear", "free", "fta"),
                             "hd": video == "HEVC" or bool(HD_RE.search(name))})
        result.append({"freq": float(m.group(1)), "pol": m.group(2), "sr": int(sr.group(1)) if sr else None,
                       "beam": html.unescape(beam.group(1)) if beam else "",
                       "satellite": sat.group(1) if sat else "", "standard_page": std.group(1) if std else "",
                       "channels": channels})
    return result


def match_page(tp: dict, page: list[dict]) -> dict:
    cands = [e for e in page if e["pol"] == tp["pol"] and abs(e["freq"] - tp["freq_mhz"]) <= 1.0]
    if not cands:
        return {}
    exact = [e for e in cands if e["sr"] is not None and abs(e["sr"] - tp["sr"]) <= 2]
    return min(exact or cands, key=lambda e: abs(e["freq"] - tp["freq_mhz"]))


def build(pos: str, ini_text: str, page_text: str) -> dict:
    tps = parse_ini(ini_text)
    page = parse_page(page_text)
    seen = set()
    out = []
    for tp in tps:
        dedup = (tp["freq_mhz"], tp["pol"], tp["sr"])
        if dedup in seen:
            continue
        seen.add(dedup)
        info = match_page(tp, page)
        if info.get("freq"):
            tp["freq_mhz"] = round(info["freq"], 2)
        if info.get("standard_page") == "dvbs2x":
            tp["system"] = "DVB-S2X"
        lim = LIMITS.get((tp["system"], tp["modulation"]))
        ok = bool(lim and lim[0] <= tp["sr"] <= lim[1] and tp["fec"] in FEC_ALLOWED[(tp["system"], tp["modulation"])])
        band = "high" if tp["freq_mhz"] >= 11700 else "low"
        lo = 10600 if band == "high" else 9750
        chans = info.get("channels", [])
        videos = sorted({c["video"] for c in chans if c["video"] != "radio/data"})
        mpeg2_sd = [c["name"] for c in chans if c["video"] == "MPEG-2" and not c["hd"]]
        out.append({
            **tp,
            "satellite": info.get("satellite", ""),
            "beam": info.get("beam", ""),
            "band": band, "lo_mhz": lo, "if_mhz": tp["freq_mhz"] - lo,
            "lnb": ("13V" if tp["pol"] in "VR" else "18V") + ("+22kHz" if band == "high" else ""),
            "required_cn_db": required_cn(tp["system"], tp["modulation"], tp["fec"]),
            "supported": ok,
            "video": videos,
            "mpeg2_sd": mpeg2_sd,
            "fta_count": sum(1 for c in chans if c["fta"]),
            "channels": chans,
        })
    out.sort(key=lambda t: (t["freq_mhz"], t["pol"]))
    return {"position": pos, "name": POSITIONS.get(pos, pos), "source": "kingofsat.net",
            "built": date.today().isoformat(), "transponders": out}


def check(new: dict, old: dict | None) -> str | None:
    """Why the new database is rejected, or None if it looks plausible."""
    tps = new["transponders"]
    if len(tps) < 10:
        return tr("satdb.too_few", n=len(tps))
    if not any(t["channels"] for t in tps):
        return tr("satdb.no_channels")
    if old and len(tps) < 0.5 * len(old.get("transponders", [])):
        return tr("satdb.shrunk", n=len(tps), old=len(old["transponders"]))
    return None


def update_all(dest_dir: Path, positions=None, old: dict | None = None, progress=None, pause: float = 1.5) -> list[dict]:
    """Download KingOfSat for every position, build, check and save to dest_dir.

    old — current databases {pos: data} for comparison; progress(pos, i, n, step) — progress
    with step "download" / "done". Returns a report per position: before/after, changes, error.
    """
    dest_dir = Path(dest_dir)
    raw = dest_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    positions = list(positions or POSITIONS)
    report = []
    for i, pos in enumerate(positions):
        prev = (old or {}).get(pos)
        row = {"pos": pos, "name": POSITIONS.get(pos, pos), "before": len(prev["transponders"]) if prev else None,
               "before_built": prev.get("built") if prev else None}
        try:
            if progress:
                progress(pos, i, len(positions), "download")
            ini = fetch(f"https://en.kingofsat.net/dl.php?pos={pos}")
            time.sleep(pause)
            page = fetch(f"https://en.kingofsat.net/pos-{pos}")
            time.sleep(pause)
            (raw / f"dl_{pos}.bin").write_text(ini, encoding="utf-8")
            (raw / f"kos_{pos}.html").write_text(page, encoding="utf-8")
            data = build(pos, ini, page)
            why = check(data, prev)
            if why:
                row.update(ok=False, error=why)
            else:
                (dest_dir / f"{pos}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                row.update(ok=True, after=len(data["transponders"]), built=data["built"], **diff(prev, data))
        except Exception as e:  # noqa: BLE001 — network/site: report this position, go on with the rest
            row.update(ok=False, error=f"{type(e).__name__}: {e}")
        report.append(row)
    if progress:
        progress(None, len(positions), len(positions), "done")
    return report


def diff(old: dict | None, new: dict) -> dict:
    """What changed: new / gone transponders (by frequency ±1, polarisation, SR)."""
    if not old:
        return {"added": len(new["transponders"]), "removed": 0, "changed": 0}
    def k(t):
        return (round(t["freq_mhz"]), t["pol"], t["sr"])
    a = {k(t): t for t in old["transponders"]}
    b = {k(t): t for t in new["transponders"]}
    changed = sum(1 for key in a.keys() & b.keys()
                  if (a[key]["fec"], a[key]["modulation"], a[key]["system"]) != (b[key]["fec"], b[key]["modulation"], b[key]["system"]))
    return {"added": len(b.keys() - a.keys()), "removed": len(a.keys() - b.keys()), "changed": changed}
