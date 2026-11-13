"""Parser for TV EXPLORER II / II+ datalogger files (*.dl).

The format was reconstructed from NetUpdate 3.16 (built-in PKTools, "Advanced
utilities"): functions 0x43a1c0 (file) and 0x43cbd0 (channel plan), labels
from English.xml. All numbers are little-endian.

    0          0xDA                      signature
    1          n                         datalogger name length
    2..        name[n]
    n+2        L (3 bytes)               channel plan block length
    n+5        plan[L]                   see parse_plan()
    P=n+L:
    P+5        k                         record header length
    P+6..P+21  16 characters             identifier
    P+0x16..   day, month, year(2), hour, minute   (if k is large enough)
    n+L+k+6    channel table: for every channel
                 m, types[m], IF mode, local oscillator (3 bytes, kHz)
    then       number of test points (2 bytes)
    and records: name length, name, units (1 dBuV, 2 dBmV, 3 dBm),
               3 measurement bytes for every channel and type.

A measurement (3 bytes): flag (0x80 ">", 0x40 "<", 0xC0 "!!" — no value) and the
value. Levels, C/N, MER etc. are signed int16 in tenths; BER is a mantissa in
tenths and an exponent (x.xE-n); offset is int16 kHz; MPEG-2 is 0/1.

From a terminal:  python3 -m tvxlink.dlfile file.dl [--csv out.csv]
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

MAGIC = 0xDA

# Measurement type → (label, units, value kind). Labels as in NetUpdate.
MEASURE_TYPES = {
    0: ("Level", "level", "dB10"),
    1: ("V/A", "dB", "dB10"),
    2: ("C/N", "dB", "dB10"),
    3: ("POWER", "level", "dB10"),
    4: ("MER", "dB", "dB10"),
    5: ("CBER", "", "ber"),
    6: ("VBER", "", "ber"),
    7: ("OFFSET(kHz)", "kHz", "int"),
    8: ("MPEG-2", "", "lock"),
    9: ("MARGIN", "dB", "dB10"),
    10: ("REF ICT", "level", "dB10"),
    11: ("C/N Auto", "dB", "dB10"),
    12: ("SER", "", "ber"),
    13: ("LBER", "", "ber"),
}
LEVEL_UNITS = {1: "dBµV", 2: "dBmV", 3: "dBm"}
LEVEL_TYPES = {0, 3, 10}

DIGITAL_STD = {0: "DVB-T (COFDM)", 1: "DVB-C (QAM)", 2: "DVB-S (QPSK)", 4: "DSS", 5: "8VSB",
               6: "QAM-B", 7: "DVB-S2", 9: "DVB-T2", 10: "ISDB-T"}
ANALOG_COLOR = {1: "PAL", 2: "SECAM", 3: "NTSC"}
LNB_POWER = {0: "EXTERNAL", 1: "5V", 2: "13V", 3: "15V", 4: "18V", 5: "24V"}
CODE_RATE = {0: "1/2", 1: "2/3", 2: "3/4", 3: "4/5", 4: "5/6", 5: "7/8", 6: "6/7"}
GUARD = {0: "1/4", 1: "1/8", 2: "1/16", 3: "1/32"}
QAM = {0: "16-QAM", 1: "32-QAM", 2: "64-QAM", 3: "128-QAM", 4: "256-QAM"}


class DLFormatError(ValueError):
    pass


def _u16(b, i):
    return b[i] | b[i + 1] << 8


def _u24(b, i):
    return b[i] | b[i + 1] << 8 | b[i + 2] << 16


def _u32(b, i):
    return b[i] | b[i + 1] << 8 | b[i + 2] << 16 | b[i + 3] << 24


def _i16(b, i):
    v = _u16(b, i)
    return v - 0x10000 if v & 0x8000 else v


def _describe_attrs(attrs: bytes) -> tuple[str | None, list[str]]:
    """5-byte channel attributes → standard and a list of parameters."""
    std, info = None, []
    for g in range(0, len(attrs) - 4, 5):
        a, b = attrs[g], attrs[g + 1]
        if a == 1:
            std = "Analog " + ANALOG_COLOR.get(b, "")
        elif a == 2:
            std = DIGITAL_STD.get(b, f"digital ({b})")
        elif a == 3 and b in LNB_POWER:
            info.append("LNB " + LNB_POWER[b])
        elif a == 4:
            info.append(f"SR {_u32(attrs, g + 1)}")
        elif a == 6 and b in (0, 1):
            info.append("spectral inv. " + ("on" if b else "off"))
        elif a == 7 and b in QAM:
            info.append(QAM[b])
        elif a == 8 and b in (0, 1):
            info.append("2K" if b == 0 else "8K")
        elif a == 9 and b in GUARD:
            info.append("GI " + GUARD[b])
        elif a == 11 and b in CODE_RATE:
            info.append("FEC " + CODE_RATE[b])
    return std, info


def parse_plan(plan: bytes) -> dict:
    """Channel plan block (NetUpdate3, 0x43cbd0)."""
    if len(plan) < 3:
        raise DLFormatError("channel plan block too short")
    nch = _u16(plan, 0)
    i = 0
    while 3 + i + 1 < len(plan) and not (plan[3 + i] == 0xFF and plan[4 + i] == 0xFF):
        i += 1
    channels = []
    off = 0
    limit = len(plan) - i - 8
    while off < limit and len(channels) < nch:
        base = i + off
        if base + 0x18 > len(plan):
            break
        name = plan[base + 8:base + 16].decode("latin-1").replace(" ", "").strip("\x00")
        f1 = _u32(plan, base + 0x10)
        f2 = _u32(plan, base + 0x14)
        s = 0
        while base + 0x19 + s < len(plan) and not (plan[base + 0x18 + s] == 0xFF and plan[base + 0x19 + s] == 0xFF):
            s += 1
        std, info = _describe_attrs(plan[base + 0x18:base + 0x18 + s])
        channels.append({"name": name, "freq_khz": f2, "freq2_khz": f1, "standard": std, "params": info})
        off += s + 0x1A
    return {"channel_count": nch, "flag": plan[2], "channels": channels}


def _decode(mtype: int, raw: bytes, units: int) -> dict:
    label, unit, kind = MEASURE_TYPES.get(mtype, (f"type {mtype}", "", "dB10"))
    if unit == "level":
        unit = LEVEL_UNITS.get(units, "dBµV")
    f = raw[0] & 0xC0 if mtype in LEVEL_TYPES else raw[0]
    flag = {0x80: ">", 0x40: "<", 0xC0: "!!"}.get(f, "")
    out = {"type": mtype, "label": label, "unit": unit, "flag": flag, "value": None, "text": ""}
    if kind == "lock":
        out["text"] = {0: "NOT LOCKED", 1: "LOCKED"}.get(raw[0], f"? ({raw[0]})")
        out["flag"] = ""
        return out
    if flag == "!!":
        out["text"] = "!!"
        return out
    if kind == "dB10":
        out["value"] = round(_i16(raw, 1) / 10.0, 1)
        out["text"] = f"{flag}{out['value']:.1f}"
    elif kind == "int":
        out["value"] = _i16(raw, 1)
        out["text"] = f"{flag}{out['value']}"
    elif kind == "ber":
        mant = raw[1] / 10.0
        exp = raw[2] - 0x100 if raw[2] & 0x80 else raw[2]
        out["value"] = mant * 10.0 ** (-abs(exp))
        out["text"] = f"{flag}{mant:.1f}E-{abs(exp)}"
    return out


def parse_dl(data: bytes) -> dict:
    b = data
    if len(b) < 8 or b[0] != MAGIC:
        raise DLFormatError("not a TV EXPLORER II datalogger file (no 0xDA signature)")
    n = b[1]
    name = b[2:2 + n].decode("latin-1")
    L = _u24(b, n + 2)
    plan = parse_plan(b[n + 5:n + 5 + L])
    nch = plan["channel_count"]

    p = n + L
    k = b[p + 5]
    header = {"id": "", "datetime": None}
    if k:
        header["id"] = b[p + 6:p + 22].decode("latin-1").strip("\x00 ")
    if k > 0x16:
        day, month, year = b[p + 0x16], b[p + 0x17], _u16(b, p + 0x18)
        hour, minute = b[p + 0x1A], b[p + 0x1B]
        header["datetime"] = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"

    # Table of measurement types per channel.
    t = n + L + k + 6
    off = 0
    chan_types = []
    for ci in range(nch):
        m = b[t + off]
        types = list(b[t + off + 1:t + off + 1 + m])
        lo_mode = b[t + off + 1 + m]
        lo = _u24(b, t + off + 2 + m)
        chan_types.append(types)
        if ci < len(plan["channels"]):
            ch = plan["channels"][ci]
            ch["types"] = types
            # As in NetUpdate: frequency to IF using the local oscillator.
            if lo_mode in (1, 3):
                ch["freq_khz"] = ch["freq_khz"] - lo
            elif lo_mode in (2, 4):
                ch["freq_khz"] = lo - ch["freq_khz"]
            ch["lo_khz"] = lo if lo_mode else None
        off += 5 + m

    r = n + L + k + off
    ntests = _u16(b, r + 6)
    x = r + 8
    tests = []
    for _ in range(ntests):
        nl = b[x]
        tname = b[x + 1:x + 1 + nl].decode("latin-1")
        units = b[x + 1 + nl]
        pos = x + 2 + nl
        rows = []
        for ci, types in enumerate(chan_types):
            ch = plan["channels"][ci] if ci < len(plan["channels"]) else {"name": f"#{ci + 1}"}
            meas = []
            for mt in types:
                meas.append(_decode(mt, b[pos:pos + 3], units))
                pos += 3
            rows.append({"channel": ch.get("name") or f"#{ci + 1}", "freq_mhz": (ch.get("freq_khz") or 0) / 1000.0,
                         "standard": ch.get("standard"), "measures": meas})
        tests.append({"name": tname, "units": LEVEL_UNITS.get(units, "dBµV"), "rows": rows})
        x = pos
    return {"name": name, "header": header, "plan": plan, "tests": tests}


def to_csv(parsed: dict, path: Path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Test point", "Channel", "Frequency, MHz", "Standard", "Measurement", "Value", "Units"])
        for t in parsed["tests"]:
            for row in t["rows"]:
                for m in row["measures"]:
                    w.writerow([t["name"], row["channel"], f"{row['freq_mhz']:.2f}", row["standard"] or "",
                                m["label"], m["text"], m["unit"]])


# --- tools for unknown files -----------------------------------------------

def hexdump(data: bytes, limit: int = 4096) -> str:
    out = []
    for off in range(0, min(len(data), limit), 16):
        row = data[off:off + 16]
        hx = " ".join(f"{x:02x}" for x in row)
        asc = "".join(chr(x) if 32 <= x < 127 else "." for x in row)
        out.append(f"{off:06x}  {hx:<47}  {asc}")
    if len(data) > limit:
        out.append(f"… {len(data) - limit} more bytes")
    return "\n".join(out)


def ascii_strings(data: bytes, min_len: int = 3) -> list[tuple[int, str]]:
    return [(m.start(), m.group().decode("ascii"))
            for m in re.finditer(rb"[\x20-\x7e]{%d,}" % min_len, data)]


def main(argv=None):
    args = list(argv or sys.argv[1:])
    csv_out = None
    if "--csv" in args:
        i = args.index("--csv")
        csv_out = Path(args[i + 1])
        del args[i:i + 2]
    for p in args:
        data = Path(p).read_bytes()
        try:
            d = parse_dl(data)
        except (DLFormatError, IndexError) as e:
            print(f"{p}: {e}\n{hexdump(data)}")
            continue
        print(f"== {p}: «{d['name']}»  {d['header']['datetime'] or ''}  {d['header']['id']}")
        for t in d["tests"]:
            print(f"-- point '{t['name']}' ({t['units']})")
            for row in t["rows"]:
                vals = ", ".join(f"{m['label']} {m['text']} {m['unit']}".strip() for m in row["measures"])
                print(f"   {row['channel']:>8}  {row['freq_mhz']:9.2f} MHz  {row['standard'] or '':14}  {vals}")
        if csv_out:
            to_csv(d, csv_out)
            print("CSV:", csv_out)


if __name__ == "__main__":
    main()
