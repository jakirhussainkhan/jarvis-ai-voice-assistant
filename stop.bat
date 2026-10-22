@echo off
rem ===========================================================================
rem  JARVIS - stop the app, including when it is hidden in the tray
rem
rem  Double-click this file or run  stop.bat .
rem  It asks the running instance to quit gracefully (main.py --quit) and
rem  force-stops only this project's process tree if it does not close in time.
rem ===========================================================================
setlocal
cd /d "%~dp0"

set "PY=%~dp0venv\Scripts\python.exe"
set "APP=%~dp0main.py"

if not exist "%PY%" (
    echo [JARVIS] Python virtual environment not found:
    echo          %PY%
    pause
    exit /b 1
)

echo Stopping JARVIS...
"%PY%" "%APP%" --quit

rem Give it a moment to shut down cleanly, then kill only this project's
rem python process tree (covers a frozen window or a stuck reply).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$dir = '%~dp0'; $end = (Get-Date).AddSeconds(8); do { Start-Sleep -Milliseconds 400; $p = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like '*python*' -and $_.CommandLine -and $_.CommandLine -like ('*' + $dir + '*') -and $_.CommandLine -like '*main.py*' }) } while ($p.Count -gt 0 -and (Get-Date) -lt $end); if ($p.Count -gt 0) { $p | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }; Write-Host 'JARVIS did not exit in time and was force-stopped.' } else { Write-Host 'JARVIS stopped.' }"

timeout /t 3 /nobreak >nul 2>&1
endlocal
exit /b 0
