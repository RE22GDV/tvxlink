"""User-space driver for the TI TUSB3410 USB-UART bridge (libusb).

The TV EXPLORER II / II+ uses a TUSB3410 (VID 0451, PID 3410). macOS has no
driver for this chip, so everything is done here, like the Linux driver
ti_usb_3410_5052.c:

1. A device with one configuration and one endpoint is the boot loader.
   The firmware umpf3410.i51 (from the PROMAX NetUpdate package, fetched by
   tools/fetch_vendor.py) is written to it over bulk OUT; then the device is
   reset and re-enumerates with 2 configurations.
2. Configuration 2 is selected, the UART is set up with vendor requests
   (SET_CONFIG, OPEN_PORT, START_PORT), and bytes go through the bulk
   endpoints like a normal serial port.
"""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path

from .i18n import tr

import usb.core
import usb.util
import usb.backend.libusb1

TI_VID = 0x0451
TI_3410_PID = 0x3410
# Second USB ID of the EXPLORER-II family from NetUpdate's Equipment.xml.
PROMAX_VID = 0x24A7
PROMAX_PID = 0x0002

TI_BOOT_CONFIG = 1
TI_ACTIVE_CONFIG = 2

TI_SET_CONFIG = 0x05
TI_OPEN_PORT = 0x06
TI_CLOSE_PORT = 0x07
TI_START_PORT = 0x08
TI_PURGE_PORT = 0x0B
TI_WRITE_DATA = 0x80

TI_UART1_PORT = 0x03
TI_RAM_PORT = 0x05

TI_UART_ENABLE_X_IN = 0x0100
TI_UART_ENABLE_X_OUT = 0x0040
TI_UART_ENABLE_MS_INTS = 0x2000
TI_UART_ENABLE_AUTO_START_DMA = 0x4000

TI_PIPE_MODE_CONTINUOUS = 0x01
TI_PIPE_TIMEOUT_ENABLE = 0x80
TI_TRANSFER_TIMEOUT = 2

TI_PURGE_OUTPUT = 0x00
TI_PURGE_INPUT = 0x80

TI_RW_DATA_ADDR_XDATA = 0x30
TI_RW_DATA_BYTE = 0x01
TI_UART1_BASE_ADDR = 0xFFA0
TI_UART_OFFSET_MCR = 0x0004
TI_MCR_LOOP = 0x04
TI_MCR_DTR = 0x10
TI_MCR_RTS = 0x20

TI_DOWNLOAD_MAX_PACKET_SIZE = 64
TI_FIRMWARE_BUF_SIZE = 16284

FIRMWARE_NAME = "umpf3410.i51"


def firmware_path() -> Path:
    """Where the TUSB3410 firmware is looked for (it is not shipped in the repository):
    $TVXLINK_FIRMWARE, <state>/vendor/ (put there by the launcher, tvxlink/vendor.py),
    <data>/vendor/, then <repo>/firmware/."""
    from .paths import data_dir, state_dir
    env = os.environ.get("TVXLINK_FIRMWARE")
    for p in ([Path(env)] if env else []) + [state_dir() / "vendor" / FIRMWARE_NAME, data_dir() / "vendor" / FIRMWARE_NAME,
                                             Path(__file__).resolve().parent.parent / "firmware" / FIRMWARE_NAME]:
        if p.exists():
            return p
    raise DeviceNotFound(tr("err.no_firmware"))


DEFAULT_FIRMWARE = None   # resolved lazily by firmware_path()

VENDOR_OUT = usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE


def _backend():
    for path in ("/opt/homebrew/lib/libusb-1.0.dylib", "/usr/local/lib/libusb-1.0.dylib"):
        if os.path.exists(path):
            return usb.backend.libusb1.get_backend(find_library=lambda _x, p=path: p)
    try:
        # Windows: libusb-1.0.dll from the libusb-package wheel, no manual install.
        import libusb_package
        be = libusb_package.get_libusb1_backend()
        if be is not None:
            return be
    except ImportError:
        pass
    return usb.backend.libusb1.get_backend()


class DeviceNotFound(RuntimeError):
    pass


