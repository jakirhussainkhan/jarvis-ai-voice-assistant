# JARVIS — Voice Commands

This is the complete, categorized guide to what you can say to JARVIS. Every
command listed here is implemented in the current codebase.

JARVIS understands two kinds of requests:

- **Local commands** are matched by `router.py` and run instantly, offline, with
  no API call. They cover apps, windows, media, volume, brightness, screenshots,
  locking, clipboard, battery/CPU/RAM/disk, time/date, screen reading, folders,
  YouTube, and dictated terminal commands.
- **Agent commands** are natural-language requests that `agent.py` sends to the
  model, which can then call the 44 tools in `tools.py` (web search, Wikipedia,
  weather, memory, timers, screen automation, terminal, CodeAct Python, …).
  These need an internet connection and a valid `GROQ_API_KEY`.

You do not need to memorize which path a request takes — just say it naturally.
Many local patterns also accept a polite prefix such as **"please"**, **"Jarvis"**,
**"Hey Jarvis"**, **"can you"** or **"could you"**.

> **Tip:** Say **"help"** or **"what can you do offline"** at any time to hear the
> built-in offline capability summary.

---

## Quick reference

| I want to… | Say |
| --- | --- |
| Open an app | "Open Telegram", "Launch Firefox", "Open terminal" |
| Open a folder or file | "Open Downloads", "Open my home folder", "Open ~/notes.txt" |
| Close an app or window | "Close Telegram", "Quit Files", "Close the terminal" |
| See what is open | "What windows are open?", "List windows" |
| Check the machine | "Battery", "System status", "CPU", "RAM", "Disk" |
| Check time or date | "What time is it?", "What's the date?" |
| Mute JARVIS | "Turn off the mic", "Stop listening" |
| Control media | "Pause", "Next song", "Go back 10 seconds", "What's playing?" |
| Play something | "Play Kesariya on YouTube", "Play lo-fi beats" |
| Search the web | "Search for the latest Python version", "Look up the weather" |
| Read the screen | "What's on my screen?", "Describe the screen" |
| Remember something | "Remember my name is Jakir" |
| Recall a fact | "What do you remember about me?" |
| Run a command | "Run the command ls -la", "Run ls in the terminal" |
| Run Python | "Use Python to calculate the first 20 Fibonacci numbers" |
| Set a timer | "Set a 5 minute timer", "Remind me to call mom in 10 minutes" |
| Power off safely | "Shut down", then answer **"yes"** |

---

## App and window control

### Open applications

Say **"open"**, **"launch"**, **"start"** or **"show"** followed by an app name.
Matching is fuzzy, so small speech-recognition slips still work.

| Example | Result |
| --- | --- |
| "Open Telegram" | Launches Telegram |
| "Launch Firefox" | Launches Firefox |
| "Open Chrome" | Launches Chrome / Chromium |
| "Open terminal" | Opens the terminal emulator |
| "Open files" | Opens the file manager |
| "Open calculator" | Opens the calculator |
| "Open VLC" | Opens VLC |
| "Open settings" | Opens System Settings |
| **"chrome"** / **"firefox"** / **"vscode"** / **"calculator"** | Bare app names also launch |

If the name is only a weak match, JARVIS asks **"Did you mean <app>?"** instead of
guessing. Protected apps (JARVIS itself, the display server, the desktop shell)
are never opened or closed by mistake.

### Open folders, files, paths and websites

| Example | Result |
| --- | --- |
| "Open Downloads" | Opens `~/Downloads` |
| "Open my home folder" | Opens your home directory |
| "Open Documents" / "Open Pictures" / "Open Music" / "Open Videos" | Opens that folder |
| "Open Desktop" | Opens `~/Desktop` |
| "Open ~/notes.txt" | Opens that file with the default app |
| "Go to example.com" | Opens the site in your browser (agent) |
| "Open the first result" | Screen automation (local vision) |

Recognized folder words: **home, downloads/download, documents/document/docs,
pictures/photos/images, music, videos/movies, desktop, templates, public**.

### Close applications and windows

Say **"close"**, **"quit"**, **"kill"**, **"stop"** or **"exit"** followed by the
target.

