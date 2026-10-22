#!/usr/bin/env bash
#
# Build a macOS .app bundle for JARVIS and package it as a .dmg.
#
#   ./build_mac.sh
#
# Output: JARVIS.dmg (containing JARVIS.app, an Applications shortcut and a
# short first-launch note)
#
# The bundle is ad-hoc signed so it launches locally. A public release without
# an Apple Developer ID is still subject to Gatekeeper's first-launch prompt;
# the note inside the DMG explains the two-click workaround.

set -euo pipefail

cd "$(dirname "$0")"

# CI sets PYTHON to the interpreter that has requirements.txt installed.
PYTHON="${PYTHON:-python3}"

echo "==> Installing PyInstaller and the macOS input dependencies"
# pyautogui is not needed on Linux, but the shared window adapter uses it on
# macOS for pointer/keyboard control. Without it the bundled app silently loses
# screen-control commands.
"$PYTHON" -m pip install --upgrade pyinstaller pyautogui

echo "==> Preparing the application icon"
ICONSET="build/JARVIS.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"

# iconutil requires the complete iconset. sips is part of every macOS install.
make_icon() {
    sips -z "$1" "$1" assets/jarvis-256.png --out "$ICONSET/$2" >/dev/null
}
make_icon 16   icon_16x16.png
make_icon 32   icon_16x16@2x.png
make_icon 32   icon_32x32.png
make_icon 64   icon_32x32@2x.png
make_icon 128  icon_128x128.png
make_icon 256  icon_128x128@2x.png
make_icon 256  icon_256x256.png
make_icon 512  icon_256x256@2x.png
make_icon 512  icon_512x512.png
make_icon 1024 icon_512x512@2x.png
iconutil -c icns "$ICONSET" -o "build/JARVIS.icns"

echo "==> Building JARVIS.app"
# Optional ML extras are lazy runtime features, not part of the app bundle.
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name JARVIS \
    --icon "build/JARVIS.icns" \
    --osx-bundle-identifier com.jarvis.assistant \
    --add-data "assets:assets" \
    --hidden-import PyQt5 \
    --hidden-import pynput \
    --hidden-import sounddevice \
    --hidden-import pynput.keyboard._darwin \
    --hidden-import pynput.mouse._darwin \
    --hidden-import pywinctl \
    --hidden-import pyautogui \
    --hidden-import PIL.ImageGrab \
    --collect-all sounddevice \
    --collect-submodules pywinctl \
    --collect-data tzdata \
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

# macOS ties microphone/automation/folder prompts to these usage strings. A
# frozen bundle without them is denied access (or terminated) the first time it
# opens the microphone, which would make the downloaded app look broken.
echo "==> Adding macOS permission descriptions"
PLIST="$APP/Contents/Info.plist"
"$PYTHON" - "$PLIST" <<'PY'
import plistlib
import sys

path = sys.argv[1]
with open(path, "rb") as handle:
    info = plistlib.load(handle)

info.update({
    "NSMicrophoneUsageDescription":
        "JARVIS needs the microphone to hear your voice commands.",
    "NSAppleEventsUsageDescription":
        "JARVIS uses AppleScript to control apps, media, volume and notifications.",
    "NSDesktopFolderUsageDescription":
        "JARVIS opens files and folders you ask it to work with.",
    "NSDocumentsFolderUsageDescription":
        "JARVIS opens files and folders you ask it to work with.",
    "NSDownloadsFolderUsageDescription":
        "JARVIS opens files and folders you ask it to work with.",
})

with open(path, "wb") as handle:
    plistlib.dump(info, handle)
PY

# Ad-hoc signature: no certificate required, and it keeps the bundle internally
# consistent on both Intel and Apple Silicon runners. It must run after the
# Info.plist edits so the signature covers the final bundle.
if command -v codesign >/dev/null 2>&1; then
    codesign --deep --force --sign - "$APP"
fi

echo "==> Packaging JARVIS.dmg"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

cat > "$STAGE/READ ME FIRST.txt" <<'EOF'
JARVIS for macOS
================

1. Drag JARVIS.app into the Applications folder.

2. The first time you open it, right-click (or Control-click) JARVIS.app and
   choose Open, then click Open again. This is only needed once; it tells
   macOS that you trust this free, unsigned build.

   If macOS says the app is damaged, run this once in Terminal and open it
   again:
       xattr -dr com.apple.quarantine /Applications/JARVIS.app

3. JARVIS opens a small window and asks for your free Groq API key. Create one
   at https://console.groq.com/keys and paste it in. The key is stored only in
   your own home folder (~/.jarvis/.env).

4. Allow the microphone when macOS asks. For screen vision, window control and
   global hotkeys, also grant Screen Recording and Accessibility in
   System Settings > Privacy & Security, then restart JARVIS.
EOF

rm -f JARVIS.dmg
hdiutil create \
    -volname "JARVIS" \
    -srcfolder "$STAGE" \
    -ov \
    -format UDZO \
    JARVIS.dmg

echo
echo "Built: JARVIS.dmg"
