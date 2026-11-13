"""Interface languages (Ukrainian and English).

All user-visible text lives in ``tvxlink/locales/<lang>.json``. The same
files are used by the server (``tr()``), the command-line tools and the web
page (served at ``/api/i18n``), so there is one dictionary to keep complete.

Language choice, first match wins:
  1. ``set_lang()`` (the page's language switch, ``--lang`` of the CLI tools);
  2. the ``TVXLINK_LANG`` environment variable;
  3. ``settings.json`` in the data folder (saved by the page);
  4. Ukrainian.
"""

from __future__ import annotations

import json
import os
import threading
from functools import lru_cache
from pathlib import Path

LOCALE_DIR = Path(__file__).resolve().parent / "locales"
LANGS = ("uk", "en")
DEFAULT = "uk"

_lock = threading.Lock()
_lang: str | None = None


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict:
    """The whole dictionary of one language (key → text with {placeholders})."""
    try:
        return json.loads((LOCALE_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}      # dictionary not written yet: tr() falls back to the key


def _settings_file() -> Path:
    from .paths import data_dir
    return data_dir() / "settings.json"


def _saved_lang() -> str | None:
    try:
        return json.loads(_settings_file().read_text(encoding="utf-8")).get("lang")
    except (OSError, ValueError, AttributeError):
        return None


def get_lang() -> str:
    global _lang
    if _lang is None:
        env = (os.environ.get("TVXLINK_LANG") or "").lower()[:2]
        _lang = env if env in LANGS else (_saved_lang() if _saved_lang() in LANGS else DEFAULT)
    return _lang


def set_lang(lang: str, save: bool = False) -> str:
    """Switch the language for this process; ``save`` also remembers it in settings.json."""
    global _lang
    lang = (lang or "").lower()[:2]
    if lang not in LANGS:
        raise ValueError(f"unknown language {lang!r}, expected one of {', '.join(LANGS)}")
    with _lock:
        _lang = lang
        if save:
            f = _settings_file()
            try:
                data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            except ValueError:
                data = {}
            data["lang"] = lang
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return lang


def tr(key: str, lang: str | None = None, **kw) -> str:
    """Text for ``key`` in the current language; ``{name}`` placeholders are filled from ``kw``.

    A missing key falls back to English and then to the key itself, so a gap in a
    dictionary never breaks the program (the i18n test reports such gaps)."""
    lang = lang or get_lang()
    text = catalog(lang).get(key)
    if text is None:
        text = catalog("en").get(key, key)
    try:
        return text.format(**kw) if kw else text
    except (KeyError, IndexError, ValueError):
        return text
