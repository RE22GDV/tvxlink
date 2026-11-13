"""PROMAX remote-control protocol (ASCII commands with XON/XOFF/ACK handshake).

Exchange order (PROMAX "Remote control commands" document; NetUpdate4.exe does
exactly the same for the EXPLORER-II family):

    meter -> XON (0x11) once a second: "ready for a command"
    PC    -> '*' + command + CR            e.g.  *?NA\r
    meter -> XOFF (0x13)                    command taken into the buffer
    meter -> ACK (0x06) or NAK (0x15)       command understood / not understood
    meter -> '*' + reply + CR               only for queries (*?..)
    meter -> XON                            ready for the next command

Commands are upper case. Queries start with "*?".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .i18n import tr

XON = 0x11
XOFF = 0x13
ACK = 0x06
NAK = 0x15
CR = 0x0D


class ProtocolError(RuntimeError):
    pass


@dataclass
class Reply:
    command: str
    ack: bool
    lines: list[str] = field(default_factory=list)
    raw: bytes = b""

    @property
    def text(self) -> str:
        return self.lines[0] if self.lines else ""


class PromaxLink:
    """Command exchange over any port object with write() and read(timeout_ms)."""

    def __init__(self, port, log=None):
        self.port = port
        self.log = log or (lambda _msg: None)
        self._buf = bytearray()

    def _fill(self, timeout_ms: int = 100) -> None:
        chunk = self.port.read(timeout_ms=timeout_ms)
        if chunk:
            self.log("<< " + chunk.hex(" "))
            self._buf.extend(chunk)

    def _take_byte(self, wanted: set[int], deadline: float) -> int | None:
        while time.monotonic() < deadline:
            for i, b in enumerate(self._buf):
                if b in wanted:
                    del self._buf[:i + 1]
                    return b
            self._fill()
        return None

    def wait_xon(self, timeout: float = 2.5) -> bool:
        return self._take_byte({XON}, time.monotonic() + timeout) is not None

    def peek_xon(self, timeout: float) -> bool:
        """Wait for XON without consuming it: the next command goes out on that XON."""
        if self.wait_xon(timeout):
            self._buf.insert(0, XON)
            return True
        return False

    def _read_line(self, deadline: float) -> bytes | None:
        while time.monotonic() < deadline:
            if CR in self._buf:
                i = self._buf.index(CR)
                line = bytes(self._buf[:i])
                del self._buf[:i + 1]
                return line
            self._fill()
        return None

    def send(self, command: str, expect_reply: bool | None = None, timeout: float = 3.0,
             wait_ready: bool = True) -> Reply:
        """Send '*CMD\\r' and return the meter's reply."""
        cmd = command.strip()
        if cmd.startswith("*"):
            cmd = cmd[1:]
        # One command per line: CR/LF, other control bytes, non-ASCII or a second '*' would let a
        # hidden command (e.g. '*?NA\r*FD…') slip past the checks in device.py onto the wire.
        if not cmd or "*" in cmd or any(not 0x20 <= ord(ch) <= 0x7E for ch in cmd):
            raise ProtocolError(tr("err.bad_command", cmd=repr(command)))
        if expect_reply is None:
            expect_reply = cmd.startswith("?")
        if wait_ready and not self.wait_xon():
            raise ProtocolError(tr("err.no_xon"))
        self._buf.clear()
        payload = ("*" + cmd + "\r").encode("ascii")
        self.log(">> " + payload.decode("ascii").replace("\r", "\\r"))
        self.port.write(payload)

        deadline = time.monotonic() + timeout
        if self._take_byte({XOFF}, deadline) is None:
            raise ProtocolError(tr("err.no_xoff", cmd=command))
        status = self._take_byte({ACK, NAK}, deadline)
        if status is None:
            raise ProtocolError(tr("err.no_ack", cmd=command))
        reply = Reply(command=command, ack=(status == ACK))
        if reply.ack and expect_reply:
            line = self._read_line(deadline)
            if line is None:
                raise ProtocolError(tr("err.no_reply", cmd=command))
            # The reply may arrive together with XON/XOFF: drop control bytes.
            clean = bytes(b for b in line if b >= 0x20)
            reply.raw = clean
            text = clean.decode("latin-1")
            reply.lines.append(text[1:] if text.startswith("*") else text)
        return reply

    def query(self, command: str, timeout: float = 3.0) -> str:
        r = self.send(command, expect_reply=True, timeout=timeout)
        if not r.ack:
            raise ProtocolError(tr("err.nak", cmd=command))
        return r.text

    def receive_file(self, command: str, idle_timeout: float = 15.0, progress=None) -> bytes:
        """Receive a file from an EXPLORER-II meter (NetUpdate4, function 0x5b52a0).

        After XOFF/ACK the meter streams '*' + 2 letters + 8 hex digits of length N,
        then N bytes. NetUpdate keeps the first N-4 of them (a 4-byte tail is dropped).
        The data is binary, so XON/XOFF inside it are left alone.
        Abort: 15 s of silence (twice) or more than 25 lone XON bytes in a row.
        """
        r = self.send(command, expect_reply=False)
        if not r.ack:
            raise ProtocolError(tr("err.nak", cmd=command))
        data = bytearray(self._buf)
        self._buf.clear()
        total = None
        lonely_xon = 0
        silent_rounds = 0
        last = time.monotonic()
        while total is None or len(data) < total:
            chunk = self.port.read(timeout_ms=200)
            if chunk:
                last = time.monotonic()
                silent_rounds = 0
                lonely_xon = lonely_xon + 1 if chunk == bytes([XON]) else 0
                if lonely_xon > 25:
                    break
                data.extend(chunk)
            elif time.monotonic() - last > idle_timeout:
                silent_rounds += 1
                last = time.monotonic()
                if silent_rounds > 1:
                    break
            if total is None:
                star = data.find(b"*")
                if star > 0:
                    del data[:star]  # garbage (XON) before the header
                if star >= 0 and len(data) >= 11:
                    try:
                        total = int(data[3:11].decode("ascii"), 16) + 11
                    except ValueError:
                        raise ProtocolError(tr("err.bad_file_header", head=repr(bytes(data[:11]))))
                    self.log(tr("log.file_size", n=total - 11))
            if progress and total:
                progress(min(len(data), total), total)
        if total is None or len(data) < total:
            got = len(data) - 11 if total else 0
            raise ProtocolError(tr("err.file_incomplete", got=max(got, 0), total=total - 11 if total else "?"))
        # After the transfer the meter sends XON again: keep it for the next command.
        self._buf.extend(data[total:])
        payload = bytes(data[11:total])
        return payload[:-4] if len(payload) >= 4 else payload
