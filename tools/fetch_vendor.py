"""Same as `python -m tvxlink.vendor`: fetch the TI TUSB3410 driver and firmware from PROMAX NetUpdate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tvxlink.vendor import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
