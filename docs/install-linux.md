# Installing JARVIS on Linux

This guide covers Ubuntu, Debian, Linux Mint, Fedora, Arch, and other desktop
Linux distributions. If you use Windows Subsystem for Linux (WSL2), follow this
guide inside your WSL distribution, then see the note at the end about audio.

## Requirements

- A 64-bit desktop Linux distribution with a working microphone and speakers.
- Python 3.10 or newer (`python3 --version`).
- `git` and `curl`.
- An internet connection for the first install.
- An API key from [Groq](https://console.groq.com/keys) (starts with `gsk_`).

## Prebuilt package (no Python needed)

If you do not want to install Python and the source checkout, download
`jarvis_2.2.4_amd64.deb` from the
[latest release](https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant/releases/latest)
and install it:

```bash
sudo apt install ./jarvis_2.2.4_amd64.deb
```

Then launch **JARVIS** from the application menu (or run `jarvis`). The sections
below describe the source installation instead.

Install the basic tools if they are missing:

```bash
# Debian / Ubuntu / Mint
sudo apt-get update && sudo apt-get install -y git curl python3 python3-venv

# Fedora
sudo dnf install -y git curl python3

# Arch
sudo pacman -S --needed git curl python
```

## 1. Clone the repository

```bash
git clone https://github.com/jakirhussainkhan/jarvis-ai-voice-assistant.git
cd jarvis-ai-voice-assistant
```

If you downloaded a source archive instead, extract it and open a terminal in
the extracted folder. The remaining commands must run from that folder.

## 2. Run the installer

The installer creates `venv/`, installs the Python dependencies, and installs
the Linux audio and desktop tools with your system package manager (`sudo` is
used for that step).

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

To add JARVIS to your application menu (and optionally start it at login):

```bash
./venv/bin/python install_desktop.py
```

## 4. Verify the installation

```bash
./venv/bin/python doctor.py
```

Fix anything the readiness report marks as missing. If the GUI starts but you
hear no reply, check that your microphone is not muted at the OS level and that
`alsa-utils` and `pulseaudio-utils` (or PipeWire equivalents) are installed.

## Troubleshooting

- **`no virtualenv found`** — run the installer again from the repository root,
  or create it manually:
  `python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`.
- **No audio on PipeWire** — install `pipewire-alsa` or make sure the
  `pulseaudio` compatibility layer is running.
- **Wayland hotkeys or window control do not work** — log in with an X11
  session, or grant the compositor the required permissions.
- **`python3 -m venv` fails** — install your distribution's venv package
  (`python3-venv` on Debian/Ubuntu).

## WSL2 note

Install and launch JARVIS inside WSL2 as above, and configure WSLg or a PulseAudio
bridge so the container can reach your Windows microphone and speakers. The GUI
needs a working X11 or Wayland display; native Windows installation is simpler
for most users (see [install-windows.md](install-windows.md)).
