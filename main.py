"""Entry point that starts the Jarvis assistant."""

import os
import re
import sys

import confirm
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


def read_input():
    """Return one user turn: typed in text mode, otherwise spoken."""
    if _TEXT:
        try:
            return input("👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print(flush=True)
            return "exit"
    return listen()


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


def main():
    global total_tokens, local_count, agent_count, _TEXT, _OFFLINE

    args = sys.argv[1:]
    if "--check" in args:
        from doctor import report
        print(report())
        return
    if "--version" in args or "-v" in args:
        from config import __version__
        print(f"Jarvis {__version__}")
        return
    if "--gui" in args:
        from gui import main as gui_main
        gui_main()
        return
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

    timers.set_speaker(speak)
    mem = Memory()
    if _TEXT:
        print("Text mode: type a command and press Enter ('exit' to quit).",
              flush=True)
    if _OFFLINE:
        print("Offline mode: the agent is disabled; only local commands run.",
              flush=True)
    speak("Jarvis online.")

    while True:
        user = read_input()
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
        speak(reply)


if __name__ == "__main__":
    main()

