"""Entry point that starts the Jarvis assistant."""

import os
import re
import sys

import agent
import config
import confirm
import startup
import voice
from voice import listen, speak
from router import try_local, CAPABILITIES, OFFLINE_REPLY
from agent import run_agent
from memory import Memory
import timers

EXIT_WORDS = ["exit", "quit", "bye", "stop"]

# Filler that can surround an exit word without changing its meaning
# ("please stop", "okay bye now", "stop it"). If real words remain after the
# exit word and filler are removed, the utterance is a command with a target
# (e.g. "quit youtube", "stop the music") -- not a request to end the session.
_EXIT_FILLER = re.compile(
    r"\b(exit|quit|bye|stop|please|jarvis|okay|ok|now|and|then|the|a|an|it|that"
    r"|session)\b",
    re.I,
)

# Explicit requests to close the application itself. These are deliberately
# narrow so "close youtube" and "shut down the computer" keep their real
# meanings; is_exit() still handles the older bare exit/stop/bye words.
_SHUTDOWN_PHRASE = re.compile(
    r"^\s*(?:(?:please|okay|ok|hey|now|and|then)[\s,]+)*"
    r"(?:jarvis[\s,]+)?"
    r"(?:"
    r"close\s+(?:yourself(?:\s+down)?|jarvis)|"
    r"shut\s+(?:yourself\s+down|down\s+jarvis|jarvis\s+down)|"
    r"shutdown\s+jarvis|"
    r"exit\s+jarvis|"
    r"quit\s+jarvis|"
    r"terminate\s+yourself"
    r")"
    r"(?:\s+(?:please|now|okay|ok))?"
    r"\s*[.!?]*$",
    re.I,
)


def is_shutdown(text: str) -> bool:
    """True only for an explicit "close yourself" / "shut down Jarvis" request."""
    return bool(_SHUTDOWN_PHRASE.match((text or "").strip()))


# Type commands instead of speaking (JARVIS_TEXT=1 or `main.py --text`). Useful
# for trying every command when the microphone is unavailable or noisy. Combine
# with JARVIS_MUTE=1 for fast, silent testing.
_TEXT = os.getenv("JARVIS_TEXT", "").strip().lower() not in ("", "0", "false", "no")

# Offline mode (JARVIS_OFFLINE=1 or `main.py --offline`) never calls the agent:
# every command goes through the local router, so the assistant keeps working
# with no connection. Without it, a network failure still degrades gracefully to
# the offline capability list instead of a raw error.
_OFFLINE = os.getenv("JARVIS_OFFLINE", "").strip().lower() not in ("", "0", "false", "no")

# Substrings that identify a network/connection failure (as opposed to a real
# bug), so Jarvis can answer with the offline capability list.
_OFFLINE_ERRORS = (
    "connection", "connect", "timed out", "timeout", "network", "getaddrinfo",
    "unreachable", "name resolution", "temporary failure", "offline", "ssl",
)