def find_device():
    """Find the meter: first the TI bridge, then the PROMAX VID variant."""
    be = _backend()
    if be is None:
        return None
    for vid, pid in ((TI_VID, TI_3410_PID), (PROMAX_VID, PROMAX_PID)):
        dev = usb.core.find(idVendor=vid, idProduct=pid, backend=be)
        if dev is not None:
            return dev
    return None


def list_usb_devices():
    """All USB devices (diagnostics when the meter is not found)."""
    out = []
    be = _backend()
    if be is None:
        return out
    try:
        devices = list(usb.core.find(find_all=True, backend=be))
    except Exception:            # noqa: BLE001 — diagnostics must never crash the server
        return out
    for dev in devices:
        try:
            name = usb.util.get_string(dev, dev.iProduct) if dev.iProduct else ""
        except Exception:        # noqa: BLE001
            # Windows: libusb cannot open a device bound to another driver (TI, HID…) —
            # NotImplementedError "Operation not supported". Skip the name then (verified).
            name = ""
        if not name and (dev.idVendor, dev.idProduct) == (0x0451, 0x3410):
            name = tr("usb.tusb3410_name")
        out.append((dev.idVendor, dev.idProduct, name or ""))
    return out


def _is_boot_mode(dev) -> bool:
    if dev.bNumConfigurations != 1:
        return False
    cfg = dev[0]
    intf = cfg[(0, 0)]
    return intf.bNumEndpoints == 1


