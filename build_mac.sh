#!/usr/bin/env bash
#
# Build a macOS .app bundle for JARVIS and package it as a .dmg.
#
#   ./build_mac.sh
#
# Output: JARVIS.dmg (containing JARVIS.app and an Applications shortcut)
#
# The bundle is ad-hoc signed so it launches locally. A public release without
# an Apple Developer ID is still subject to Gatekeeper's first-launch prompt.

set -euo pipefail

cd "$(dirname "$0")"

# CI sets PYTHON to the interpreter that has requirements.txt installed.
PYTHON="${PYTHON:-python3}"

echo "==> Installing PyInstaller"
"$PYTHON" -m pip install --upgrade pyinstaller

echo "==> Building JARVIS.app"
# Optional ML extras are lazy runtime features, not part of the app bundle.
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name JARVIS \
    --osx-bundle-identifier com.jarvis.assistant \
    --add-data "assets:assets" \
    --hidden-import PyQt5 \
    --hidden-import pynput \
    --hidden-import sounddevice \
    --hidden-import pynput.keyboard._darwin \
    --hidden-import pynput.mouse._darwin \
    --collect-all sounddevice \
    --exclude-module faster_whisper \
    --exclude-module fastembed \
    --exclude-module openwakeword \
    --exclude-module onnxruntime \
    --exclude-module ctranslate2 \
    --exclude-module tokenizers \
    --exclude-module huggingface_hub \
    jarvis_app.py

APP="dist/JARVIS.app"
if [ ! -d "$APP" ]; then
    echo "Build failed: $APP was not produced." >&2
    exit 1
fi

# Ad-hoc signature: no certificate required, and it keeps the bundle internally
# consistent on both Intel and Apple Silicon runners.
if command -v codesign >/dev/null 2>&1; then
    codesign --deep --force --sign - "$APP"
fi

echo "==> Packaging JARVIS.dmg"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

rm -f JARVIS.dmg
hdiutil create \
    -volname "JARVIS" \
    -srcfolder "$STAGE" \
    -ov \
    -format UDZO \
    JARVIS.dmg

echo
echo "Built: JARVIS.dmg"
