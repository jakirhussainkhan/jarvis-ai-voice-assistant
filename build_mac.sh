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
"$PYTHON" -m pip install --upgrade pyinstaller pyautogui pillow

echo "==> Regenerating icons from the canonical Arc Reactor artwork"
"$PYTHON" gen_icon.py

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

echo "==> Preparing a static macOS ffmpeg"
# A downloaded .dmg cannot assume Homebrew or a system ffmpeg, so a static
# binary is bundled next to the app executable further down.
#
# evermeet.cx used to serve https://evermeet.cx/ffmpeg/getrelease/zip. That
# redirect now answers HTTP 410 Gone, which is exactly what failed the v3.0.0
# DMG build (curl exit code 22). Its remaining download mirror is also very
# slow, so the pinned, arch-matched ffmpeg-static build on GitHub's CDN is tried
# first and evermeet's JSON feed (the current release) is only the fallback.
# JARVIS_FFMPEG_URL, JARVIS_FFMPEG_GZ_URL and JARVIS_FFMPEG_INFO_URL override
# each step for pinned or offline builds.
FFMPEG_INFO_URL="${JARVIS_FFMPEG_INFO_URL:-https://evermeet.cx/ffmpeg/info/ffmpeg/release}"
FFMPEG_URL="${JARVIS_FFMPEG_URL:-}"
case "$(uname -m)" in
    arm64) FFMPEG_ARCH="arm64" ;;
    *)     FFMPEG_ARCH="x64" ;;
esac
FFMPEG_GZ_URL="${JARVIS_FFMPEG_GZ_URL:-https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0/ffmpeg-darwin-${FFMPEG_ARCH}.gz}"
FFMPEG_ZIP="build/ffmpeg-macos.zip"
FFMPEG_GZ="build/ffmpeg-macos.gz"
FFMPEG_UNPACKED="build/ffmpeg-macos"
FFMPEG_BIN="build/ffmpeg"

resolve_evermeet_url() {
    "$PYTHON" - "$FFMPEG_INFO_URL" <<'PY'
import json
import sys
import urllib.request

request = urllib.request.Request(
    sys.argv[1], headers={"User-Agent": "jarvis-build/3.0"}
)
with urllib.request.urlopen(request, timeout=60) as response:
    release = json.load(response)
print(release["download"]["zip"]["url"])
PY
}

download_zip() {
    echo "    Downloading $1"
    curl -L --fail --retry 3 --retry-delay 2 --silent --show-error \
        -o "$FFMPEG_ZIP" "$1" || return 1
    # A 410/HTML error page would otherwise surface later as a confusing unzip
    # error, so validate the archive before unpacking it.
    if ! unzip -tq "$FFMPEG_ZIP" >/dev/null 2>&1; then
        echo "    $1 did not return a valid zip archive" >&2
        return 1
    fi
    unzip -q -o "$FFMPEG_ZIP" -d "$FFMPEG_UNPACKED"
}

download_gz() {
    echo "    Downloading $1"
    curl -L --fail --retry 3 --retry-delay 2 --silent --show-error \
        -o "$FFMPEG_GZ" "$1" || return 1
    # gunzip verifies the gzip CRC, so a truncated download cannot be mistaken
    # for a usable binary.
    gunzip -c "$FFMPEG_GZ" > "$FFMPEG_BIN.download" || return 1
    chmod +x "$FFMPEG_BIN.download"
    mv "$FFMPEG_BIN.download" "$FFMPEG_BIN"
}

if [ ! -x "$FFMPEG_BIN" ]; then
    mkdir -p build
    rm -rf "$FFMPEG_UNPACKED" "$FFMPEG_ZIP" "$FFMPEG_GZ" "$FFMPEG_BIN"
    mkdir -p "$FFMPEG_UNPACKED"

    DOWNLOADED=""
    if [ -n "$FFMPEG_URL" ]; then
        download_zip "$FFMPEG_URL" && DOWNLOADED=1
    else
        download_gz "$FFMPEG_GZ_URL" && DOWNLOADED=1
        if [ -z "$DOWNLOADED" ]; then
            echo "    ffmpeg-static is unavailable; resolving evermeet.cx instead" >&2
            if FFMPEG_URL="$(resolve_evermeet_url)"; then
                download_zip "$FFMPEG_URL" && DOWNLOADED=1
            else
                echo "    evermeet.cx could not be resolved" >&2
            fi
        fi
    fi
    if [ -z "$DOWNLOADED" ]; then
        echo "Build failed: no static ffmpeg build could be downloaded." >&2
        exit 1
    fi

    if [ ! -f "$FFMPEG_BIN" ]; then
        if [ -f "$FFMPEG_UNPACKED/ffmpeg" ]; then
            FOUND_FFMPEG="$FFMPEG_UNPACKED/ffmpeg"
        else
            # evermeet normally puts the binary at the archive root; keep a
            # fallback for archives that wrap it in a directory. ``|| true``
            # keeps pipefail from treating head's early close as a failure.
            FOUND_FFMPEG="$(find "$FFMPEG_UNPACKED" -type f -name ffmpeg | head -n 1 || true)"
        fi
        if [ -z "$FOUND_FFMPEG" ]; then
            echo "Build failed: ffmpeg was not found in the downloaded archive." >&2
            exit 1
        fi
        cp "$FOUND_FFMPEG" "$FFMPEG_BIN"
    fi

    # Catch a download that unpacked to something that is not a macOS binary
    # (an HTML error page, a Linux build, ...) before it reaches the .app.
    case "$(file -b "$FFMPEG_BIN")" in
        *Mach-O*) ;;
        *)
            echo "Build failed: the downloaded ffmpeg is not a macOS executable." >&2
            exit 1
            ;;
    esac
fi
chmod +x "$FFMPEG_BIN"

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
    --exclude-module chromadb \
    --exclude-module silero_vad \
    --exclude-module torch \
    --exclude-module torchaudio \
    --exclude-module torchvision \
    --exclude-module ctranslate2 \
    --exclude-module tokenizers \
    --exclude-module huggingface_hub \
    jarvis_app.py

APP="dist/JARVIS.app"
if [ ! -d "$APP" ]; then
    echo "Build failed: $APP was not produced." >&2
    exit 1
fi

# voice._ffmpeg_binary() checks sys._MEIPASS and the executable's directory.
# Contents/MacOS is exactly where the frozen launcher lives, so the bundled
# ffmpeg is found even when the app is launched from Finder.
echo "==> Bundling ffmpeg into the app"
cp "$FFMPEG_BIN" "$APP/Contents/MacOS/ffmpeg"
chmod +x "$APP/Contents/MacOS/ffmpeg"
if [ ! -x "$APP/Contents/MacOS/ffmpeg" ]; then
    echo "Build failed: the app does not contain an executable ffmpeg." >&2
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
        "JARVIS needs microphone access to hear your voice commands.",
    "NSAccessibilityUsageDescription":
        "JARVIS needs accessibility access to control windows and send keyboard shortcuts.",
    "NSAppleEventsUsageDescription":
        "JARVIS needs to control other apps.",
    "LSMinimumSystemVersion": "10.15",
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