def download_firmware(dev, firmware: Path | None = None):
    image = Path(firmware or firmware_path()).read_bytes()
    if len(image) > TI_FIRMWARE_BUF_SIZE:
        raise ValueError(f"firmware too large: {len(image)} bytes")
    # Header: image length (LE16) + 8-bit sum of the image bytes.
    header = struct.pack("<HB", len(image), sum(image) & 0xFF)
    blob = header + image
    try:
        dev.set_configuration(TI_BOOT_CONFIG)
    except usb.core.USBError:
        pass
    intf = dev.get_active_configuration()[(0, 0)]
    ep_out = usb.util.find_descriptor(
        intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    if ep_out is None:
        raise RuntimeError("boot loader bulk OUT endpoint not found")
    for pos in range(0, len(blob), TI_DOWNLOAD_MAX_PACKET_SIZE):
        ep_out.write(blob[pos:pos + TI_DOWNLOAD_MAX_PACKET_SIZE], timeout=1000)
    time.sleep(0.1)
    try:
        dev.reset()
    except usb.core.USBError:
        pass  # the device drops off the bus to re-enumerate — expected
    usb.util.dispose_resources(dev)


class TUSB3410Serial:
    """A minimal "serial port" on top of the TUSB3410."""

    def __init__(self, baud: int = 115200, firmware: Path | None = None, xonxoff: bool = False):
        self.baud = baud
        self.xonxoff = xonxoff
        dev = find_device()
        if dev is None:
            raise DeviceNotFound(tr("err.usb_not_found"))
        if dev.idVendor == TI_VID and _is_boot_mode(dev):
            download_firmware(dev, firmware)
            dev = self._wait_reenumerate()
        self.dev = dev
        self._claim()
        self._open_port()

    @staticmethod
    def _wait_reenumerate(timeout: float = 8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.3)
            dev = find_device()
            if dev is not None and not _is_boot_mode(dev):
                return dev
        raise RuntimeError("the chip did not re-enumerate after the firmware download")

    def _claim(self):
        dev = self.dev
        if dev.idVendor == TI_VID:
            try:
                cur = dev.get_active_configuration().bConfigurationValue
            except usb.core.USBError:
                cur = None
            if cur != TI_ACTIVE_CONFIG:
                dev.set_configuration(TI_ACTIVE_CONFIG)
        else:
            try:
                dev.get_active_configuration()
            except usb.core.USBError:
                dev.set_configuration()
        intf = dev.get_active_configuration()[(0, 0)]
        self.intf_num = intf.bInterfaceNumber
        usb.util.claim_interface(dev, self.intf_num)
        eps = list(intf)

        def pick(direction, ep_type):
            for e in eps:
                if (usb.util.endpoint_direction(e.bEndpointAddress) == direction
                        and usb.util.endpoint_type(e.bmAttributes) == ep_type):
                    return e
            return None

        self.ep_in = pick(usb.util.ENDPOINT_IN, usb.util.ENDPOINT_TYPE_BULK)
        self.ep_out = pick(usb.util.ENDPOINT_OUT, usb.util.ENDPOINT_TYPE_BULK)
        if self.ep_in is None or self.ep_out is None:
            raise RuntimeError("the device has no bulk endpoints for the UART")

    def _cmd(self, command: int, module: int, value: int = 0, data: bytes = b""):
        self.dev.ctrl_transfer(VENDOR_OUT, command, value, module, data, timeout=1000)

    def _set_uart_config(self):
        wbaud = (923077 + self.baud // 2) // self.baud
        flags = TI_UART_ENABLE_MS_INTS | TI_UART_ENABLE_AUTO_START_DMA
        xon, xoff = 0, 0
        if self.xonxoff:
            flags |= TI_UART_ENABLE_X_IN | TI_UART_ENABLE_X_OUT
            xon, xoff = 0x11, 0x13
        # wBaudRate, wFlags (BE16), 8 bits, no parity, 1 stop, XON, XOFF, RS-232
        cfg = struct.pack(">HHBBBBBB", wbaud, flags, 0x03, 0x00, 0x00, xon, xoff, 0x00)
        self._cmd(TI_SET_CONFIG, TI_UART1_PORT, 0, cfg)
        self._set_mcr(TI_MCR_DTR | TI_MCR_RTS)

    def _set_mcr(self, mcr: int):
        addr = TI_UART1_BASE_ADDR + TI_UART_OFFSET_MCR
        data = struct.pack(">BBBHHBB", TI_RW_DATA_ADDR_XDATA, TI_RW_DATA_BYTE, 1,
                           addr >> 16, addr & 0xFFFF, TI_MCR_RTS | TI_MCR_DTR | TI_MCR_LOOP, mcr)
        self._cmd(TI_WRITE_DATA, TI_RAM_PORT, 0, data)

    def _open_port(self):
        if self.dev.idVendor != TI_VID:
            return  # the 24A7:0002 variant has no TI commands, plain bulk
        open_settings = TI_PIPE_MODE_CONTINUOUS | TI_PIPE_TIMEOUT_ENABLE | (TI_TRANSFER_TIMEOUT << 2)
        # Same sequence as ti_open() in the Linux driver.
        self._set_uart_config()
        self._cmd(TI_OPEN_PORT, TI_UART1_PORT, open_settings)
        self._cmd(TI_START_PORT, TI_UART1_PORT)
        self._cmd(TI_PURGE_PORT, TI_UART1_PORT, TI_PURGE_INPUT)
        self._cmd(TI_PURGE_PORT, TI_UART1_PORT, TI_PURGE_OUTPUT)
        for ep in (self.ep_out, self.ep_in):
            try:
                self.dev.clear_halt(ep)
            except usb.core.USBError:
                pass
        self._set_uart_config()
        self._cmd(TI_OPEN_PORT, TI_UART1_PORT, open_settings)
        self._cmd(TI_START_PORT, TI_UART1_PORT)

    def set_baud(self, baud: int):
        self.baud = baud
        if self.dev.idVendor == TI_VID:
            self._set_uart_config()

    def write(self, data: bytes):
        self.ep_out.write(data, timeout=2000)

    def read(self, size: int = 4096, timeout_ms: int = 200) -> bytes:
        try:
            return bytes(self.ep_in.read(max(size, self.ep_in.wMaxPacketSize), timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            return b""
        except usb.core.USBError as e:
            if getattr(e, "errno", None) in (60, 110):  # timeout on macOS / Linux
                return b""
            raise

    def close(self):
        try:
            if self.dev.idVendor == TI_VID:
                self._cmd(TI_CLOSE_PORT, TI_UART1_PORT)
        except usb.core.USBError:
            pass
        try:
            usb.util.release_interface(self.dev, self.intf_num)
        except usb.core.USBError:
            pass
        usb.util.dispose_resources(self.dev)
