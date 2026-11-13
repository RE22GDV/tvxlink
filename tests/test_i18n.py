"""Interface languages: both dictionaries complete, every key used by the code exists,
no hard-coded Cyrillic text left in the program.  python -m unittest tests.test_i18n"""
import json
import re
import unittest
from pathlib import Path

from tvxlink import i18n, lab, scan

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "tvxlink"
TOOLS = ROOT / "tools"
CYRILLIC = re.compile("[\u0400-\u04ff]")


def used_keys() -> set:
    keys = set()
    for p in list(PKG.rglob("*.py")) + list(TOOLS.glob("*.py")):
        t = p.read_text(encoding="utf-8")
        keys |= set(re.findall(r'\btr\(\s*"([a-z][\w.]+)"', t))
        keys |= set(re.findall(r'"((?:col|meas|cli)\.[\w.]+)"', t))
    ui = (PKG / "ui.html").read_text(encoding="utf-8")
    keys |= set(re.findall(r'data-i18n(?:-html|-title|-ph)?="([\w.]+)"', ui))
    keys |= set(k for k in re.findall(r"'((?:ui|status|kind|why|diff|src|meas)\.[\w.]+)'", ui) if not k.endswith("."))
    keys |= {"status." + s for s in scan.STATUSES} | {"kind." + k for k in lab.KINDS}
    keys |= {"src." + s for s in ("scan", "seek", "pair", "tune", "aim", "measure", "lab", "edge")}
    keys |= {"diff." + d for d in ("standard", "sr", "fec", "pol", "band")}
    keys |= {"why." + w for w in ("verified", "fta_mpeg2", "fta_count", "dvbs", "threshold", "narrow", "beam", "same_pol")}
    keys |= {"ui.upd." + s for s in ("start", "download", "done", "error")}
    return {k for k in keys if not k.endswith(".")}      # "kind." + code etc. are prefixes


class I18nTest(unittest.TestCase):
    def test_same_keys_and_placeholders(self):
        uk, en = i18n.catalog("uk"), i18n.catalog("en")
        self.assertEqual(set(uk) ^ set(en), set())
        for k in uk:
            self.assertEqual(set(re.findall(r"\{(\w+)\}", uk[k])), set(re.findall(r"\{(\w+)\}", en[k])), k)
            self.assertTrue(uk[k].strip() and en[k].strip(), k)

    def test_every_used_key_exists(self):
        missing = sorted(used_keys() - set(i18n.catalog("en")))
        self.assertEqual(missing, [], "keys used in the code but missing in the dictionaries")

    def test_english_has_no_cyrillic(self):
        bad = [k for k, v in i18n.catalog("en").items() if CYRILLIC.search(v)]
        self.assertEqual(bad, [])

    def test_no_hardcoded_cyrillic(self):
        """User-visible text goes through the dictionaries; only data mappings may hold Cyrillic."""
        allowed = re.compile(r"ПН\|ПД|_SOUTH_WEST")
        offenders = []
        # tools/screenshots.py holds the translated notes of the real session (data, not UI text)
        files = list(PKG.rglob("*.py")) + [t for t in TOOLS.glob("*.py") if t.name != "screenshots.py"] + [PKG / "ui.html"]
        for p in files:
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if CYRILLIC.search(line) and not allowed.search(line) and "Мова / Language" not in line:
                    offenders.append(f"{p.name}:{n}: {line.strip()[:80]}")
        self.assertEqual(offenders, [])

    def test_tr_and_switch(self):
        try:
            i18n.set_lang("en")
            self.assertEqual(i18n.tr("err.if_range", mhz="3000.0"), "IF 3000.0 MHz is outside 950–2150 MHz")
            i18n.set_lang("uk")
            self.assertIn("ПЧ 3000.0 МГц", i18n.tr("err.if_range", mhz="3000.0"))
            self.assertEqual(i18n.tr("no.such.key"), "no.such.key")          # a gap never crashes
        finally:
            i18n.set_lang("uk")
        with self.assertRaises(ValueError):
            i18n.set_lang("xx")

    def test_locale_files_are_valid_json(self):
        for lang in i18n.LANGS:
            json.loads((PKG / "locales" / f"{lang}.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class NoRussianTest(unittest.TestCase):
    """The project is published in Ukrainian and English only."""

    def test_no_russian_text_in_the_repository(self):
        import os
        # Russian-only letters and four common Russian words, escaped so this file stays clean
        russian = re.compile("[\u042b\u044b\u042d\u044d\u042a\u044a\u0401\u0451]"
                             "|\\b(\u0447\u0442\u043e|\u043f\u0440\u0438\u0431\u043e\u0440\\w*|\u043d\u0435\u0442|\u0435\u0441\u043b\u0438)\\b")
        skip_dirs = {".git", "__pycache__", "satellites_raw", "originals", "extracted", "decompiled", "build", "dist"}
        bad = []
        for top, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.endswith(".egg-info")]
            for name in files:
                p = Path(top) / name
                if p.suffix in (".png", ".gz", ".bin", ".zip", ".pyc") or (p.parent.name == "satellites" and name[0].isdigit()):
                    continue                     # satellite databases: third-party channel names
                try:
                    text = p.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    if russian.search(line):
                        bad.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:80]}")
        self.assertEqual(bad, [], "\n".join(bad[:40]))
