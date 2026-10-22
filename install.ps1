# One-line installer for Jarvis on Windows (PowerShell).
#
#   # from a checkout:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   # or, one line:
#   irm https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.ps1 | iex
#
# It creates .\venv, installs requirements.txt, then the Windows-only extras that
# give Jarvis volume control (pycaw), window control (pywinctl), screen input
# (pyautogui), brightness, screenshots and notifications.

$ErrorActionPreference = "Stop"
# Support both `.\install.ps1` and `irm ... | iex`: when the script is piped,
# PowerShell leaves $PSScriptRoot empty, so fall back to the current directory
# (the cloned checkout the user is standing in).
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location -Path $ScriptDir

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python 3.10+ is required, but 'python' was not found on PATH."
    exit 1
}

Write-Host "==> Creating the virtual environment (venv\)"
python -m venv venv

Write-Host "==> Installing the Python dependencies"
& .\venv\Scripts\python.exe -m pip install --upgrade pip
& .\venv\Scripts\pip.exe install -r requirements.txt

Write-Host "==> Installing the Windows-only extras"
& .\venv\Scripts\pip.exe install `
    pycaw comtypes `
    pywinctl pyautogui `
    screen-brightness-control pillow plyer

Write-Host ""
Write-Host "Done. Next steps:"
Write-Host '  .\start.bat                          # launch the floating widget (no console)'
Write-Host '  .\start.bat --console                # ...or with a log window'
Write-Host '  .\start.bat is safe to run twice: the second launch wakes the running app.'
Write-Host '  .\stop.bat                           # stop it again (works from the tray)'
Write-Host ''
Write-Host '  Add the API key in the first-run dialog, or put it in .env:'
Write-Host '  "GROQ_API_KEY=your-key-here" | Out-File -Encoding ascii .env   # https://console.groq.com/keys'
Write-Host ''
Write-Host '  .\venv\Scripts\python.exe main.py --check    # readiness report'
Write-Host '  .\venv\Scripts\python.exe main.py            # terminal voice loop'
Write-Host ''
