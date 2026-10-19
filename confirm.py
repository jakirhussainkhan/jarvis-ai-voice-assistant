"""Short-lived "Are you sure?" confirmations for destructive actions.

Destructive or disruptive actions (shutdown, restart, logout, suspend, and any
future delete) must never run straight from a misheard utterance. Instead the
router registers a zero-argument *action* here and asks a question. The next
utterance is intercepted by :func:`resolve` at the top of ``main.handle``:

* a clear "yes" runs the action,
* anything else cancels it,
* the request expires after ``_TTL`` seconds.

Only one request can be pending at a time.
"""

import os
import re
import time

_TTL = 15.0  # seconds a pending request stays valid

# A deliberately narrow "yes": misheard words should cancel, not trigger.
_YES = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|do it|go ahead|"
    r"yes please|please do)\b",
    re.I,
)

_pending = {"action": None, "prompt": "", "expires": 0.0}


def request(action, prompt):
    """Register a pending action and return the question to speak."""
    _pending["action"] = action
    _pending["prompt"] = prompt
    _pending["expires"] = time.monotonic() + _TTL
    return prompt


def ask_action(prompt, action):
    """Prompt-first alias of :func:`request`.

    Reads more naturally at a call site (``ask_action("Are you sure?", do_it)``)
    while sharing the same single pending slot.
    """
    return request(action, prompt)


def is_pending():
    return _pending["action"] is not None


def prompt():
    return _pending["prompt"]


def clear():
    _pending["action"] = None
    _pending["prompt"] = ""
    _pending["expires"] = 0.0


def is_yes(text):
    return bool(_YES.search(text or ""))


def resolve(text, now=None):
    """Consume a pending request. Returns ``(handled, reply)``.

    ``handled`` is False when nothing was pending (the utterance should be
    processed normally). When handled, ``reply`` is the confirmation result.
    """
    action = _pending["action"]
    if action is None:
        return False, None

    now = time.monotonic() if now is None else now
    expired = now > _pending["expires"]
    clear()

    if expired:
        return True, "That request expired."
    if is_yes(text):
        try:
            return True, action()
        except Exception as exc:  # a failed action must not kill the loop
            return True, f"Sorry, that failed: {exc}"
    return True, "Cancelled."
