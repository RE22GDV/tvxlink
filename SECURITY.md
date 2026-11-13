# Security

- The server listens on **127.0.0.1 only**, checks the `Host` header and accepts POST requests only
  from its own page (the `Origin` must match scheme, host and port exactly), so other sites open in
  your browser cannot drive the meter.
- Nothing is sent anywhere except: the KingOfSat pages when you press "Update channel databases",
  and on the first start the Python / pip / PROMAX NetUpdate / 7-Zip downloads, each checked by SHA-256.
- Firmware update and file deletion (`*BO`, `*3141`, `*SB`, `*FD`, `.pro`) are refused always,
  expert mode or not. Other commands that write to the meter's memory or file system need
  "expert mode" in the console. The CLI tools never send any of them.
- Every command goes to the meter as exactly one line of printable ASCII: CR/LF, other control
  bytes or a second `*` are refused before the port, so no command can be hidden behind another.

Found a problem? Report it privately on GitHub (**Security → Report a vulnerability**) or open an issue.
