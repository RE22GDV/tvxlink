"""Talking to the meter through a COM port (Windows / Linux / any virtual-port driver).

This is how NetUpdate works on Windows too: the TI TUSB3410 driver
(drivers/windows/TUSB3410, see tools/fetch_vendor.py) loads the firmware into the
chip and creates a virtual COM port "UMP USB Serial Port / TUSB3410". From there it
is a plain serial port: 115200 baud, 8N1, no hardware flow control. The PROMAX
XON/XOFF handshake is handled by the program itself, so the port's software flow
control stays off (verified on Windows 11, COM3).
"""

from __future__ import annotations

import serial
import serial.tools.list_ports

from .i18n import tr
from .tusb3410 import DeviceNotFound, TI_VID, TI_3410_PID, PROMAX_VID, PROMAX_PID

KNOWN_IDS = {(TI_VID, TI_3410_PID), (PROMAX_VID, PROMAX_PID)}
NAME_HINTS = ("TUSB3410", "UMP", "PROMAX", "TV EXPLORER")


def list_ports() -> list[dict]:
    out = []
    for p in serial.tools.list_ports.comports():
        text = f"{p.description} {p.manufacturer or ''} {p.hwid}".upper()
        promax = (p.vid, p.pid) in KNOWN_IDS or any(h in text for h in NAME_HINTS)
        out.append({"device": p.device, "description": p.description, "hwid": p.hwid,
                    "vid": f"{p.vid:04x}" if p.vid is not None else None,
                    "pid": f"{p.pid:04x}" if p.pid is not None else None, "promax": promax})
    return out


def find_port() -> str | None:
    """The meter's COM port by VID/PID or driver name."""
    for p in list_ports():
        if p["promax"]:
            return p["device"]
    return None


class SerialPortLink:
    """Same interface as TUSB3410Serial: write(), read(timeout_ms), close()."""

    def __init__(self, device: str | None = None, baud: int = 115200):
        device = device or find_port()
        if not device:
            raise DeviceNotFound(tr("err.com_not_found"))
        self.device = device
        # serial_for_url understands "COM3", "/dev/ttyUSB0" and "loop://" (tests)
        self.ser = serial.serial_for_url(device, baudrate=baud, bytesize=8, parity="N", stopbits=1,
                                         timeout=0.2, xonxoff=False, rtscts=False, dsrdtr=False)
        self.ser.dtr = True
        self.ser.rts = True
        self.ser.reset_input_buffer()

    def write(self, data: bytes):
        self.ser.write(data)
        self.ser.flush()

    def read(self, size: int = 4096, timeout_ms: int = 200) -> bytes:
        self.ser.timeout = timeout_ms / 1000
        first = self.ser.read(1)
        if not first:
            return b""
        waiting = self.ser.in_waiting
        return first + (self.ser.read(min(waiting, size)) if waiting else b"")

    def close(self):
        try:
            self.ser.close()
        except serial.SerialException:
            pass
