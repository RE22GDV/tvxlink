"""Build the shipped transponder databases from KingOfSat (for maintainers).

    python -m tvxlink.cli.buildsats              download and build every position
    python -m tvxlink.cli.buildsats --raw DIR    build from already downloaded files

Writes tvxlink/satellites/<position>.json. Users update their databases with the
"Update channel databases" button instead (tvxlink/satdb.py: update_all).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..satdb import PACKAGE_DIR as OUT_DIR, POSITIONS, build, fetch


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tvxlink-buildsats", description="Build the shipped satellite databases from KingOfSat")
    ap.add_argument("--raw", type=Path, help="folder with kos_<pos>.html and dl_<pos>.bin")
    ap.add_argument("positions", nargs="*", default=list(POSITIONS))
    a = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for pos in a.positions:
        if a.raw:
            ini = (a.raw / f"dl_{pos}.bin").read_text(encoding="utf-8", errors="replace")
            page = (a.raw / f"kos_{pos}.html").read_text(encoding="utf-8", errors="replace")
        else:
            ini = fetch(f"https://en.kingofsat.net/dl.php?pos={pos}")
            time.sleep(1.5)
            page = fetch(f"https://en.kingofsat.net/pos-{pos}")
            time.sleep(1.5)
        data = build(pos, ini, page)
        (OUT_DIR / f"{pos}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tps = data["transponders"]
        print(f"{pos}: {len(tps)} transponders, demodulated by the meter: {sum(t['supported'] for t in tps)}, "
              f"with channels: {sum(1 for t in tps if t['channels'])}")


if __name__ == "__main__":
    main()
