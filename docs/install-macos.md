# Installing JARVIS on macOS

This guide targets macOS 12 (Monterey) or newer on Apple Silicon and Intel Macs.

## Requirements

- macOS 12 or newer.
- Python 3.10 or newer (`python3 --version`).
- [Homebrew](https://brew.sh) for the audio and desktop helper tools.
- Xcode Command Line Tools (Homebrew installs them if needed):
  `xcode-select --install`.
- A working microphone and speakers.
- An internet connection for the first install.
- An API key from [Groq](https://console.groq.com/keys) (starts with `gsk_`).

## Prebuilt app (no Python needed)

Download `JARVIS.dmg` from the
[latest release](https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant/releases/latest),
open it, and drag **JARVIS** into **Applications**. The first time, right-click
the app and choose **Open** (the build is ad-hoc signed, so Gatekeeper asks for a
one-time confirmation).

If macOS reports that the app is damaged, run this once and open it again:

```bash
xattr -dr com.apple.quarantine /Applications/JARVIS.app
```

The sections below describe the source installation instead.

If Python is missing, install it with Homebrew:

```bash
brew install python git
```

## 1. Clone the repository

```bash
git clone https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant.git
cd jarvis-ai-voice-assistant
```

If you downloaded a source archive instead, extract it and open Terminal in the
extracted folder. The remaining commands must run from that folder.

## 2. Run the installer

The installer creates `venv/`, installs the Python dependencies, and installs
the macOS helper tools with Homebrew (`portaudio`, `ffmpeg`, `yt-dlp`, and
`brightness`).

Run it directly:

```bash
chmod +x install.sh
./install.sh
```

Or use the one-line form from inside the cloned folder:

```bash
curl -fsSL https://raw.githubusercontent.com/jakirhussainkhan/jarvis-ai-voice-assistant/main/install.sh | bash
```

> The installer installs into the current directory. Run the one-liner from
> inside `jarvis-ai-voice-assistant`, not from your home directory.

When it finishes, add your Groq API key:

```bash
echo "GROQ_API_KEY=your-key-here" > .env
```

Optional settings such as `JARVIS_TTS_VOICE`, `JARVIS_WAKE=0`, or
`USE_LOCAL_LLM=True` can be added to the same `.env` file. See
[`.env.example`](../.env.example) for the full list.

## 3. Launch JARVIS

Start the floating desktop widget and system tray:

```bash
./run.sh
```

`run.sh` uses the project virtualenv automatically. You can also start the GUI
directly:

```bash
./venv/bin/python main.py --gui
```

Other launch modes:

```bash
./venv/bin/python main.py            # terminal voice loop
./venv/bin/python main.py --text     # type commands instead of speaking
./venv/bin/python main.py --offline  # local router only, no network
```

On first launch macOS asks for permission to use the microphone. Grant it to
Terminal (or to Python) in **System Settings → Privacy & Security → Microphone**.
If you use screen vision or desktop automation, also grant **Screen Recording**
and **Accessibility** permissions, then restart JARVIS.

## 4. Verify the installation

```bash
./venv/bin/python doctor.py
```

Fix anything the readiness report marks as missing.

## Troubleshooting

- **`no virtualenv found`** — run the installer again from the repository root,
  or create it manually:
  `python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`.
- **`portaudio` build errors** — run `brew install portaudio` and reinstall the
  Python requirements inside the virtualenv.
- **No microphone input** — check **System Settings → Privacy & Security →
  Microphone** and restart the terminal app after granting access.
- **Hotkeys do not work** — allow the terminal app under **System Settings →
  Privacy & Security → Accessibility**.
- **Apple Silicon and Rosetta warnings** — use an arm64 Python from Homebrew
  (`/opt/homebrew/bin/python3`) so native wheels are installed.
