"""Regenerate docs/screenshots/*.png from the real data of the 2026-11-12 meter session.

    python tools/screenshots.py [--out docs/screenshots] [--chrome PATH]

Every number on the screenshots comes from docs/validation/2026-11-12 (logs, sessions,
protocol, found channels, self-test). The program runs in demo mode only so that the
window has a "connection"; the values are put back exactly as the meter reported them:
  * the aiming trend is decoded from the hex exchange log (12:16:57–12:19:07, 19.2°E);
  * the spectrum is the meter's own sweep around 13°E 10992 V, with the real lock result;
  * the protocol, journal, threshold run and QPSK/8PSK pair are the saved session files.
Free-text notes written by the user are translated into the screenshot language.
Needs Node.js 22+ and Google Chrome / Chromium.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tvxlink.device import parse_measure  # noqa: E402

VAL = ROOT / "docs" / "validation" / "2026-11-12"
NOTES = {   # protocol notes and journal entries of the real session, translated (numbers untouched)
    "uk": {
        "p1": "19.2E Canal Sur, антена в піку наведення (C/N 8,4 дБ, MER 7,5 дБ о 12:18:55); на екрані приладу була картинка",
        "p2": "межа: відведення антени за азимутом у бік «+» (останній вимір із захопленням перед зривом)",
        "p3": "межа: відведення антени за азимутом у бік «−» (останній вимір із захопленням перед зривом)",
        "j": ["Навівся на 19.2E, прилад захопив 11156 V Canal Sur (MPEG-2 TS DVB-S), ПЧ прилад підстроїв до 1408,0 МГц",
              "На екрані приладу картинка Canal Sur (режим ТБ): у ньому прилад відповідає NAK на *?LV/CN/ME/CB/VB",
              "Сузір’я Canal Sur (19.2E 11156 V, QPSK) сфотографовано",
              "Межа: антену відводив за азимутом спершу в «+», потім у «−», два зриви захоплення; повернув антену в оптимум",
              "Рядок №4 приховано: прилад лишився в режимі DVB-S2 (SA7), *SA3 не надсилався. Виправлено, пару переміряно (№6, №7)",
              "8PSK 11670.75 H: екран вимірів і сузір’я (8 точок) сфотографовано. Лабораторну завершено"],
    },
    "en": {
        "p1": "19.2E Canal Sur, dish at the aiming peak (C/N 8.4 dB, MER 7.5 dB at 12:18:55); the picture was on the meter screen",
        "p2": "threshold: dish moved off in azimuth towards \"+\" (last locked reading before the loss)",
        "p3": "threshold: dish moved off in azimuth towards \"−\" (last locked reading before the loss)",
        "j": ["Aimed at 19.2E, the meter locked 11156 V Canal Sur (MPEG-2 TS DVB-S), the meter fine-tuned the IF to 1408.0 MHz",
              "The meter shows the Canal Sur picture (TV mode): in this mode it answers NAK to *?LV/CN/ME/CB/VB",
              "Canal Sur constellation (19.2E 11156 V, QPSK) photographed",
              "Threshold: dish moved in azimuth first to \"+\", then to \"−\", two losses of lock; dish back at the optimum",
              "Row #4 hidden: the meter stayed in DVB-S2 mode (SA7), *SA3 was not sent. Fixed, pair measured again (#6, #7)",
              "8PSK 11670.75 H: measurement screen and constellation (8 points) photographed. Lab finished"],
    },
}


def aim_points() -> list[dict]:
    """LV / C/N / MER triples of the aiming window, decoded from the hex exchange log."""
    rows, cur, buf, ts = [], None, b"", None
    for line in (VAL / "logs" / "session_20261112_120740.log").read_text(encoding="utf-8").splitlines():
        m = re.match(r"(\d\d:\d\d:\d\d\.\d+)\s+(>>|<<)\s+(.*)", line)
        if not m:
            continue
        t, d, rest = m.groups()
        if d == ">>":
            if cur and buf:
                txt = bytes(b for b in buf if 32 <= b < 127).decode()
                mm = re.search(r"\*(LV|CN|ME)([^*]*)", txt)
                if mm:
                    rows.append((ts, cur, mm.group(1) + mm.group(2)))
            cur, buf, ts = rest.replace("\\r", "").strip(), b"", t
        else:
            try:
                buf += bytes.fromhex(rest)
            except ValueError:
                pass
    pts, last = [], {}
    for t, cmd, rep in rows:
        last[cmd[2:4]] = parse_measure(cmd, rep).get("value")
        if cmd == "*?ME" and {"LV", "CN"} <= set(last):
            pts.append({"t": t, "lv": last["LV"], "cn": last["CN"], "me": last["ME"]})
            last = {}
    return [p for p in pts if "12:16:57" <= p["t"] <= "12:19:08"]


def edge_run() -> dict:
    items = json.loads((VAL / "sessions" / "20261112_123022_edge" / "result.json").read_text(encoding="utf-8"))
    x = {"edge": None, "loss": None, "losses": 0, "min_locked_cn": None, "best_cn": None}
    last, prev = None, None
    for it in items:
        if it["locked"]:
            last = it
            if it["cn"] is not None:
                x["min_locked_cn"] = it["cn"] if x["min_locked_cn"] is None else min(x["min_locked_cn"], it["cn"])
                x["best_cn"] = it["cn"] if x["best_cn"] is None else max(x["best_cn"], it["cn"])
        elif prev is not None and prev["locked"] and last is not None:
            x["edge"], x["loss"] = last, it
            x["losses"] += 1
        prev = it
    return {"items": items, "extra": x}


def build_data(data: Path, lang: str) -> dict:
    """A data folder for the window: real protocol (notes translated), journal, found log, sessions."""
    (data / "lab").mkdir(parents=True)
    rows = [json.loads(l) for l in (VAL / "lab" / "protocol.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for r in rows:
        if r.get("note") and r.get("id") in (1, 2, 3):
            r["note"] = NOTES[lang][f"p{r['id']}"]
    (data / "lab" / "protocol.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    jr = [json.loads(l) for l in (VAL / "lab" / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    for r, text in zip(jr, NOTES[lang]["j"]):
        r["text"] = text
    (data / "lab" / "journal.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in jr) + "\n", encoding="utf-8")
    shutil.copytree(VAL / "found", data / "found")
    shutil.copytree(VAL / "sessions", data / "sessions")
    for s in (VAL / "selftest" / "20261112_104147" / "sessions").iterdir():
        if s.is_dir():
            shutil.copytree(s, data / "sessions" / s.name)
    report = json.loads((VAL / "selftest" / "20261112_104147" / "report.json").read_text(encoding="utf-8"))
    scans = {}
    for d in (data / "sessions").glob("*_scan"):
        for it in json.loads((d / "result.json").read_text(encoding="utf-8")):
            scans[f"{it['pos']}|{it['freq_mhz']}|{it['pol']}|{it['sr']}"] = it
    pairs = [i for d in sorted((data / "sessions").glob("*_pair")) for i in json.loads((d / "result.json").read_text(encoding="utf-8"))]
    return {"aim": aim_points(), "edge": edge_run(), "series": report["data"]["measure_series"][-1], "scans": scans,
            "pair": pairs[-2:], "info": {"model": "TV EXPLORER II+", "firmware": "1.30.1090.280", "id": None},
            "usb": "0bda:b009\n8086:9d2f\n04f2:b5d5\n0451:3410  "}


def wait_up(port: int, seconds: float = 20) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1)
            return
        except OSError:
            time.sleep(0.3)
    raise SystemExit("the TVXLink server did not start")


def find_chrome(given: str | None) -> str:
    for c in [given, os.environ.get("CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"),
              r"C:\Program Files\Google\Chrome\Application\chrome.exe"]:
        if c and os.path.exists(c):
            return c
    raise SystemExit("Chrome / Chromium not found (--chrome PATH)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Regenerate the README screenshots from the real 2026-11-12 data")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "screenshots")
    ap.add_argument("--chrome")
    ap.add_argument("--only", help="comma-separated shot names")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    chrome = find_chrome(a.chrome)
    for lang, port in (("uk", 8791), ("en", 8792)):
        tmp = Path(tempfile.mkdtemp(prefix=f"tvxlink-shots-{lang}-"))
        scen = build_data(tmp / "data", lang)
        (tmp / "scenario.json").write_text(json.dumps(scen, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "TVXLINK_DATA": str(tmp / "data"), "TVXLINK_STATE": str(tmp / "state"), "PYTHONUTF8": "1"}
        srv = subprocess.Popen([sys.executable, "-m", "tvxlink", "--no-browser", "--port", str(port), "--lang", lang],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_up(port)
            subprocess.run(["node", str(ROOT / "tools" / "screenshots.mjs"), str(port), str(tmp / "scenario.json"),
                            str(a.out), chrome, lang, a.only or ""], check=True)
        finally:
            srv.terminate()
            srv.wait(10)
            shutil.rmtree(tmp, ignore_errors=True)
    print("screenshots →", a.out)


if __name__ == "__main__":
    main()