| Example | Result |
| --- | --- |
| "Close Telegram" | Closes Telegram gracefully |
| "Quit Files" | Closes the file manager |
| "Kill Calculator" | Stops the calculator |
| "Close the terminal" / "Close console" | Closes the terminal window |
| "Close YouTube" | Closes the YouTube tab only |
| "Close Brave" / "Close Chrome" | Closes that browser app |
| "Close that popup" | Screen automation (agent) |

### Window management

Listing windows is local; focus, minimize and maximize are handled by the agent
using the window tools.

| Example |
| --- |
| "What windows are open?" / "List windows" / "Show windows" (local) |
| "Focus the Chrome window" |
| "Minimize Spotify" |
| "Maximize the Firefox window" |
| "What apps are running?" |

---

## System telemetry and device control

### Battery, CPU, RAM, disk, time and date

| Example | Result |
| --- | --- |
| "Battery" / "What's the battery?" / "Am I charging?" | Battery percentage and charging state |
| "System status" | CPU, RAM and disk usage |
| "CPU" / "RAM" / "Memory" / "Disk" / "Resources" | Resource usage summary |
| "What time is it?" | Current time in Asia/Dhaka |
| "What's the date?" / "Today's date" | Current date in Asia/Dhaka |
| "Full system info" | CPU, RAM, disk, uptime, battery and user (agent) |

> "Open system settings" is treated as an **app launch**, not a status request.

### Microphone mute and unmute

These commands mute **JARVIS's own microphone**, not the system volume. They work
in the GUI, the CLI and with the wake word.

| Example | Result |
| --- | --- |
| "Turn off the mic" | Mutes JARVIS listening |
| "Switch off the microphone" | Mutes JARVIS listening |
| "Mute yourself" / "Mute your mic" / "Mute the mic" | Mutes JARVIS listening |
| "Stop listening" | Mutes JARVIS listening |
| "Turn on the mic" | Unmutes JARVIS listening |
| "Unmute yourself" / "Unmute the mic" | Unmutes JARVIS listening |
| "Start listening" | Unmutes JARVIS listening |

A bare **"mute"** controls **system volume** instead. Say **"mute yourself"** or
**"mute the mic"** when you mean JARVIS.

### Volume

| Example | Result |
| --- | --- |
| "Volume up" / "Increase the volume" / "Raise volume" | One step louder |
| "Volume down" / "Lower the volume" / "Decrease volume" | One step quieter |
| "Louder" / "Make it louder" | One step louder |
| "Quieter" / "Softer" | One step quieter |
| "Set volume to 40" / "Volume at 40" | Sets volume to 40 % |
| "Volume 40" | Sets volume to 40 % |
| "Mute" | Mutes system audio |
| "Unmute" | Unmutes system audio |

### Brightness

| Example | Result |
| --- | --- |
| "Brightness up" / "Increase brightness" / "Brighten" / "Brighter" | One step brighter |
| "Brightness down" / "Lower brightness" / "Dim" / "Dimmer" / "Darker" | One step darker |
| "Set brightness to 40" / "Brightness 40 percent" / "Brightness 40%" | Sets brightness to 40 % |
| "Brightness" / "What's the brightness?" | Asks for a level (changes nothing) |

> Use **"set brightness to 40"** — a bare "brightness 40" reports the current
> level instead of changing it.

### Screenshot, lock and clipboard

| Example | Result |
| --- | --- |
| "Take a screenshot" / "Screenshot" / "Screen shot" | Saves a PNG in `~/Pictures` |
| "Capture the screen" / "Grab the screen" | Saves a PNG in `~/Pictures` |
| "Lock the screen" / "Lock" | Locks the session |
| "Copy" / "Copy that" / "Copy this" | Copies the selection |
| "Cut" / "Cut that" | Cuts the selection |
| "Paste" / "Paste that" | Pastes the clipboard |
| "What's on the clipboard?" / "Read the clipboard" | Reads clipboard text |
| "Show clipboard contents" | Reads clipboard text |

> "Open lock screen settings" is treated as an app launch, not an immediate lock.

### Power actions (confirmation required)

Power actions never run immediately. JARVIS asks **"Are you sure you want to …?"**
and only a clear **"yes"** performs the action. Anything else cancels, and the
request expires after about 15 seconds.

| Example | Action |
| --- | --- |
| "Shut down" / "Shutdown" / "Power off" / "Turn off the computer" | Shut down |
| "Restart" / "Reboot" | Restart |
| "Log out" / "Sign out" | Log out |
| "Suspend" / "Sleep" | Suspend |

