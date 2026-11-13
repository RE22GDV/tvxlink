"""TVXLink launcher: one double-click installs everything once, then starts the program.

Called by Start-Windows.bat, Start-Mac.command and start.sh (or by hand):

    python launcher.py                 install what is missing and start the window
    python launcher.py --repair        redo the whole setup
    python launcher.py --offline       never download anything
    python launcher.py --check         show what is installed and exit
    python launcher.py --no-driver     do not touch the meter driver / firmware
    python launcher.py -- --port 8798 --no-browser --lang en      options for the program itself

What the first start does:
  1. a private Python environment in the per-user folder (a venv, or the official
     embedded Python on Windows when no Python is installed — set up by the .bat);
  2. the packages from requirements.txt (pyserial, pyusb, libusb-package);
  3. the TI TUSB3410 driver (Windows, with a UAC prompt) or its firmware (macOS),
     taken from the official PROMAX NetUpdate package and checked by SHA-256;
  4. a quick self-check, then the local server and the browser.
The user's measurement data lives in <home>/TVXLink and is never touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from tvxlink import __version__, i18n  # noqa: E402  (stdlib only, safe before any install)
from tvxlink.i18n import tr  # noqa: E402
from tvxlink.paths import data_dir, state_dir  # noqa: E402

PIP_PYZ = ("https://bootstrap.pypa.io/pip/zipapp/pip-24.3.1.pyz",
           "c59df64b826d5baa978829fd1e1b3ea2e748f73dce7cce58d09aa28a1c9323a7")
REQUIREMENTS = ROOT / "requirements.txt"
RUN_SNIPPET = "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module('tvxlink', run_name='__main__')"


def say(key: str, **kw):
    print(tr(key, **kw), flush=True)


def is_embedded(python: Path) -> bool:
    """The official Windows embeddable Python (a ._pth file next to python.exe, no venv/pip)."""
    return any(python.parent.glob("python3*._pth"))


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def requirements_hash() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()[:16]


def marker_path() -> Path:
    return state_dir() / "installed.json"


def read_marker() -> dict:
    try:
        return json.loads(marker_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def environment(args) -> Path:
    """The Python that runs the program: embedded (as is) or a private venv."""
    me = Path(sys.executable)
    if is_embedded(me):
        return me
    venv = state_dir() / "venv"
    py = venv_python(venv)
    if args.repair and venv.exists():
        import shutil
        shutil.rmtree(venv, ignore_errors=True)
    if not py.exists():
        say("launcher.venv", dir=venv)
        r = subprocess.run([sys.executable, "-m", "venv", str(venv)])
        if r.returncode or not py.exists():
            raise SystemExit(tr("launcher.venv_fail"))
    return py


def ensure_packages(py: Path, args):
    marker = read_marker()
    if not args.repair and marker.get("requirements") == requirements_hash() and marker.get("python") == str(py):
        return
    if args.offline:
        say("launcher.offline_skip")
        return
    say("launcher.packages")
    base = [str(py), "-m", "pip"]
    if is_embedded(py):
        site = py.parent / "Lib" / "site-packages"
        site.mkdir(parents=True, exist_ok=True)
        if subprocess.run([str(py), "-c", "import pip"], capture_output=True).returncode:
            pyz = state_dir() / "pip.pyz"
            if not pyz.exists() or hashlib.sha256(pyz.read_bytes()).hexdigest() != PIP_PYZ[1]:
                data = urllib.request.urlopen(PIP_PYZ[0], timeout=120).read()
                if hashlib.sha256(data).hexdigest() != PIP_PYZ[1]:
                    raise SystemExit(tr("vendor.bad_hash", what="pip"))
                pyz.write_bytes(data)
            base = [str(py), str(pyz)]
        cmd = base + ["install", "--disable-pip-version-check", "--no-warn-script-location", "--target", str(site),
                      "--upgrade", "-r", str(REQUIREMENTS)]
    else:
        cmd = base + ["install", "--disable-pip-version-check", "-q", "-r", str(REQUIREMENTS)]
    if subprocess.run(cmd).returncode:
        raise SystemExit(tr("launcher.packages_fail"))
    state_dir().mkdir(parents=True, exist_ok=True)
    marker.update(requirements=requirements_hash(), python=str(py), version=__version__)
    marker_path().write_text(json.dumps(marker, indent=1), encoding="utf-8")


def ensure_vendor(args):
    """Windows: TI driver (UAC prompt). macOS: TUSB3410 firmware for the libusb path. Linux: the kernel has a driver."""
    if args.no_driver or sys.platform.startswith("linux"):
        return
    from tvxlink import vendor
    if os.name == "nt" and vendor.driver_installed():
        return
    if sys.platform == "darwin" and vendor.ready():
        return
    if args.offline:
        say("launcher.offline_vendor")
        return
    try:
        vendor.fetch()
        if os.name == "nt":
            vendor.install_driver()
    except Exception as e:  # noqa: BLE001 — the program also runs without the meter (demo)
        say("launcher.vendor_fail", error=e)


def self_check(py: Path) -> bool:
    code = "import sys; sys.path.insert(0, sys.argv[1]); import tvxlink.server, serial, usb; print('ok')"
    r = subprocess.run([str(py), "-c", code, str(ROOT)], capture_output=True, text=True,
                       env={**os.environ, "TVXLINK_DATA": str(data_dir())})
    if r.returncode:
        say("launcher.check_fail", error=(r.stderr or r.stdout).strip().splitlines()[-1:] or "")
    return r.returncode == 0


def check_report(py: Path | None):
    from tvxlink import vendor
    info = {"version": __version__, "python": str(py) if py else None, "embedded": bool(py and is_embedded(py)),
            "state_dir": str(state_dir()), "data_dir": str(data_dir()), "installed": read_marker(),
            "vendor": vendor.status(), "driver_installed": vendor.driver_installed()}
    print(json.dumps(info, indent=1, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="launcher.py", description="TVXLink: install once, then start")
    ap.add_argument("--repair", action="store_true", help="redo the whole setup")
    ap.add_argument("--offline", action="store_true", help="never download anything")
    ap.add_argument("--check", action="store_true", help="show what is installed and exit")
    ap.add_argument("--no-driver", action="store_true", help="do not touch the meter driver / firmware")
    ap.add_argument("--lang", choices=list(i18n.LANGS), help="language of the launcher and the window")
    ap.add_argument("program_args", nargs=argparse.REMAINDER, help="after --: options for the program")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.lang:
        i18n.set_lang(args.lang)
    if sys.version_info < (3, 9):
        raise SystemExit(tr("launcher.old_python", v=sys.version.split()[0]))
    say("launcher.hello", version=__version__)
    if args.check:                       # report only: create nothing
        me = Path(sys.executable)
        py = me if is_embedded(me) else venv_python(state_dir() / "venv")
        check_report(py if py.exists() else None)
        return 0
    py = environment(args)
    ensure_packages(py, args)
    ensure_vendor(args)
    if not self_check(py):
        if args.offline:
            return 1
        say("launcher.retry")
        args.repair = True
        ensure_packages(py, args)
        if not self_check(py):
            return 1
    extra = [a for a in args.program_args if a != "--"]
    if args.lang and "--lang" not in extra:
        extra += ["--lang", args.lang]
    say("launcher.start", data=data_dir())
    try:
        return subprocess.call([str(py), "-c", RUN_SNIPPET, str(ROOT), *extra])
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
