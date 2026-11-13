"""Vendor files that cannot be shipped in this repository: the TI TUSB3410 driver
(Windows) and its 8051 firmware umpf3410.i51 (libusb path on macOS/Linux).

They are taken from the official PROMAX NetUpdate 4.35 package on promax.es,
exactly as NetUpdate installs them. Every download and every extracted file is
checked by SHA-256 against the values below (the driver is byte-for-byte the one
that worked with a real TV EXPLORER II+ on Windows 11, 2026-11-12).

    python -m tvxlink.vendor            fetch and unpack into <state>/vendor
    python -m tvxlink.vendor --check    only report what is there

The NetUpdate installer is an NSIS archive; it is unpacked with 7-Zip. When no
7-Zip is installed, the official 7-Zip 24.09 build is downloaded (also pinned).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .i18n import tr
from .paths import state_dir

NETUPDATE = ("https://www.promax.es/downloads/software/NetUpdate/NU4Setup_v4.35.zip",
             "0ce1a90ed9c0d547be265ac1197a16403878468c0b2f7330338f33e23b44973e")
SEVENZIP = {
    "7zr.exe": ("https://github.com/ip7z/7zip/releases/download/24.09/7zr.exe",
                "d2c0045523cf053a6b43f9315e9672fc2535f06aeadd4ffa53c729cd8b2b6dfe"),
    "win": ("https://github.com/ip7z/7zip/releases/download/24.09/7z2409-x64.exe",
            "bdd1a33de78618d16ee4ce148b849932c05d0015491c34887846d431d29f308e"),
    "mac": ("https://github.com/ip7z/7zip/releases/download/24.09/7z2409-mac.tar.xz",
            "073b6fa8dc5d9adb6f742888d0d75f5767406b503199b9704ccbf61133a21ded"),
    "linux": ("https://github.com/ip7z/7zip/releases/download/24.09/7z2409-linux-x64.tar.xz",
              "914c7e20ad5ef8e4d3cf08620ff8894b28fe11b7eb99809d6930870fbe48a281"),
}
SRC = "USB_Driver/Windows/tiinst/TUSB3410"
# target file → (file inside the NetUpdate installer, SHA-256)
FILES = {
    "TUSB3410/manual_x64/ump3410.cat": ("Win7-64/ump3410.cat", "6e41a281aab403508bbe194a5991872e265bca7ae0a8d3442161f3b4d87b3f64"),
    "TUSB3410/manual_x64/umpusbvista.sys": ("Win7-64/umpusbvista.sys", "d38086004a9500cf6da4b138bebc56d35ab84c4c3fcbdea66f18e51870356c30"),
    "TUSB3410/manual_x64/WdfCoInstaller01009.dll": ("Common-64/WdfCoInstaller01009.dll", "18487b4ff94edccc98ed59d9fca662d4a1331c5f1e14df8db3093256dd9f1c3e"),
    "TUSB3410/manual_x64/usbuart3410.inf": ("Common-64/usbuart3410.inf", "2dc7095f70ab333989374ac22f18a5807fb0e4ad036f89cb1bf0d67b47ff49e5"),
    "TUSB3410/manual_x64/umpf3410.i51": ("Common-64/umpf3410.i51", "3f31c5eeef031df0e038bc2a72fc93e1d0f7d3289c1fd117b06b4a2a736ce74c"),
    "TUSB3410/manual_x86/ump3410.cat": ("Win7-32/ump3410.cat", "15d5815db236e4a058feb045fb474f709e43890bafe413093cfc83dbf170e683"),
    "TUSB3410/manual_x86/umpusbvista.sys": ("Win7-32/umpusbvista.sys", "8eed569b3ffc9a1238a417f604835ca299735471e4722d3cdc190ef3886023ec"),
    "TUSB3410/manual_x86/WdfCoInstaller01009.dll": ("Common-32/WdfCoInstaller01009.dll", "7a6bf1f950684381205c717a51af2d9c81b203cb1f3db0006a4602e2df675c77"),
    "TUSB3410/manual_x86/usbuart3410.inf": ("Common-32/usbuart3410.inf", "2dc7095f70ab333989374ac22f18a5807fb0e4ad036f89cb1bf0d67b47ff49e5"),
    "TUSB3410/manual_x86/umpf3410.i51": ("Common-32/umpf3410.i51", "3f31c5eeef031df0e038bc2a72fc93e1d0f7d3289c1fd117b06b4a2a736ce74c"),
    "TUSB3410/LICENSE.rtf": ("LICENSE.rtf", None),
    "umpf3410.i51": ("Common-64/umpf3410.i51", "3f31c5eeef031df0e038bc2a72fc93e1d0f7d3289c1fd117b06b4a2a736ce74c"),
}


def vendor_dir() -> Path:
    return state_dir() / "vendor"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download(url: str, digest: str | None, label: str) -> bytes:
    print(tr("vendor.downloading", what=label, url=url), flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "TVXLink installer"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if digest and sha256(data) != digest:
        raise RuntimeError(tr("vendor.bad_hash", what=label))
    return data


def status() -> dict:
    """What is already unpacked and whether it matches the pinned hashes."""
    d = vendor_dir()
    out = {}
    for rel, (_src, digest) in FILES.items():
        p = d / rel
        out[rel] = "ok" if p.exists() and (digest is None or sha256(p.read_bytes()) == digest) else "missing"
    return out


def ready() -> bool:
    return all(v == "ok" for v in status().values())


def find_7z() -> str | None:
    for name in ("7zz", "7z", "7za"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe", "/opt/homebrew/bin/7zz", "/usr/local/bin/7zz"):
        if os.path.exists(p):
            return p
    return None


def fetch_7z(tmp: Path) -> str:
    """Download the official 7-Zip 24.09 build for this OS into tmp and return the program path."""
    if os.name == "nt":
        (tmp / "7zr.exe").write_bytes(download(*SEVENZIP["7zr.exe"], "7-Zip (7zr)"))
        (tmp / "7z-setup.exe").write_bytes(download(*SEVENZIP["win"], "7-Zip"))
        subprocess.run([str(tmp / "7zr.exe"), "x", "-y", f"-o{tmp / '7z'}", str(tmp / "7z-setup.exe"), "7z.exe", "7z.dll"],
                       check=True, capture_output=True)
        return str(tmp / "7z" / "7z.exe")
    key = "mac" if sys.platform == "darwin" else "linux"
    if key == "linux" and platform.machine().lower() not in ("x86_64", "amd64"):
        raise RuntimeError(tr("vendor.need_7z"))
    data = download(*SEVENZIP[key], "7-Zip")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tf:
        member = tf.getmember("7zz")
        (tmp / "7zz").write_bytes(tf.extractfile(member).read())
    exe = tmp / "7zz"
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


def fetch(force: bool = False, package: Path | None = None) -> Path:
    """Fetch NetUpdate 4.35, unpack the TUSB3410 driver and firmware into <state>/vendor."""
    dest = vendor_dir()
    if ready() and not force:
        print(tr("vendor.already", dir=dest))
        return dest
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        if package:
            data = Path(package).read_bytes()
            if sha256(data) != NETUPDATE[1]:
                raise RuntimeError(tr("vendor.bad_hash", what=package))
        else:
            data = download(*NETUPDATE, "PROMAX NetUpdate 4.35")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            exe = next(n for n in z.namelist() if n.lower().endswith(".exe"))
            z.extract(exe, tmp)
        seven = find_7z() or fetch_7z(tmp)
        print(tr("vendor.unpacking"), flush=True)
        subprocess.run([seven, "x", "-y", f"-o{tmp / 'nu'}", str(tmp / exe), f"{SRC}/*"], check=True, capture_output=True)
        src = tmp / "nu" / SRC
        for rel, (name, digest) in FILES.items():
            blob = (src / name).read_bytes()
            if digest and sha256(blob) != digest:
                raise RuntimeError(tr("vendor.bad_hash", what=name))
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
    (dest / "manifest.json").write_text(json.dumps({"source": NETUPDATE[0], "sha256": NETUPDATE[1], "files": status()}, indent=1),
                                        encoding="utf-8")
    print(tr("vendor.done", dir=dest), flush=True)
    return dest


def driver_inf() -> Path:
    arch = "x64" if platform.machine().endswith("64") else "x86"
    return vendor_dir() / "TUSB3410" / f"manual_{arch}" / "usbuart3410.inf"


def driver_installed() -> bool:
    """Windows: is the TI TUSB3410 driver in the driver store?"""
    if os.name != "nt":
        return True
    try:
        out = subprocess.run(["pnputil", "/enum-drivers"], capture_output=True, text=True, errors="replace", timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "usbuart3410.inf" in out.lower()


def install_driver() -> bool:
    """Windows: add the driver with pnputil, elevated through the UAC prompt. True when installed."""
    if os.name != "nt" or driver_installed():
        return True
    inf = driver_inf()
    if not inf.exists():
        fetch()
    print(tr("vendor.uac"), flush=True)
    ps = (f"Start-Process -FilePath pnputil -ArgumentList '/add-driver','\"{inf}\"','/install' "
          "-Verb RunAs -Wait -WindowStyle Hidden")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], check=False)
    ok = driver_installed()
    print(tr("vendor.driver_ok") if ok else tr("vendor.driver_fail", inf=inf), flush=True)
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tvxlink-vendor", description="Fetch the TI TUSB3410 driver and firmware from the PROMAX NetUpdate package")
    ap.add_argument("--check", action="store_true", help="only show what is there")
    ap.add_argument("--force", action="store_true", help="download again")
    ap.add_argument("--package", type=Path, help="use a local NU4Setup_v4.35.zip instead of downloading")
    ap.add_argument("--install-driver", action="store_true", help="Windows: also install the driver (UAC prompt)")
    a = ap.parse_args(argv)
    if a.check:
        print(json.dumps({"dir": str(vendor_dir()), "files": status(), "driver_installed": driver_installed()}, indent=1))
        return 0
    fetch(force=a.force, package=a.package)
    if a.install_driver:
        return 0 if install_driver() else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
