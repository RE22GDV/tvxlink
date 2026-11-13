# Contributing

Thank you for helping! A few rules keep TVXLink reliable on a real meter.

1. **Tests first.** `python -m unittest discover -s tests -t .` must pass. Everything runs
   against the built-in simulator, no meter needed.
2. **Two languages.** Every user-visible string goes through `tr("key")` (Python) or `T('key')`
   (page) and lives in both `tvxlink/locales/uk.json` and `en.json`. `tests/test_i18n.py` fails on
   a missing key or on hard-coded Cyrillic text.
3. **Protocol changes need evidence.** If you change `device.py` / `protocol.py`, attach the raw
   reply (`tvx --json q …`) and what the meter screen showed. Update `docs/PROTOCOL.md`.
4. **Never** add firmware-update or delete commands (`*BO`, `*3141&`, `*SB…`, `*FD…`, `.pro` upload).
5. **No vendor binaries** in the repository — `tvxlink/vendor.py` fetches them from PROMAX.
6. Code style: standard library first, no web frameworks, comments in English.
