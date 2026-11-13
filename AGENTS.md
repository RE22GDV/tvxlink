# TVXLink — instructions for AI agents

TVXLink drives a PROMAX TV EXPLORER II / II+ satellite meter over USB: tuning, live
measurements, spectrum sweeps, antenna aiming, a lab assistant and a datalogger reader.
Python standard library (HTTP server + one HTML page), no web framework. Any coding agent
(Claude Code, Codex, Cursor, …) can test the meter and run a lab session through the CLI.
Answer the user in their language; the program speaks Ukrainian and English (`--lang uk|en`).

## Map

| Path | What |
|---|---|
| `Start-Windows.bat`, `Start-Mac.command`, `start.sh`, `launcher.py` | one-click install + start (see `launcher.py --help`) |
| `tvxlink/protocol.py` | PROMAX ASCII protocol, XON/XOFF/ACK, file transfer |
| `tvxlink/device.py` | meter commands: identify, measure, `lock_status()` (*?ST), `tune_satellite()` |
| `tvxlink/serialport.py`, `tvxlink/tusb3410.py` | COM port (Windows/Linux) / libusb user-space driver (macOS) |
| `tvxlink/scan.py` | background jobs: scan, seek, sweep, decoding-threshold run |
| `tvxlink/lab.py` | protocol, journal, look angles, BER theory, QPSK↔8PSK pairs, channel recommendations |
| `tvxlink/satdb.py`, `tvxlink/satellites/` | KingOfSat databases, `verified.json`, `st_codes.json` |
| `tvxlink/server.py`, `tvxlink/ui.html` | local web interface (127.0.0.1:8765) |
| `tvxlink/i18n.py`, `tvxlink/locales/{uk,en}.json` | every user-visible string |
| `tvxlink/cli/` | `tvx`, `tvxlink-selftest`, `tvxlink-lab` |
| `tvxlink/vendor.py` | fetches the TI driver/firmware from the PROMAX package (SHA-256 pinned) |
| `docs/` | how it works, protocol, reverse engineering, testing, AI agents & CLI (en + uk) |
| `reverse/` | reverse-engineering notes (Ukrainian) and scripts |

User data: `~/TVXLink` (`%USERPROFILE%\TVXLink`, override `TVXLINK_DATA`) — logs with hex of
every byte, sessions, protocol, found channels. Runtime: `%LOCALAPPDATA%\TVXLink` etc.

## Without a meter

```
python -m unittest discover -s tests -t .          # everything, against the simulator
python -m tvxlink.cli.tvx --port demo ident          # any CLI command works with --port demo
```

## With a meter: test it

1. `python -m tvxlink.cli.tvx ports` — a port marked ★ (VID 0451 PID 3410). None on Windows →
   run `Start-Windows.bat` once (it installs the driver; the user must accept the UAC prompt).
2. `python -m tvxlink.cli.selftest` — read-only, safe. Read `<data>/selftest/<time>/report.md`
   and explain every FAIL/WARN.
3. Quick checks: `tvx listen 3` (XON once a second), `tvx q LV CN ME CB VB FR SA ST`,
   `tvx --json measure -n 5`. Confirm formats by asking the user what the meter screen shows.
   *?LV/CN return NAK while the meter shows the TV picture — ask the user to press [12].
4. **Before any tuning ask the user** whether a dish with an LNB is connected and which
   satellite it points at: tuning puts 13/18 V on the input.
   Then `python -m tvxlink.cli.selftest --tune --verified N --sat POS` (N = index in
   `tvxlink/satellites/verified.json`) or `--tune --rf … --sr … --fec … --lnb … --sat … --pol …`.
5. Fix by the report's recommendations (`device.py`: `parse_measure`, `LOCK_ST`, `lock_status`,
   `_settle`), run the unit tests and the self-test again, record new protocol facts in
   `docs/PROTOCOL.md`.

`tvx` and the self-test never send firmware or delete commands; `tvx set` passes only tuning
commands (`*SA *LB *CR0 *RA *CO0 *SI *FR …`).

## With a meter: run a lab session by chat

The user writes short messages ("aimed", "photographed the constellation", "moving the dish away").
Answer with `python -m tvxlink.cli.lab …` — it talks to the **open window** (which holds the COM
port, so do not run `tvx`/selftest meanwhile). No window → ask the user to start it, or run
`python -m tvxlink --no-browser` in the background, then `tvxlink-lab connect`.

Rules:
- **Protocol = meter readings only** (`record`, `edge save`, `pair --save`). Numbers the user reads
  from the screen go to the journal (`note`), marked as such. Never write model values.
- **Journal = the user's words** (`note`): photos taken, picture or black screen, how the dish moved.
- Check `status` before `record`; without lock, say so and ask.
- Lab-manual transponder tables age fast — pick channels with `tvxlink-lab rec POS`.
- Demo rows are marked DEMO; offer to hide them (✕ in the Lab tab) before a real session.

| The user writes | You do |
|---|---|
| "connected", "let's start" | `status` (not connected → `connect`); `rec 13E` (or their satellite), suggest QPSK #1 |
| "pointing at 13E" | `angles 13E` — say Az/El; `tune 13E 1`; then `status` on request: is C/N/MER growing? |
| "locked", "optimum" | `status` → if ✔ `record optimum --note "<what they said about the picture>"`; remind to photograph the [12] screen |
| "photographed …", "picture ok / black" | `note "…"` |
| "constellation" | remind: short [22] → Constellation, photo with axes; then `note` |
| "moving the dish", "threshold", "EDGE" | `edge start`; `edge show` along the way; after the loss `edge save`, `edge stop`; remind half a step back and a photo |
| "dish back" | `status` (lock back?) + `note` |
| "8PSK", "compare" | remind to enable DVB-S2 recognition on the meter; `pair 13E`, `pair 13E 1 --save` (or `tune 13E 1 --8psk` + `record 8psk`) |
| "what's recorded?" | `list`; check: optimum + photos, EDGE + photo, QPSK and 8PSK + photos |
| "done" | `list`; give the paths `lab/protocol.csv`, `lab/journal.txt`, `sessions/…_edge/edge.csv` |

## Rules for code changes

- Never implement or send firmware/memory commands (`*3141&`, `*BO`, `*SB…`, `.pro`, `*FD…`).
- Every new user-visible string: both locale files (`tests/test_i18n.py` checks).
- `.bat` files keep CRLF (`.gitattributes`).
