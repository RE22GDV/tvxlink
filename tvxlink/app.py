"""`tvxlink` / `python -m tvxlink` — start the local web interface."""

from __future__ import annotations

import argparse

from . import __version__


def main(argv=None):
    p = argparse.ArgumentParser(prog="tvxlink", description="TVXLink: PROMAX TV EXPLORER II / II+ in the browser")
    p.add_argument("--port", type=int, default=8765, help="local port (default 8765)")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser")
    p.add_argument("--lang", choices=["uk", "en"], help="interface language (default: saved choice, else Ukrainian)")
    p.add_argument("--version", action="version", version=f"tvxlink {__version__}")
    a = p.parse_args(argv)
    from .server import main as serve        # imported late: the server opens the data folder on import
    serve(port=a.port, open_browser=not a.no_browser, lang=a.lang)


if __name__ == "__main__":
    main()
