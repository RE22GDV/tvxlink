"""TV EXPLORER II+ simulator for demo mode and tests (no meter needed).

It speaks the same protocol (XON/XOFF/ACK, *?LV …) and pretends to receive one
orbital position: a noise floor plus a "shelf" for every transponder of the
database, lock status, C/N, MER and BER in the real reply formats.
The numbers are made up — they only exercise the program.
"""

from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

from .device import LOCK_ST
from .protocol import XON, XOFF, ACK, NAK

SAT_DIR = Path(__file__).resolve().parent / "satellites"
LNB_BY_CODE = {0: None, 2: ("V", "low"), 6: ("V", "high"), 4: ("H", "low"), 7: ("H", "high")}
NOISE_DBUV = 42.0


class SimulatedExplorer:
    def __init__(self, position: str = "16E", seed: int = 9):
        self.rnd = random.Random(seed)
        self._last_xon = time.monotonic()
        self.strength = {}
        self.point(position)
        self.out = bytearray([XON])
        self.mode = 3
        self.lnb = 2
        self.fr_khz = 1_721_000
        self.sr = 29950
        self.co = 9

    def point(self, position: str):
        """"Point the antenna" of the simulator at an orbital position."""
        sat = json.loads((SAT_DIR / f"{position}.json").read_text(encoding="utf-8"))
        self.position = position
        self.tps = sat["transponders"]
        # Every transponder gets its own made-up strength (dB above noise).
        for t in self.tps:
            self.strength.setdefault((position, t["freq_mhz"], t["pol"], t["sr"]), self.rnd.uniform(6, 22))

    def _st(self, t) -> float:
        return self.strength[(self.position, t["freq_mhz"], t["pol"], t["sr"])]

    # --- reception model ---------------------------------------------------------
    def _active(self):
        pol_band = LNB_BY_CODE.get(self.lnb)
        if not pol_band:
            return []
        pol, band = pol_band
        return [t for t in self.tps if t["pol"] == pol and t["band"] == band]

    def _level(self, if_mhz: float) -> float:
        p = 10 ** (NOISE_DBUV / 10)
        for t in self._active():
            bw = t["sr"] / 1000 * 1.2
            d = abs(t["if_mhz"] - if_mhz)
            if d < bw:
                shape = 1.0 if d < bw * 0.4 else max(0.0, 1 - (d - bw * 0.4) / (bw * 0.6))
                p += 10 ** ((NOISE_DBUV + self._st(t)) / 10) * shape
        return 10 * math.log10(p) + self.rnd.uniform(-0.4, 0.4)

    def _locked(self):
        if_mhz = self.fr_khz / 1000
        for t in self._active():
            std_ok = {3: "DVB-S", 7: "DVB-S2"}.get(self.mode) == t["system"]
            if std_ok and abs(t["if_mhz"] - if_mhz) < 2 and abs(t["sr"] - self.sr) < 50:
                cn = self._st(t)
                need = t["required_cn_db"] or 0
                if cn > need - 1:
                    return t, cn
        return None, None

    # --- protocol ----------------------------------------------------------------
    @staticmethod
    def _db(code: str, v: float | None) -> str:
        if v is None:
            return f"*{code}!\r"
        return f"*{code} {'-' if v < 0 else '+'}{abs(round(v * 10)):04d}\r"

    @staticmethod
    def _ber(b: float) -> str:
        """Real meter format: [flag][sign][2 digits]E[exponent], 3.4E-5 → ' +34E-6'."""
        if b < 1e-8:
            return "<+10E-9"
        e = math.floor(math.log10(b))
        m = round(b / 10 ** e * 10)
        if m >= 100:
            m, e = 10, e + 1
        return f" +{m:02d}E{e - 1:+d}"

    def write(self, data: bytes):
        cmd = data.decode("latin-1").strip()
        self.out += bytes([XOFF])
        reply = None
        ok = True
        if cmd == "*?NA":
            reply = "*NATV EXPLORER II+ (SIMULATOR)\r"
        elif cmd == "*?VE":
            reply = "*VE0.00-sim\r"
        elif cmd == "*?ID":
            reply = "*ID0000\r"
        elif cmd == "*?SA":
            reply = f"*SA{self.mode:02X}\r"
        elif cmd == "*?ST":
            # Locked → the mode's "MPEG-2 TS" code (as in RM-404); otherwise made-up
            # codes: 1 — signal above noise, 0 — no signal.
            t, _ = self._locked()
            if t:
                code = LOCK_ST.get(self.mode, 0)
            else:
                code = 1 if self._level(self.fr_khz / 1000) > NOISE_DBUV + 3 else 0
            reply = f"*ST{code:02X}\r"
        elif cmd == "*?FR":
            reply = f"*FR{self.fr_khz}\r"
        elif cmd == "*?LV":
            reply = self._db("LV", self._level(self.fr_khz / 1000))
        elif cmd in ("*?CN", "*?ME"):
            t, cn = self._locked()
            if cmd == "*?CN":
                v = cn + self.rnd.uniform(-0.3, 0.3) if t else max(0.0, self._level(self.fr_khz / 1000) - NOISE_DBUV)
                reply = self._db("CN", v)
            else:
                reply = self._db("ME", cn - 1.5 + self.rnd.uniform(-0.3, 0.3) if t else None)
        elif cmd in ("*?CB", "*?VB"):
            # Made-up BER: CBER follows the ideal curve for C/N, VBER is much lower.
            # Without lock the real meter answers "above the limit": CB '>+10E-2', VB '>+10E-4'.
            t, cn = self._locked()
            if not t:
                reply = f"*{cmd[2:4]}>+10E-{2 if cmd == '*?CB' else 4}\r"
            else:
                from .lab import ber_theory
                b = ber_theory(cn + self.rnd.uniform(-0.3, 0.3), t["modulation"] if t["modulation"] in ("QPSK", "8PSK") else "QPSK")
                if cmd == "*?VB":
                    b = min(b, b * b * 10)      # after the decoder — much lower
                reply = f"*{cmd[2:4]}{self._ber(b)}\r"
        elif cmd.startswith("*SA"):
            self.mode = int(cmd[3:], 16)
        elif cmd.startswith("*LB"):
            self.lnb = int(cmd[3:])
        elif cmd.startswith("*FR"):
            self.fr_khz = int(cmd[3:])
        elif cmd.startswith("*RA"):
            self.sr = int(cmd[3:])
        elif cmd.startswith("*CO0"):
            self.co = int(cmd[4:])
        elif cmd.startswith(("*CR0", "*SI")):
            pass
        elif cmd.startswith("*?FI0A"):
            reply = "*FI00\r" if len(cmd) == 6 else None
        else:
            ok = False
        self.out += bytes([ACK if ok else NAK])
        if reply:
            self.out += reply.encode("latin-1")
        self.out += bytes([XON])

    def read(self, size: int = 4096, timeout_ms: int = 100) -> bytes:
        if not self.out:
            # Like the real meter: XON once a second while idle ("ready").
            now = time.monotonic()
            if now - self._last_xon >= 1.0:
                self._last_xon = now
                return bytes([XON])
            time.sleep(min(timeout_ms, 50) / 1000)
            return b""
        chunk, self.out = bytes(self.out[:256]), self.out[256:]
        return chunk

    def close(self):
        pass
