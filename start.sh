#!/bin/sh
# TVXLink for Linux: ./start.sh (needs Python 3.9+ with python3-venv).
# Serial access: add yourself to the dialout group once (sudo usermod -aG dialout $USER).

cd "$(dirname "$0")" || exit 1
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9+ with venv is needed: e.g. sudo apt install python3 python3-venv"
  exit 1
fi
exec "$PY" launcher.py "$@"
