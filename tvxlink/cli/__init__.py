"""Command-line tools: tvx (direct meter access), tvxlink-selftest, tvxlink-lab (lab by chat)."""

from __future__ import annotations

import sys

from .. import i18n


def add_lang(ap):
    ap.add_argument("--lang", choices=list(i18n.LANGS), help="output language (default: saved choice, else uk)")


def apply_lang(args):
    if getattr(args, "lang", None):
        i18n.set_lang(args.lang)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
