#!/bin/bash
# TVXLink for macOS: double-click (first time: right-click → Open → Open).
# The first start installs everything once; later starts just open the program.

cd "$(dirname "$0")" || exit 1
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Потрібен Python 3.9+ з python.org — відкриваю сторінку завантаження."
  echo "Python 3.9+ from python.org is needed — opening the download page."
  open "https://www.python.org/downloads/macos/"
  read -r -n1 -p "Press any key…"; exit 1
fi
"$PY" launcher.py "$@"
st=$?
if [ $st -ne 0 ]; then echo; read -r -n1 -p "Press any key…"; fi
exit $st
