"""High-level control of a PROMAX TV EXPLORER II / II+.

The command set comes from NetUpdate4.exe (EXPLORER-II family in Equipment.xml):
  *?NA — model name      *?VE — firmware version      *?ID — identifier
Measurement queries and reply parsing come from the NetUpdate monitoring windows
(functions 0x41d610…0x41eaa0), see parse_measure(). Tuning commands come from
RM-404 v2.60 (PROMAX monitoring software for the TV EXPLORER series).
Everything marked "verified" was checked on a real TV EXPLORER II+ (firmware
1.30.1090.280) on 2026-11-12, see docs/TESTING.md.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .i18n import tr
from .protocol import PromaxLink, ProtocolError
from .tusb3410 import TUSB3410Serial, DeviceNotFound, list_usb_devices

BAUD = 115200  # <Baud> of the EXPLORER-II family in Equipment.xml (verified)

IDENT_COMMANDS = {
    "model": "*?NA",
    "firmware": "*?VE",
    "id": "*?ID",      # TV EXPLORER II+ 1.30 answers NAK (verified): optional
}

# Queries of the NetUpdate monitoring windows; the second item is the label key.
MEASURE_COMMANDS = [
    ("*?FR", "meas.freq"),
    ("*?LV", "meas.level"),
    ("*?CN", "meas.cn"),
    ("*?ME", "meas.mer"),
    ("*?CB", "meas.cber"),
    ("*?VB", "meas.vber"),
]

# Commands that change the firmware or memory of the meter. The console refuses
# them unless "expert mode" is on.
DANGEROUS_PREFIXES = ("*BO", "*3141", "*SB", "*RM", "*FD", "*FL", "*FI", "*EM", "*FILE", "*INSTALLATION",
                      "*OPEN", "*CLOSE", "*DIR", "*LR")
# Firmware update and file deletion are refused always, expert mode or not.
FORBIDDEN_PREFIXES = ("*BO", "*3141", "*SB", "*FD")

# Satellite tuning: commands and codes from RM-404 v2.60 (Config_dvbs,
# functions 0x4ac2c0…0x4aca30 and 0x4adda0…).
SAT_MODES = {"DVB-S": 3, "DVB-S2": 7}                       # *SA<n>, read back with *?SA
LNB_CODES = {"external": 0, "13V": 2, "13V+22kHz": 6, "18V": 4, "18V+22kHz": 7}   # *LB<n>
FEC_DVBS = {"1/2": 0, "2/3": 1, "3/4": 2, "5/6": 4, "7/8": 6}                      # *CR0<n>
FEC_DVBS2 = {"1/2": 0, "2/3": 1, "3/4": 2, "4/5": 3, "5/6": 4, "6/7": 5, "7/8": 6,
             "1/4": 7, "1/3": 8, "2/5": 9, "3/5": 10, "8/9": 11, "9/10": 12}
MOD_DVBS2 = {"QPSK": 8, "8PSK": 9}                                                 # *CO0<n>

# *?ST is the demodulator status line (what the meter prints at the bottom of the
# screen: "No signal received" … "MPEG-2 TS"). The reply is hex after '*ST'.
# After every tuning step RM-404 compares *?ST with the "channel locked" code of
# the current mode (*SA) and stops tuning once it matches. Codes from RM-404
# (functions 0x4a3bee…0x4b2a2a): *SA → *?ST when locked. Verified: SA3 → 6, SA7 → 8.
LOCK_ST = {1: 0x2, 2: 0x4, 3: 0x6, 4: 0x7, 5: 0x3, 6: 0x5, 7: 0x8, 9: 0x9, 10: 0xA}
SA_NAMES = {0: "analog", 1: "DVB-T", 2: "DVB-C", 3: "DVB-S", 7: "DVB-S2"}
LOCK_TEXT = {3: "MPEG-2 TS DVB-S", 7: "MPEG-2 TS DVB-S2", 1: "MPEG-2 TS DVB-T", 2: "MPEG-2 TS DVB-C"}
# Intermediate codes are not decoded in the PROMAX software. The screen texts are
# known from the manual (satellites/st_codes.json: screen_messages); codes seen on a
# real meter go to "labels" there.
SETTLE_AFTER_SA = 3.0    # RM-404: Sleep(3000) after *SA
SETTLE_AFTER_SET = 1.5   # RM-404: Sleep(1500) after every tuning command


def _load_st_labels() -> dict:
    """Screen texts for *?ST codes observed on a real meter."""
    f = Path(__file__).resolve().parent / "satellites" / "st_codes.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("labels", {})
    except (OSError, ValueError):
        return {}


ST_LABELS = _load_st_labels()

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
# LV/CN/ME: the '<'/'>' flag may stand before the sign ('ME<+0030', verified) or after a space ('LV <0300').
_DB_RE = re.compile(r"^\s*([<>])?\s*([+-])?\s*([<>])?\s*(\d+(?:\.\d+)?)\s*$")


def lnb_pol(lnb: str) -> str | None:
    """LNB supply → polarisation: 13 V vertical, 18 V horizontal."""
    return "V" if lnb.startswith("13") else "H" if lnb.startswith("18") else None


def is_dangerous(cmd: str) -> bool:
    c = cmd.strip().upper()
    if not c.startswith("*"):
        c = "*" + c
    return not c.startswith("*?") and c.startswith(DANGEROUS_PREFIXES)


def is_forbidden(cmd: str) -> bool:
    c = cmd.strip().upper()
    if not c.startswith("*"):
        c = "*" + c
    return c.startswith(FORBIDDEN_PREFIXES) or c.endswith(".PRO")


def first_number(text: str):
    m = NUM_RE.search(text or "")
    return float(m.group().replace(",", ".")) if m else None


def parse_measure(cmd: str, text: str) -> dict:
    """Parse a reply the same way NetUpdate does.

    A reply is '<2-letter code><data>' (the protocol strips the leading '*'):
      FR: data is the frequency in kHz                  → MHz = data / 1000 (verified)
      LV/CN/ME: [flag][sign][value×10]; flag '!' means "no value",
                '<' / '>' — below / above the display limit ('ME<+0030' = MER <3.0 dB, verified)
      CB/VB (BER): [flag][sign][2 digits]E[exponent], a plain number: 'CB>+10E-2' is
                   ">1.0E-1" on the screen (verified on TV EXPLORER II+ 1.30).
    """
    code = cmd.strip().lstrip("*?").upper()
    s = text[2:] if text[:2].upper() == code else text
    try:
        if code == "FR":
            return {"value": round(float(s) / 1000.0, 3)}
        if code in ("LV", "CN", "ME"):
            if s.strip().startswith("!"):
                return {"value": None, "note": "---"}
            m = _DB_RE.match(s)
            if not m:
                raise ValueError(s)
            flag = m.group(1) or m.group(3)
            val = float(m.group(4)) / 10.0
            if m.group(2) == "-":
                val = -val
            return {"value": round(val, 1), "note": flag}
        if code in ("CB", "VB"):
            if s.strip().startswith("!"):
                return {"value": None, "note": "---"}
            body = s.replace(" ", "")
            flag = body[0] if body[:1] in ("<", ">") else None
            body = body[1:] if flag else body
            try:
                return {"value": float(body), "note": flag}
            except ValueError:
                return {"value": None, "note": body}
    except ValueError:
        pass
    return {"value": first_number(s)}


class Explorer:
    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()   # lock_status() is also called from inside tuning
        self.port = None
        self.link = None
        self.info = {}
        self.log_lines: list[str] = []
        self.log_file = None
        self._sat_sent: dict[str, str] = {}   # last tuning commands sent (not repeated)
        self.demo = False
        self.settle_after_sa = SETTLE_AFTER_SA     # shorter in demo mode
        self.settle_after_set = SETTLE_AFTER_SET
        self.current: dict = {}      # what the meter is tuned to now (for logs and the lab protocol)

    # --- log ---------------------------------------------------------------
    def log(self, msg: str):
        line = datetime.now().strftime("%H:%M:%S.%f")[:-3] + "  " + msg
        self.log_lines.append(line)
        del self.log_lines[:-2000]
        if self.log_file:
            self.log_file.write(line + "\n")
            self.log_file.flush()

    # --- connection ----------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self.link is not None

    def connect(self, demo: bool = False, port: str | None = None) -> dict:
        """Connect: demo, a given COM port, the detected COM port or libusb.

        Windows: the TI TUSB3410 driver gives a COM port — the main path.
        macOS: there is no driver, so the chip is driven directly through libusb.
        """
        with self.lock:
            if self.link:
                return self.info
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log_file = open(self.log_dir / f"session_{stamp}{'_demo' if demo else ''}.log", "w", encoding="utf-8")
            if demo:
                from .simulator import SimulatedExplorer
                self.port = SimulatedExplorer()
                self.log(tr("log.demo_mode"))
            else:
                try:
                    self.port = self._open_transport(port)
                except Exception as e:
                    self.log(tr("log.connect_failed", error=e))
                    self.log_file.close()
                    self.log_file = None
                    raise
            self.demo = demo
            self.settle_after_sa, self.settle_after_set = (0.1, 0.2) if demo else (SETTLE_AFTER_SA, SETTLE_AFTER_SET)
            self.link = PromaxLink(self.port, log=self.log)
            self._sat_sent = {}
            self.info = {}
            # Right after the port opens the TV EXPLORER II+ may stay silent (no XON) for
            # 8 s and longer (verified). Wait for the first XON longer and retry on failure.
            if not demo:
                self.link.peek_xon(12.0)
            for _attempt in range(1 if demo else 2):
                for key, cmd in IDENT_COMMANDS.items():
                    if self.info.get(key):
                        continue
                    try:
                        text = self.link.query(cmd)
                        # the reply repeats the command code: '*?NA' → 'NA<name>'
                        self.info[key] = text[2:].strip() if text[:2] == cmd[2:4] else text
                    except ProtocolError as e:
                        self.info[key] = None
                        self.log(f"{cmd}: {e}")
                if self.info.get("model") or not self.link.peek_xon(10.0):
                    break
            if not any(self.info.values()):
                self.log(tr("log.no_answer"))
            return self.info

    def _open_transport(self, port: str | None):
        try:
            from .serialport import SerialPortLink, find_port
        except ImportError:          # pyserial is not installed — libusb only
            SerialPortLink = find_port = None
        if port and port != "usb":
            if SerialPortLink is None:
                raise DeviceNotFound(tr("err.need_pyserial"))
            link = SerialPortLink(port, baud=BAUD)
            self.log(tr("log.port_opened", port=port, baud=BAUD))
            return link
        if port != "usb" and find_port is not None:
            found = find_port()
            if found:
                link = SerialPortLink(found, baud=BAUD)
                self.log(tr("log.port_found", port=found, baud=BAUD))
                return link
        self.log(tr("log.usb_search"))
        try:
            link = TUSB3410Serial(baud=BAUD)
        except DeviceNotFound:
            devs = ", ".join(f"{v:04x}:{p:04x} {n}".strip() for v, p, n in list_usb_devices())
            self.log(tr("log.usb_not_found", devices=devs or "—"))
            if os.name == "nt":
                self.log(tr("log.windows_driver_hint"))
            raise
        d = link.dev
        self.log(tr("log.usb_found", vid=f"{d.idVendor:04x}", pid=f"{d.idProduct:04x}", baud=BAUD))
        return link

    def disconnect(self):
        with self.lock:
            if self.port:
                self.port.close()
            self.port = None
            self.link = None
            self.log(tr("log.disconnected"))
            if self.log_file:
                self.log_file.close()
                self.log_file = None

    # --- commands ------------------------------------------------------------
    def command(self, cmd: str, expert: bool = False) -> dict:
        if not self.link:
            raise ProtocolError(tr("err.not_connected"))
        if is_forbidden(cmd):
            raise ProtocolError(tr("err.forbidden"))
        if is_dangerous(cmd) and not expert:
            raise ProtocolError(tr("err.dangerous"))
        with self.lock:
            r = self.link.send(cmd)
        return {"command": cmd, "ack": r.ack, "reply": r.text}

    def measure(self, commands=None) -> dict:
        if not self.link:
            raise ProtocolError(tr("err.not_connected"))
        out = {"time": datetime.now().isoformat(timespec="seconds"), "values": []}
        for cmd, label in (commands or MEASURE_COMMANDS):
            try:
                with self.lock:
                    text = self.link.query(cmd)
                out["values"].append({"cmd": cmd, "label": tr(label), "raw": text, **parse_measure(cmd, text)})
            except ProtocolError as e:
                out["values"].append({"cmd": cmd, "label": tr(label), "raw": None, "error": str(e)})
            time.sleep(0.05)
        return out

    # --- satellite tuning ----------------------------------------------------
    def _hex_query(self, cmd: str) -> int | None:
        text = self.link.query(cmd)
        try:
            return int(text[2:].strip() or "x", 16)
        except ValueError:
            return None

    def lock_status(self) -> dict:
        """Demodulator state: is the channel locked (like the status line on the meter's screen)."""
        if not self.link:
            raise ProtocolError(tr("err.not_connected"))
        with self.lock:
            sa = self._hex_query("*?SA")
            st = self._hex_query("*?ST")
        lock_code = LOCK_ST.get(sa)
        locked = st is not None and lock_code is not None and st == lock_code
        if locked:
            text = LOCK_TEXT.get(sa, f"MPEG-2 TS ({SA_NAMES.get(sa, sa)})")
        elif st is None:
            text = tr("st.no_reply")
        else:
            text = ST_LABELS.get(str(sa), {}).get(str(st)) or tr("st.no_lock", code=st)
        return {"sa": sa, "mode": SA_NAMES.get(sa, sa), "st": st, "lock_code": lock_code,
                "locked": locked, "text": text}

    def _settle(self, seconds: float | None = None) -> bool:
        """Pause after a tuning command (as RM-404 does); stop early once the channel is locked."""
        deadline = time.monotonic() + (self.settle_after_set if seconds is None else seconds)
        while True:
            try:
                if self.lock_status()["locked"]:
                    return True
            except ProtocolError:
                pass
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))

    _wait_ready = _settle   # old name (tools/tests)

    def tune_satellite(self, rf_mhz: float, lo_mhz: float, symbol_rate: int, fec: str,
                       lnb: str = "13V", standard: str = "DVB-S", modulation: str = "QPSK",
                       inversion: bool = False, force: bool = False) -> dict:
        """Tune the meter to a satellite channel the way RM-404 does.

        Order: *SA (if the mode differs), *LB, *FR (IF = RF − LO, kHz), then *CR0,
        *RA, *CO0 (S2), *SI. After every command there is an RM-404 pause, but as
        soon as *?ST reports "locked" the remaining parameters are not sent: the
        meter found the signal itself. Unchanged parameters are not re-sent
        (unless force=True).
        """
        if not self.link:
            raise ProtocolError(tr("err.not_connected"))
        mode = SAT_MODES[standard]
        fec_table = FEC_DVBS2 if standard == "DVB-S2" else FEC_DVBS
        if fec not in fec_table:
            raise ProtocolError(tr("err.fec_unsupported", fec=fec, std=standard))
        if_khz = round((rf_mhz - lo_mhz) * 1000)
        if not 950_000 <= if_khz <= 2_150_000:
            raise ProtocolError(tr("err.if_range", mhz=f"{if_khz / 1000:.1f}"))
        steps = [f"*LB{LNB_CODES[lnb]}", f"*FR{if_khz:07d}", f"*CR0{fec_table[fec]}", f"*RA{int(symbol_rate):05d}"]
        if standard == "DVB-S2":
            steps.append(f"*CO0{MOD_DVBS2[modulation]}")
        steps.append(f"*SI{1 if inversion else 0}")
        sent, skipped, locked = [], [], False
        if force:
            self._sat_sent = {}
        with self.lock:
            # Ask the meter for its mode every time instead of trusting memory: the meter
            # changes it by itself (DVB-S2 recognition switches to SA7, verified), and then
            # a DVB-S channel never locks without *SA3.
            if self._hex_query("*?SA") != mode:
                self.link.send(f"*SA{mode}")
                sent.append(f"*SA{mode}")
                self._sat_sent = {}
                time.sleep(self.settle_after_sa)
            self._sat_sent["SA"] = f"*SA{mode}"
            for i, cmd in enumerate(steps):
                key = cmd[1:3]
                if self._sat_sent.get(key) == cmd:
                    continue
                if locked and i >= 2:          # after *LB and *FR the meter already locked by itself
                    skipped.append(cmd)
                    continue
                r = self.link.send(cmd)
                sent.append(cmd + ("" if r.ack else " (NAK)"))
                self._sat_sent[key] = cmd if r.ack else ""
                if i >= 1:                     # right after *LB the frequency is not set yet
                    locked = self._settle()
                else:
                    time.sleep(min(0.3, self.settle_after_set))
            status = self.lock_status()
        self.log(tr("log.tuned", sent=" ".join(sent) or tr("log.no_changes"),
                    skipped=tr("log.tuned_skipped", cmds=" ".join(skipped)) if skipped else "",
                    status=status["text"]))
        self.current = {"freq_mhz": rf_mhz, "lo_mhz": lo_mhz, "sr": int(symbol_rate), "fec": fec, "system": standard,
                        "modulation": modulation if standard == "DVB-S2" else "QPSK", "lnb": lnb,
                        "pol": lnb_pol(lnb), "pos": self.current.get("pos")}
        return {"sent": sent, "skipped": skipped, "if_mhz": if_khz / 1000, "locked": status["locked"],
                "st": status["st"], "status": status["text"]}

    def set_if(self, if_khz: int, settle: float = 0.15):
        """Fast frequency change for a spectrum sweep (no *?ST wait)."""
        with self.lock:
            self.link.send(f"*FR{int(if_khz):07d}")
            self._sat_sent["FR"] = f"*FR{int(if_khz):07d}"
        time.sleep(settle)

    def goto_frequency(self, rf_mhz: float, lo_mhz: float, lnb: str) -> dict:
        """"Like a radio": move to a frequency without touching SR/FEC/modulation.

        The LNB supply (polarisation/band) is sent only when it changes.
        """
        if not self.link:
            raise ProtocolError(tr("err.not_connected"))
        if_khz = round((rf_mhz - lo_mhz) * 1000)
        if not 950_000 <= if_khz <= 2_150_000:
            raise ProtocolError(tr("err.if_range", mhz=f"{if_khz / 1000:.1f}"))
        lb = f"*LB{LNB_CODES[lnb]}"
        if self._sat_sent.get("LB") != lb:
            with self.lock:
                self.link.send(lb)
            self._sat_sent["LB"] = lb
            time.sleep(0.3)
        self.set_if(if_khz, settle=0)
        self.current = {**self.current, "freq_mhz": rf_mhz, "lo_mhz": lo_mhz, "lnb": lnb, "pol": lnb_pol(lnb)}
        return {"if_mhz": if_khz / 1000, "rf_mhz": rf_mhz}

    def read(self, commands) -> dict:
        """Run a set of queries → {code: parsed value}."""
        out = {}
        for cmd in commands:
            try:
                with self.lock:
                    text = self.link.query(cmd)
                out[cmd[2:4]] = {"raw": text, **parse_measure(cmd, text)}
            except ProtocolError as e:
                out[cmd[2:4]] = {"raw": None, "value": None, "error": str(e)}
        return out

    # --- datalogger ------------------------------------------------------------
    # File type 0x0A is a datalogger (NetUpdate: *?FI0A, *?FL0A<name>, *FD0A).
    DL_TYPE = "0A"

    def list_datalogger(self) -> list[dict]:
        """Dataloggers stored in the meter (NetUpdate4, function 0x5b4a20).

        *?FI0A      → '*FI' + number of files (2 hex)
        *?FI0ANN    → '*FI' + field(2) + field(8) + file name
        """
        if not self.link:
            raise ProtocolError(tr("err.not_connected"))
        with self.lock:
            head = self.link.query("*?FI" + self.DL_TYPE)
            count = int(head[2:4], 16)
            self.log(tr("log.dl_count", n=count))
            files = []
            for i in range(count):
                text = self.link.query(f"*?FI{self.DL_TYPE}{i:02x}")
                name = "".join(ch for ch in text[12:] if " " <= ch <= "~").strip()
                files.append({"index": i, "name": name, "field1": text[2:4], "field2": text[4:12], "raw": text})
        return files

    def download_datalogger(self, name: str, dest_dir: Path, progress=None) -> Path:
        """Download a datalogger and save it as <name>.dl, byte for byte like NetUpdate."""
        if not self.link:
            raise ProtocolError(tr("err.not_connected"))
        with self.lock:
            data = self.link.receive_file(f"*?FL{self.DL_TYPE}{name}", progress=progress)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\-. ]", "_", name).strip().rstrip(".") or "datalogger"
        if safe.split(".")[0].upper() in WINDOWS_RESERVED:   # Windows cannot create CON.dl etc.
            safe = "_" + safe
        path = dest_dir / f"{safe}.dl"
        path.write_bytes(data)
        self.log(tr("log.dl_saved", n=len(data), path=path))
        return path
