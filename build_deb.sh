#!/usr/bin/env bash
#
# Build a self-contained Debian package for JARVIS.
#
#   ./build_deb.sh
#
# Output: jarvis_<version>_amd64.deb
#
# The GUI is frozen with PyInstaller into a single binary at /usr/bin/jarvis,
# then packaged with its desktop entry and icon. No Python installation is
# required on the target machine.

set -euo pipefail
umask 022

cd "$(dirname "$0")"

# CI sets PYTHON to the interpreter that has requirements.txt installed.
PYTHON="${PYTHON:-python3}"

VERSION="$("$PYTHON" -c 'import config; print(config.__version__)')"
ARCH="amd64"
PKG="jarvis_${VERSION}_${ARCH}"
ROOT="build/deb/${PKG}"

echo "==> Installing PyInstaller and icon tooling"
"$PYTHON" -m pip install --upgrade pyinstaller pillow

echo "==> Regenerating icons from the canonical Arc Reactor artwork"
"$PYTHON" gen_icon.py

echo "==> Building the frozen GUI binary"
# Optional ML extras (offline STT/embeddings/VAD) are deliberately not frozen:
# they are loaded lazily at runtime and fall back cleanly when absent. Silero VAD
# pulls the torch stack (multi-GB on Linux), which pushed the .deb past GitHub's
# 2 GB release-asset limit; the calibrated RMS gate remains the fallback anyway.
# ffmpeg is intentionally NOT bundled: the .deb depends on the system package
# so the distro keeps it patched, and voice._ffmpeg_binary() falls back to PATH.
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --onefile \
    --name jarvis \
    --add-data "assets:assets" \
    --hidden-import PyQt5 \
    --hidden-import pynput \
    --hidden-import sounddevice \
    --hidden-import pynput.keyboard._xorg \
    --hidden-import pynput.mouse._xorg \
    --hidden-import pywinctl \
    --collect-all sounddevice \
    --collect-all onnxruntime \
    --collect-all chromadb \
    --collect-all pywinctl \
    --collect-data tzdata \
    --exclude-module faster_whisper \
    --exclude-module fastembed \
    --exclude-module openwakeword \
    --exclude-module silero_vad \
    --exclude-module torch \
    --exclude-module torchaudio \
    --exclude-module torchvision \
    --exclude-module nvidia \
    --exclude-module triton \
    --exclude-module ctranslate2 \
    --exclude-module tokenizers \
    --exclude-module huggingface_hub \
    jarvis_app.py

if [ ! -x dist/jarvis ]; then
    echo "Build failed: dist/jarvis was not produced." >&2
    exit 1
fi

echo "==> Staging the Debian package"
rm -rf build/deb
mkdir -p \
    "${ROOT}/DEBIAN" \
    "${ROOT}/usr/bin" \
    "${ROOT}/usr/share/applications" \
    "${ROOT}/usr/share/icons/hicolor/256x256/apps"

install -m 0755 dist/jarvis "${ROOT}/usr/bin/jarvis"
install -m 0644 assets/jarvis-256.png \
    "${ROOT}/usr/share/icons/hicolor/256x256/apps/jarvis.png"

cat > "${ROOT}/usr/share/applications/jarvis.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Version=1.0
Name=JARVIS
GenericName=Voice Assistant
Comment=Voice-controlled desktop assistant (English)
Exec=/usr/bin/jarvis
Icon=jarvis
Terminal=false
StartupNotify=false
Categories=Utility;Accessibility;
Keywords=jarvis;voice;assistant;
EOF

cat > "${ROOT}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 0755 "${ROOT}/DEBIAN/postinst"

INSTALLED_SIZE="$(du -sk "${ROOT}" | cut -f1)"

cat > "${ROOT}/DEBIAN/control" <<EOF
Package: jarvis
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE}
Maintainer: Jakir Hussain Khan <jakirhussainkhan@users.noreply.github.com>
Depends: python3, ffmpeg, libportaudio2, libasound2 | libasound2t64, tzdata, libxcb-xinerama0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-shape0, libxkbcommon-x11-0, libegl1, libgl1, libdbus-1-3
Recommends: pulseaudio-utils, playerctl, brightnessctl, wmctrl, xdotool, yt-dlp, scrot, xclip
Description: JARVIS voice-controlled desktop assistant
 JARVIS is a voice-controlled desktop assistant for Linux, Windows and macOS.
 It handles local commands offline (apps, media, windows, volume, brightness,
 timers, notes) and uses a Groq-backed agent for web and AI answers.
 .
 This package installs a self-contained GUI binary and a desktop entry.
EOF

echo "==> Building ${PKG}.deb"
dpkg-deb --build --root-owner-group "${ROOT}" "${PKG}.deb"

echo
echo "Built: ${PKG}.deb"
