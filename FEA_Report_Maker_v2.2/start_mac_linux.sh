#!/usr/bin/env bash
# FEA Report Maker - start script for macOS and Linux
cd "$(dirname "$0")" || exit 1

pause() { if [ -t 0 ]; then read -r -p "Press Enter to close ... " _; fi; }

echo "=============================================================="
echo "  FEA Report Maker"
echo "=============================================================="

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo
  echo "  Python 3 was not found. Install it from https://www.python.org/downloads/"
  echo "  (Linux: sudo apt install python3 python3-venv) and start this file again."
  pause; exit 1
fi

if [ ! -f .venv/fea_ready_22 ]; then
  echo
  echo "  First start: installing the needed Python packages."
  echo "  This needs internet and takes about 1 minute. It is done only once."
  echo
  rm -rf .venv
  "$PY" -m venv .venv || { echo "  Could not create the Python environment (Ubuntu/Debian: sudo apt install python3-venv)."; rm -rf .venv; pause; exit 1; }
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt \
    || { echo "  The packages could not be installed. Check the internet connection and start again."; rm -rf .venv; pause; exit 1; }
  touch .venv/fea_ready_22
  echo "  Packages installed."
fi

.venv/bin/python app/server.py "$@"
echo
echo "  The program has stopped."
pause
