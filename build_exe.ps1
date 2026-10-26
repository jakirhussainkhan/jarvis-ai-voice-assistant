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

Write-Host "==> Regenerating icons from the canonical Arc Reactor artwork"
python gen_icon.py
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }

Write-Host "==> Preparing a static ffmpeg.exe"
# Jarvis decodes every edge-tts MP3 with ffmpeg and also uses it for media and
# screenshots. Bundling the static Windows build means an end user never has to
# install ffmpeg (or add it to PATH) for the EXE to speak.
$ffmpeg = Join-Path $PSScriptRoot "ffmpeg.exe"
if (-not (Test-Path $ffmpeg)) {
    $ProgressPreference = "SilentlyContinue"
    $url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    $zip = Join-Path ([System.IO.Path]::GetTempPath()) "ffmpeg-release-essentials.zip"
    $unpacked = Join-Path ([System.IO.Path]::GetTempPath()) "jarvis-ffmpeg"
    Write-Host "    Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    if (Test-Path $unpacked) {
        Remove-Item $unpacked -Recurse -Force
    }
    Expand-Archive -Path $zip -DestinationPath $unpacked -Force
    $binary = Get-ChildItem -Path $unpacked -Recurse -Filter "ffmpeg.exe" |
        Select-Object -First 1
    if (-not $binary) {
        throw "ffmpeg.exe was not found in the downloaded archive."
    }
    Copy-Item $binary.FullName $ffmpeg -Force
}
if (-not (Test-Path $ffmpeg)) {
    throw "Build failed: ffmpeg.exe is missing."
}

Write-Host "==> Building JARVIS.exe"
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name JARVIS `
    --icon "assets\jarvis.ico" `
    --add-data "assets;assets" `
    --add-binary "ffmpeg.exe;." `
    --hidden-import PyQt5 `
    --hidden-import pynput `
    --hidden-import=sounddevice `
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
    --collect-data sounddevice `
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
