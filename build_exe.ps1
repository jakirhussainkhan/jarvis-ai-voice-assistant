# Build a single-file Windows executable for JARVIS with PyInstaller.
#
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#
# Output: dist\JARVIS.exe
#
# The executable opens the floating GUI when double-clicked. Command-line flags
# such as --autostart-enable and --check continue to work.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> Installing PyInstaller and the Windows runtime extras"
# These are installed by install.ps1 on a source checkout. A release build must
# include them too, otherwise volume, brightness, screen input, screenshots and
# notifications silently fall back to "not installed" in the frozen EXE.
python -m pip install --upgrade pyinstaller
python -m pip install pycaw comtypes pyautogui screen-brightness-control pillow plyer

Write-Host "==> Building JARVIS.exe"
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name JARVIS `
    --icon "assets\jarvis.ico" `
    --add-data "assets;assets" `
    --hidden-import PyQt5 `
    --hidden-import pynput `
    --hidden-import sounddevice `
    --hidden-import pynput.keyboard._win32 `
    --hidden-import pynput.mouse._win32 `
    --hidden-import pywinctl `
    --hidden-import pyautogui `
    --hidden-import pycaw `
    --hidden-import comtypes `
    --hidden-import screen_brightness_control `
    --hidden-import PIL.ImageGrab `
    --hidden-import plyer.platforms.win.notification `
    --collect-all sounddevice `
    --collect-submodules comtypes `
    --collect-submodules pycaw `
    --collect-data tzdata `
    --exclude-module faster_whisper `
    --exclude-module fastembed `
    --exclude-module openwakeword `
    --exclude-module onnxruntime `
    --exclude-module ctranslate2 `
    --exclude-module tokenizers `
    --exclude-module huggingface_hub `
    jarvis_app.py

$exe = Join-Path $PSScriptRoot "dist\JARVIS.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed: $exe was not produced."
}

Write-Host ""
Write-Host "Built: $exe"