def _looks_offline(exc) -> bool:
    """True if ``exc`` looks like the network being unavailable."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(key in text for key in _OFFLINE_ERRORS)

total_tokens = 0
local_count = 0
agent_count = 0


def is_exit(text: str) -> bool:
    """True if text is an exit command, not a command *about* exit.

    Word boundaries keep substrings like 'stop' in 'unstoppable' (or 'start' in
    'restart') from ending the session. A trailing target is respected: "stop"
    and "please stop" end the session, but "quit youtube" / "stop the music" do
    not, so they can be routed as commands instead. Explicit app-close phrases
    such as "close yourself" and "shut down Jarvis" are always exits.
    """
    text = text.lower().strip()
    if is_shutdown(text):
        return True
    if not any(re.search(rf"\b{re.escape(w)}\b", text) for w in EXIT_WORDS):
        return False
    rest = re.sub(r"\s+", " ", _EXIT_FILLER.sub(" ", text)).strip(" .,!?")
    return not rest


def read_input(barge=False):
    """Return one user turn: typed in text mode, otherwise spoken.

    ``barge`` is passed to :func:`voice.listen` so that, right after the user
    talked over a reply, a pure interruption ("stop", "wait", "listen",
    "hold on") is swallowed instead of being dispatched or ending the session.
    """
    if _TEXT:
        try:
            return input("👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print(flush=True)
            return "exit"
    return listen(barge=barge)


def _barge_monitor(on_barge):
    """Build a barge-in monitor (a seam tests can replace without a mic)."""
    return voice.BargeInMonitor(on_barge=on_barge)


def _speak_with_barge_in(reply):
    """Speak ``reply``; return True when the user talked over it.

    While the reply plays, barge-in watches the microphone. On an interruption
    the speech is cut immediately with :func:`voice.stop_speaking`; the app keeps
    running and the loop goes straight back to listening for the next command.
    """
    if _TEXT:
        speak(reply)
        return False

    barged = {"value": False}

    def on_barge():
        barged["value"] = True
        voice.stop_speaking()

    monitor = _barge_monitor(on_barge)
    try:
        started = monitor.start()
    except Exception:
        started = False
    if not started:
        # No barge-in (disabled, muted or push-to-talk): speak normally.
        speak(reply)
        return False
    try:
        speak(reply)
    finally:
        monitor.stop()
    return barged["value"]


def handle(text, memory):
    """Dispatch one utterance through the real path.

    A pending "Are you sure?" confirmation is resolved first: a "yes" runs the
    stored action, anything else cancels it. Otherwise the local router is tried,
    and only a miss reaches the agent. Returns ``(reply, kind, tokens)`` where
    ``kind`` is ``"local"``, ``"cache"`` or ``"agent"``.
    """
    handled, reply = confirm.resolve(text)
    if handled:
        return reply, "local", 0

    local = try_local(text)
    if local is not None:
        return local, "local", 0
    if _OFFLINE:
        return CAPABILITIES, "local", 0
    try:
        reply, tokens = run_agent(text, memory)
    except Exception as exc:                     # never let a network error kill us
        if _looks_offline(exc):
            return OFFLINE_REPLY, "local", 0
        return f"Sorry, I couldn't reach the assistant: {exc}", "local", 0
    return reply, ("cache" if tokens == 0 else "agent"), tokens


def _run_loop():
    """The spoken/typed command loop (everything except the GUI)."""
    global total_tokens, local_count, agent_count

    timers.set_speaker(speak)
    mem = Memory()
    if _TEXT:
        print("Text mode: type a command and press Enter ('exit' to quit).",
              flush=True)
    if _OFFLINE:
        print("Offline mode: the agent is disabled; only local commands run.",
              flush=True)
    speak("Jarvis online.")

    barge_pending = False
    while True:
        user = read_input(barge=barge_pending)
        barge_pending = False
        if not user:
            continue

        if is_exit(user):
            speak("Goodbye, sir")
            sys.exit(0)

        reply, kind, tok = handle(user, mem)
        if kind == "local":
            local_count += 1
            print(f"[STAT] local | tokens={total_tokens} | local={local_count}", flush=True)
        elif kind == "cache":
            print(f"[STAT] cache | tokens={total_tokens} | local={local_count}", flush=True)
        else:
            agent_count += 1
            total_tokens += tok
            print(
                f"[STAT] agent | +{tok} | tokens={total_tokens} | agent={agent_count}",
                flush=True,
            )
        barge_pending = _speak_with_barge_in(reply)


SETUP_WELCOME = (
    "Welcome to JARVIS! You need a Groq API key to use the AI features. "
    "Get a free one at https://console.groq.com/keys"
)


def enable_offline_mode():
    """Restrict the session to local router commands (no LLM calls)."""
    global _OFFLINE
    _OFFLINE = True


def prompt_for_api_key(input_fn=input, print_fn=print):
    """First-run CLI setup: explain, read a key, and save it securely.

    Returns the saved key, or ``None`` when the user enters nothing, cancels,
    or the file cannot be written. ``input_fn``/``print_fn`` are injectable so
    the flow can be tested without a real terminal.
    """
    print_fn(SETUP_WELCOME)
    try:
        key = input_fn(
            "Paste your Groq API key (or press Enter to use offline mode): "
        )
    except (EOFError, KeyboardInterrupt):
        print_fn("")
        key = ""
    key = (key or "").strip()

    if not key:
        print_fn(
            "No key entered. Starting in offline mode: only local commands "
            "will work."
        )
        return None
    if not config.is_api_key_configured(key):
        print_fn(
            "That is a placeholder, not a real key. Starting in offline mode."
        )
        return None
    if not key.startswith("gsk_"):
        print_fn(
            "Note: Groq keys normally start with 'gsk_'. Saving this key anyway."
        )

    try:
        path = config.save_api_key(key)
    except (OSError, ValueError) as exc:
        print_fn(f"Could not save the API key: {exc}")
        print_fn("Starting in offline mode instead.")
        return None

    print_fn(f"API key saved to {path}. Starting JARVIS...")
    return key


def ensure_api_key(input_fn=input, print_fn=print):
    """Run the CLI setup flow when no usable key is configured.

    The GUI handles this itself with a modal dialog, so ``main()`` only calls
    this for non-GUI starts. Returns ``True`` when a key is available.
    """
    if config.is_api_key_configured():
        return True
    if prompt_for_api_key(input_fn=input_fn, print_fn=print_fn) is None:
        enable_offline_mode()
        return False
    return True


def list_microphones():
    """Print sounddevice input devices and exit with a status code."""
    from platform_adapters import _sd

    try:
        devices = _sd.list_input_devices()
    except Exception as exc:
        print(f"Could not list microphones: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        print("Install the capture dependency with "
              "'pip install sounddevice'.", file=sys.stderr)
        return 1

    print("sounddevice input devices:")
    if not devices:
        print("  <none found>")
        print("No microphone is visible to PortAudio. Check the Windows "
              "privacy setting for microphone access.", file=sys.stderr)
        return 1

    for device in devices:
        marker = " (default)" if device.get("default") else ""
        rate = device.get("default_samplerate")
        rate_text = f", default rate {rate:g} Hz" if isinstance(rate, (int, float)) else ""
        print(f"  [{device['index']}] {device['name']}{marker}")
        print(f"      host API: {device.get('hostapi_name', 'unknown')}, "
              f"input channels: {device.get('channels', '?')}{rate_text}")
    print("\nChoose one with JARVIS_MIC_DEVICE=<index or name substring>, "
          "for example JARVIS_MIC_DEVICE=1 or "
          "JARVIS_MIC_DEVICE=\"USB Microphone\".")
    return 0


def main():
    global _TEXT, _OFFLINE

    args = sys.argv[1:]
    if "--list-mics" in args:
        return list_microphones()
    if "--autostart-enable" in args or "--autostart-disable" in args:
        enable = "--autostart-enable" in args
        try:
            startup.set_autostart(enable)
        except OSError as exc:
            print(f"Could not update auto-start: {exc}", file=sys.stderr)
            return 1
        print("Auto-start enabled." if enable else "Auto-start disabled.")
        print(f"Current status: {startup.status_text()}")
        return 0
    if "--local" in args:
        # Force the opt-in Ollama backend for this process only. The default
        # remains Groq, and no Ollama connection is attempted otherwise.
        config.USE_LOCAL_LLM = True
        agent.USE_LOCAL_LLM = True
    if "--check" in args:
        from doctor import report
        print(report())
        return
    if "--version" in args or "-v" in args:
        from config import __version__
        print(f"Jarvis {__version__}")
        return

    gui_mode = "--gui" in args or "--wake" in args
    # The GUI opens its own modal setup dialog. CLI (and --screen) prompts here.
    # --offline and --local explicitly do not need a Groq key.
    if (not gui_mode
            and "--offline" not in args
            and not config.USE_LOCAL_LLM
            and not config.is_api_key_configured()):
        ensure_api_key()

    for flag in ("--screen", "--see", "--look"):
        if flag in args:
            index = args.index(flag)
            words = [a for a in args[index + 1:] if not a.startswith("-")]
            from vision import look
            print(look(" ".join(words) or None))
            return
    if "--text" in args or "-t" in args:
        _TEXT = True
    if "--offline" in args:
        _OFFLINE = True

    # Single instance: a second launch must not create a duplicate tray icon,
    # hotkey grab or microphone. It wakes the running Jarvis and exits.
    import instance
    wake = "--wake" in args
    if wake and instance.signal_wake():
        print("Jarvis is already running -- I asked it to wake up.", flush=True)
        return
    try:
        lock = instance.acquire()
    except instance.AlreadyRunning:
        if instance.signal_wake():
            print("Jarvis is already running -- I asked it to wake up.",
                  flush=True)
        else:
            print("Jarvis is already running.", flush=True)
        return
    try:
        if "--gui" in args or wake:
            from gui import main as gui_main
            gui_main()
            return
        _run_loop()
    finally:
        instance.release(lock)


if __name__ == "__main__":
    main()