### Terminal-free system tools (agent)

| Example |
| --- |
| "Find my file called budget" |
| "Send a notification that the build finished" |
| "What is my username?" |

---

## Media and YouTube

### Playback control

| Example | Result |
| --- | --- |
| "Pause" / "Pause the music" / "Pause the song" | Pauses the active player |
| "Resume" / "Unpause" | Resumes playback |
| "Next" / "Next song" / "Next track" / "Skip" | Next track |
| "Previous" / "Previous song" / "Previous track" | Previous track |
| "Go back" (no number) | Previous track |
| "Stop the music" / "Stop the song" / "Stop playback" | Stops playback |
| "Go back 10 seconds" / "Rewind 10 seconds" / "Back 10 seconds" | Seeks back |
| "Skip forward 30 seconds" / "Forward 30 seconds" / "Ahead 30 sec" | Seeks forward |
| "What's playing?" / "Now playing" / "What song is this?" | Current artist and title |
| "Current track" | Current artist and title |

> A bare **"stop"** in a continuous chat session ends the conversation. Use
> **"stop the music"** for playback.

### Play a song or video

You do not need to say "YouTube". JARVIS resolves the top result with `yt-dlp`,
shows you the real title, and asks before playing:

> JARVIS: **"I found <video title>. Do you want me to play this?"**
> You: **"yes"**

| Example | Action |
| --- | --- |
| "Play Kesariya" | Resolves and confirms the top result |
| "Play the song Kesariya" | Same, with filler words ignored |
| "Play Lo-Fi Beats on YouTube" | Resolves and confirms |
| "Watch lofi beats" | Resolves and confirms |
| "Listen to Arijit Singh" | Resolves and confirms |
| "Search Kesariya and play it" | Resolves the song, then plays it |
| "Find lo-fi music then play" | Resolves the song, then plays it |
| "Play the second song" | Clicks the second visible result on screen |
| "Play this video" / "Play that one" / "Play it" | Acts on what is currently on screen |

If the exact video cannot be resolved, JARVIS opens the YouTube results page
instead. If a YouTube tab is already open, JARVIS navigates that tab in place
rather than opening a duplicate.

### Open, search and close YouTube

| Example | Result |
| --- | --- |
| "Open YouTube" | Opens YouTube, or raises the existing tab |
| "Search cats on YouTube" | Opens YouTube search results (agent) |
| "Search for lo-fi music" | Web search (agent) |
| "Close YouTube" | Closes the YouTube tab only |

### Fullscreen and windowed playback

| Example | Result |
| --- | --- |
| "Full screen" / "Fullscreen" | Player fullscreen (`f` key) |
| "Play in full screen" | Player fullscreen |
| "Maximize the video" / "Maximize the browser" | Player fullscreen |
| "Normal screen" / "Windowed" | Leaves fullscreen |
| "Exit full screen" / "Leave fullscreen" | Leaves fullscreen |

### Screen-reading and on-screen actions

| Example | Result |
| --- | --- |
| "What's on my screen?" | Describes the screen |
| "Describe the screen" / "Read the screen" | Describes the screen |
| "Summarize the page on my screen" | Summarizes the visible page |
| "Show me what's on my screen" | Describes the screen |
| "Click the play button on my screen" | Clicks it |
| "Play the video on my screen" | Plays the visible video |
| "Click the login button" | Screen automation (agent) |
| "Fill in the search box and search" | Screen automation (agent) |

Destructive or financial screen goals — delete, uninstall, format, buy, pay,
send, submit, accept — are confirmed before JARVIS acts.

---

## Memory and notes

Long-term facts are stored in SQLite and retrieved semantically with ChromaDB,
so you can ask about them in your own words.

### Remember and recall

| Example | Result |
| --- | --- |
| "Remember my name is Jakir" | Saves the fact |
| "Remember that I prefer tea" | Saves the preference |
| "Remember my birthday is in May" | Saves the birthday |
| "My name is Jakir" | Saves it as a durable fact |
| "What do you remember about me?" | Lists saved facts |
| "What is my name?" | Recalls the matching fact |
| "What's my favourite drink?" | Semantic recall |
| "Do you remember my birthday?" | Semantic recall |

