"""Where the program keeps user data (shared by the window and the CLI tools)."""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """<home>/TVXLink, or $TVXLINK_DATA.

    Deliberately outside OneDrive/iCloud: sync clients stall writes to fresh files
    (TimeoutError Errno 60 in the middle of a scan). Data of the pre-1.0 versions
    (<home>/TVExplorer, $TVEXPLORER_DATA) is used if it exists and nothing newer does."""
    env = os.environ.get("TVXLINK_DATA") or os.environ.get("TVEXPLORER_DATA")
    if env:
        return Path(env)
    home = Path.home()
    new, old = home / "TVXLink", home / "TVExplorer"
    return old if old.is_dir() and not new.exists() else new


def state_dir() -> Path:
    """Per-user folder for the runtime (Python environment, vendor driver/firmware):
    %LOCALAPPDATA%\\TVXLink, ~/Library/Application Support/TVXLink or ~/.local/share/tvxlink."""
    import sys
    if os.environ.get("TVXLINK_STATE"):
        return Path(os.environ["TVXLINK_STATE"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TVXLink"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TVXLink"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "tvxlink"
