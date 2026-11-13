# Changelog

## 1.0.0 — 2026-11-13

First public release.

- **Meter link.** PROMAX remote-control protocol (XON/XOFF/ACK) over the TI TUSB3410 bridge:
  COM port on Windows/Linux, user-space libusb driver with firmware upload on macOS.
- **Verified on a real TV EXPLORER II+** (firmware 1.30.1090.280, Windows 11, 2026-11-12):
  reply formats of LV/CN/ME/CB/VB including `<`/`>` flags, *?ST lock codes (DVB-S 6, DVB-S2 8),
  tuning sequence, level settling time (0.77 s), DVB-S2 auto-switch of the meter (*SA re-read
  before every tune), silent first seconds after the port opens. See `docs/TESTING.md`.
- **Window (uk/en).** Meter, Channels, Spectrum, Aiming, Lab, Found, Datalogger, Log & console.
- **Channels.** KingOfSat databases for 9°E, 13°E, 16°E, 19.2°E, 23.5°E, 28.2°E, 42°E with
  QEF thresholds; one-button update with sanity checks; channels verified on a real meter.
- **Spectrum like a radio.** Sweep, click-to-tune, seek until the meter locks, check every
  transponder on screen, saved sweeps, SVG/HTML report.
- **Aiming.** Live level / C/N / MER with peak hold, trend and sound; look angles (Az/El/skew).
- **Lab assistant.** Measurement protocol (meter readings only), session journal, decoding
  threshold (EDGE) detection, BER(C/N) theory vs readings, QPSK↔8PSK pairs, recommended channels.
- **Found channels.** Every lock is logged automatically and kept between runs.
- **Datalogger.** List, download and parse `.dl` files.
- **CLI for people and AI agents.** `tvx`, `tvxlink-selftest`, `tvxlink-lab` (`--json`, `--lang`).
- **One-click start.** `Start-Windows.bat` / `Start-Mac.command` / `start.sh` install Python
  (Windows, embedded), packages and the TI driver/firmware from the official PROMAX package
  (SHA-256 pinned) on the first run, then just start.
- **Safety.** Firmware update and file deletion (`*BO`, `*3141&`, `*SB…`, `*FD…`, `.pro`) are refused
  always, expert mode or not; other write commands (memory, file blocks) need expert mode. Every
  command goes out as exactly one line of printable ASCII, so no command can hide behind CR/LF.
  The local server accepts POST only from its own page's exact Origin.
- **Documentation (en + uk).** How it works, the protocol, how it was reverse-engineered (Ghidra),
  testing and the real-meter validation, AI agents & CLI. The project contains Ukrainian and English
  only; the 2026-11-12 validation data was translated from the earlier version's messages.
