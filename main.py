"""Entry point that starts the Jarvis assistant."""

import re

from voice import listen, speak
from router import try_local
from agent import run_agent
from memory import Memory

EXIT_WORDS = ["exit", "quit", "bye", "stop"]

total_tokens = 0
local_count = 0
agent_count = 0


def is_exit(text: str) -> bool:
    """True if text contains an exit word as a whole word.

    Word boundaries keep substrings like 'stop' in 'unstoppable' (or 'start' in
    'restart') from ending the session.
    """
    text = text.lower().strip()
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in EXIT_WORDS)


def handle(text, memory):
    """Dispatch one utterance through the real path.

    The local router is tried first; only a miss reaches the agent. Returns
    ``(reply, kind, tokens)`` where ``kind`` is ``"local"``, ``"cache"`` or
    ``"agent"``.
    """
    local = try_local(text)
    if local:
        return local, "local", 0
    reply, tokens = run_agent(text, memory)
    return reply, ("cache" if tokens == 0 else "agent"), tokens


def main():
    global total_tokens, local_count, agent_count
    mem = Memory()
    speak("Jarvis online.")

    while True:
        user = listen()
        if not user:
            continue

        if is_exit(user):
            speak(f"Shutting down. Session: {local_count} local, "
                  f"{agent_count} agent, {total_tokens} tokens.")
            break

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
