#!/usr/bin/env bash
#
# One-line installer for Jarvis on Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.sh | bash
#   # or, from a checkout:
#   ./install.sh
#
# It creates ./venv, installs requirements.txt, then installs the platform's
# desktop/audio helper tools with the system package manager. Jarvis itself
# never runs these commands -- this script does, once, with your consent.

set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "Python 3.10+ is required, but '$PY' was not found on PATH." >&2
    exit 1
fi

echo "==> Creating the virtual environment (venv/)"
"$PY" -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip >/dev/null

echo "==> Installing the Python dependencies"
pip install -r requirements.txt

os="$(uname -s)"
case "$os" in
    Linux)
        echo "==> Installing the Linux audio/desktop tools (uses sudo)"
        if command -v apt-get >/dev/null 2>&1; then
            sudo apt-get update
            sudo apt-get install -y \
                alsa-utils pulseaudio-utils \
                playerctl brightnessctl wmctrl xdotool \
                scrot imagemagick ffmpeg \
                libnotify-bin xdg-utils xclip \
                portaudio19-dev libportaudio2
            sudo apt-get install -y yt-dlp || echo "    (yt-dlp not in the repos; pip install yt-dlp works too)"
        else
            echo "    This is not an apt system. Install the equivalents of:"
            echo "      alsa-utils pulseaudio-utils playerctl brightnessctl wmctrl xdotool"
            echo "      scrot imagemagick ffmpeg libnotify xdg-utils xclip portaudio"
        fi
        ;;
    Darwin)
        echo "==> Installing the macOS tools (uses Homebrew)"
        if command -v brew >/dev/null 2>&1; then
            brew install portaudio ffmpeg yt-dlp brightness
        else
            echo "    Homebrew is not installed -- see https://brew.sh to get it."
        fi
        ;;
    *)
        echo "    Unrecognised platform '$os' -- installed the Python dependencies only."
        ;;
esac

cat <<'EOF'

Done. Next steps:

  echo "GROQ_API_KEY=your-key-here" > .env     # get one at https://console.groq.com/keys
  ./venv/bin/python doctor.py                  # readiness report
  ./venv/bin/python main.py                    # terminal voice loop
  ./run.sh                                      # the floating GUI widget

EOF
