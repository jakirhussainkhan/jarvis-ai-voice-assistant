# Installing JARVIS on Windows

This guide targets native Windows 10 and Windows 11 with PowerShell. For
Windows Subsystem for Linux (WSL2), follow [install-linux.md](install-linux.md)
inside your WSL distribution instead.

## Requirements

- Windows 10 (version 2004 or newer) or Windows 11, 64-bit.
- Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/).
  During setup, tick **Add python.exe to PATH**.
- [Git for Windows](https://git-scm.com/download/win).
- A working microphone and speakers.
- An internet connection for the first install.
- An API key from [Groq](https://console.groq.com/keys) (starts with `gsk_`).

Confirm the tools are available in a new PowerShell window:

```powershell
python --version
git --version
```

## 1. Clone the repository

```powershell
git clone https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant.git
cd jarvis-ai-voice-assistant
```

If you downloaded a ZIP instead, extract it and open PowerShell in the extracted
folder. The installer creates `.\venv` relative to the current directory, so all
commands below must run from the repository root.

## 2. Run the installer

Run the local script:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Or use the one-line form from inside the cloned folder:

```powershell
irm https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.ps1 | iex
```

The installer creates `.\venv`, installs `requirements.txt`, and then installs
the Windows-only extras for volume (`pycaw`), window control (`pywinctl`), screen
input (`pyautogui`), brightness, screenshots, and notifications.

When it finishes, create the `.env` file with your Groq API key:

```powershell
"GROQ_API_KEY=your-key-here" | Out-File -Encoding ascii .env
```

Optional settings such as `JARVIS_TTS_VOICE`, `JARVIS_WAKE=0`, or
`USE_LOCAL_LLM=True` can be added to the same `.env` file. See
[`.env.example`](../.env.example) for the full list.

## 3. Launch JARVIS

Start the floating desktop widget and system tray:

```powershell
.\venv\Scripts\python.exe main.py --gui
```

`run.sh` is a Bash script and does not run in native PowerShell. On Windows, use
`main.py --gui` as shown above. Other launch modes:

```powershell
.\venv\Scripts\python.exe main.py            # terminal voice loop
.\venv\Scripts\python.exe main.py --text     # type commands instead of speaking
.\venv\Scripts\python.exe main.py --offline  # local router only, no network
```

To start JARVIS automatically when you sign in, create a shortcut to the command
above in `shell:startup` (press `Win+R`, type `shell:startup`, then place the
shortcut in the folder that opens).

## 4. Verify the installation

```powershell
.\venv\Scripts\python.exe doctor.py
```

Fix anything the readiness report marks as missing.

## Troubleshooting

- **`python` is not recognised** — reinstall Python and enable **Add python.exe
  to PATH**, or use the `py` launcher instead of `python`.
- **`running scripts is disabled on this system`** — use
  `powershell -ExecutionPolicy Bypass -File .\install.ps1`, which only relaxes
  the policy for that one process.
- **No microphone input** — open **Settings → Privacy & security → Microphone**
  and allow desktop apps to use the microphone.
- **No sound** — check the Windows volume mixer and make sure the default
  playback device is correct.
- **Antivirus blocks a helper** — the optional automation packages
  (`pyautogui`, `pywinctl`, `pycaw`) can trigger heuristic warnings. Allow them
  for the project virtualenv, or skip the features that need them.
