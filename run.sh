#!/usr/bin/env bash
# Launch the Jarvis GUI: the floating ChatGPT-Voice-style card + system tray.
#
#   ./run.sh
#
# Left-click the orb (or the tray icon) to talk; Esc or the ✕ button hides it to
# the tray. Start speaking while Jarvis talks to interrupt it. Stop it with
# Ctrl+C here, or "Quit" in the tray menu.
#
# Optional: ./venv/bin/python install_desktop.py   # add it to the app menu
set -euo pipefail

DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

if [ ! -x venv/bin/python ]; then
    echo "Jarvis: no virtualenv found in $DIR/venv" >&2
    echo "Create it first:" >&2
    echo "  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

exec ./venv/bin/python main.py --gui "$@"
