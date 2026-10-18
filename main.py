"""Entry point that starts the Jarvis assistant."""

import re

import confirm
from voice import listen, speak
from router import try_local
from agent import run_agent
from memory import Memory

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

total_tokens = 0
local_count = 0
agent_count = 0


def is_exit(text: str) -> bool:
    """True if text is an exit command, not a command *about* exit.

    Word boundaries keep substrings like 'stop' in 'unstoppable' (or 'start' in
    'restart') from ending the session. A trailing target is respected: "stop"
    and "please stop" end the session, but "quit youtube" / "stop the music" do
    not, so they can be routed as commands instead.
    """
    text = text.lower().strip()
    if not any(re.search(rf"\b{re.escape(w)}\b", text) for w in EXIT_WORDS):
        return False
    rest = re.sub(r"\s+", " ", _EXIT_FILLER.sub(" ", text)).strip(" .,!?")
    return not rest


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