### Forget

| Example | Result |
| --- | --- |
| "Forget my name" | Deletes facts containing "name" |
| "Forget my birthday" | Deletes birthday facts |
| "Forget that I prefer tea" | Deletes matching facts |

### Notes

| Example | Result |
| --- | --- |
| "Make a note that the server restarts on Friday" | Appends to `~/jarvis_notes.md` |
| "Save a note: buy milk" | Appends a timestamped note |
| "Note that the build passed" | Appends a timestamped note |

Notes are timestamped and appended to `~/jarvis_notes.md`.

---

## Web, vision and screen

### Live internet answers (agent)

These use `net.py` (keyless DuckDuckGo search, page fetch, Wikipedia, weather)
and need a connection.

| Example | Result |
| --- | --- |
| "Search for the latest Python version" | Web search |
| "Look up the weather in Dhaka" | Weather |
| "What's the weather?" | Local-area weather |
| "Who is Ada Lovelace?" | Web / Wikipedia answer |
| "What happened in tech today?" | Current news |
| "Wikipedia quantum computing" | Wikipedia summary |
| "Tell me about the Eiffel Tower from Wikipedia" | Wikipedia summary |
| "Read the page at example.com/article" | Fetches and reads the page |
| "Search for lo-fi music" | Web search |

### Screen reading and automation

Phrases that name the screen are answered locally; broader visual tasks and UI
actions go to the agent.

| Example | Result |
| --- | --- |
| "What's on my screen?" | Vision description (local) |
| "Read this" / "Read this page" | Reads the visible content |
| "Describe what you see" | Vision description |
| "Summarize the page" | Summarizes the screen |
| "Look at my screen and tell me the error" | Vision answer |
| "Click the login button" | Clicks it |
| "Open the first result" | Opens the first visible result (local vision) |
| "Close that popup" | Closes the visible popup |
| "Scroll down" / "Scroll up" | Scrolls the focused window |
| "Type hello world" | Types into the focused window |
| "Press ctrl+s" / "Press alt+tab" | Sends a key combination |
| "Move the mouse to 500, 300" | Moves the pointer |
| "Click at 500, 300" | Clicks at coordinates |

Screen automation uses a bounded perceive → act loop (`JARVIS_SCREEN_STEPS`,
default 6). It is best-effort and X11-friendly; some Wayland sessions restrict
synthetic input.

---

## Terminal, CodeAct and automation

Speech is unreliable, so terminal commands pass `shell.py`'s three safety layers:
privilege escalation and catastrophic commands are refused outright, state-changing
commands require a spoken **"yes"**, and everything else runs with a timeout and
capped output.

### Run a command directly (offline, local router)

Say an explicit marker — **"run the command"**, **"execute the command"**,
**"run in the terminal"**, **"terminal"**, **"shell"**, **"bash"**, **"command"**
or **"cmd"** — followed by the command. No model call is made.

| Example |
| --- |
| "Run the command ls -la" |
| "Run this command df -h" |
| "Execute the command git status" |
| "Terminal ls -la" |
| "Shell git status" |
| "Bash echo hello" |
| "Command uname -a" |
| "Cmd dir" |

### Ask the agent to run a command

Say **"run"** or **"execute"** without a marker, and the agent picks the right
terminal tool. This is also how the task example **"Run command ls"** is handled.

| Example | Result |
| --- | --- |
| "Run ls" | Agent → `fn_run_command` |
| "Run command ls" | Agent → `fn_run_command` |
| "Execute df -h" | Agent → `fn_run_command` |
| "Run ls in the terminal and show me the output" | Agent → terminal tool |
| "Open the terminal and run htop" | Agent → visible terminal |
| "Run git log and show me the output" | Agent → terminal tool |

### Dictate a command into a visible terminal (X11)

JARVIS opens a real terminal, types the command as if you had typed it, and
presses Enter — nothing runs invisibly.

| Example |
| --- |
| "Open terminal and type ls -la" |
| "Type ls -la in the terminal" |
| "Open a terminal and enter git status" |
| "Type python --version into the terminal" |

> Dictation needs `xdotool` and X11. On Wayland JARVIS says it cannot type into a
> terminal; use the guarded terminal tool instead.

### What is refused

- `sudo`, `su`, `doas`, `pkexec` — JARVIS never becomes root.
- Shutdown, reboot, suspend, logout, or killing init from the terminal.
- `rm -rf`, `mkfs`, `dd` to a device, fork bombs, piping downloads into a shell,
  partition editors, credential changes, and direct writes to `/dev`.
- Anything else that mutates files or packages is read back and confirmed first.

### CodeAct: run a Python script (agent)

`fn_execute_python` runs a short, self-contained script in a subprocess with a
15-second timeout. It is a guardrail, not a full sandbox.

| Example | Result |
| --- | --- |
| "Use Python to calculate the first 20 Fibonacci numbers" | Runs a script and returns output |
| "Run a Python script that sums 1 to 100" | Runs the calculation |
| "Use Python to find the longest word in this sentence" | Runs the script |
| "Write and run a Python script to format this list" | Runs the script |
| "Calculate 17 factorial with Python" | Runs the script |

**Allowed:** safe standard-library code, normal calculations, string/data
processing, writing files in the working directory.

**Blocked before execution:** `os`, `subprocess`, `shutil`, `sys`, `socket`,
`ctypes`, `multiprocessing`, `pty`, `resource`, `signal`, `importlib`, `builtins`,
`pickle`, `marshal`, `shelve`, `urllib`, `http`, plus `eval`, `exec`, `compile`,
`__import__`, `getattr`, `setattr`, introspection dunders, and dangerous methods
such as `system`, `popen`, `rmtree`, `unlink`, `fork`, `kill`, `spawn`.

### Keyboard, mouse and window tools (agent)

| Example |
| --- | --- |
| "Press ctrl+s" / "Press alt+tab" / "Press super" |
| "Click at 800, 450" / "Double-click at 300, 200" |
| "Move the mouse to 100, 100" |
| "Type this into the window: hello" |
| "Scroll down three times" |
| "Focus the terminal window" |
| "Maximize Firefox" / "Maximize the Firefox window" |

---

## Timers, reminders and conversation

### Timers and reminders (agent)

| Example | Result |
| --- | --- |
| "Set a 5 minute timer" | Speaks when it finishes |
| "Set a timer for 90 seconds" | Speaks when it finishes |
| "Set a 10 minute timer called pasta" | Named timer |
| "Remind me to call mom in 10 minutes" | Spoken reminder |
| "Remind me in 5 minutes to stretch" | Spoken reminder |

### Conversation control

| Say | Effect |
| --- | --- |
| "Yes" / "Yeah" / "Sure" | Confirms a pending action |
| "No" / anything else | Cancels a pending action |
| "Bye" / "Exit" / "Quit" (on their own) | Ends a CLI session |
| "Stop" (on its own) | Ends a continuous chat session |
| "Stop listening" | Mutes the microphone |
| "Stop the music" | Stops playback |
| "Help" | Reads the offline capability list |

---

## MCP tools and custom commands

If an MCP server is configured, its tools are exposed to the agent automatically
as `mcp__<server>__<tool>` and can be used through natural language. For example,
with a filesystem server connected:

- "List the files in my project folder"
- "Read the README from the filesystem server"

MCP setup is documented in [`docs/mcp-setup.md`](../mcp-setup.md). A broken or
unavailable server is contained and logged; it cannot crash the voice loop.

---

## Phrasing tips

1. **Be direct.** "Open Downloads" works better than "Could you perhaps open the
   Downloads folder for me?" — though polite prefixes are accepted.
2. **Separate microphone mute from volume mute.** "Mute yourself" mutes JARVIS;
   "mute" mutes system audio.
3. **Use a number for seeking.** "Go back 10 seconds" works; a bare "go back"
   means the previous track.
4. **Say "set … to …" for exact levels.** "Set volume to 40" and "set brightness
   to 40" are unambiguous.
5. **Answer confirmations clearly.** A clear "yes" runs a pending power, terminal,
   YouTube or screen action. Anything else cancels.
6. **Use "on my screen" for visual context.** "Play the second video on my screen"
   acts on what is visible instead of searching for those words.

---

## Related documentation

- [README](../../README.md) — install, run and architecture overview
- [Linux install guide](../install-linux.md)
- [Windows install guide](../install-windows.md)
- [macOS install guide](../install-macos.md)
- [MCP setup](../mcp-setup.md)
