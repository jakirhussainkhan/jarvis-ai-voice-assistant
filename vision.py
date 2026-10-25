"""Screen vision for Jarvis: capture the screen, ask a multimodal model about it,
and (optionally) act on it one bounded step at a time.

Two capabilities live here:

* :func:`look` -- "what's on my screen?", "read this", "summarise the page". One
  screenshot is captured and downscaled, then sent to :data:`config.VISION_MODEL`.
* :func:`act` -- a perceive -> decide -> act loop for goals like "click the login
  button". Each step screenshots the screen, asks the model for exactly one JSON
  action, executes it via :mod:`desktop` (whitelisted xdotool/wmctrl calls only),
  and repeats until the model says ``done`` or ``max_steps`` is reached.

Nothing here can run a shell command; screen actions are limited to move/click/
type/key/scroll/wait.
"""

import base64
import json
import os
import re
import tempfile
import time
from pathlib import Path

from openai import OpenAI

import desktop
import logger
from config import (API_KEY, API_TIMEOUT, BASE_URL, SCREEN_MAX_WIDTH,
                    SCREEN_STEPS, VISION_MODEL)
from digest import digest_text

_client = (
    OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=API_TIMEOUT,
           max_retries=0)
    if API_KEY else None
)

_SYSTEM = (
    "You are Jarvis, looking at a screenshot of the user's Linux desktop. Reply "
    "concisely and in English. Describe only what is actually visible -- never "
    "invent details. If the user asks you to do something, say where it is; the "
    "assistant performs the clicks."
)

_AGENT_SYSTEM = (
    "You are a precise Linux desktop-automation agent. You are shown a full-"
    "resolution screenshot and a goal. Reply with EXACTLY ONE JSON object and "
    "nothing else -- the single next step toward the goal. Allowed objects:\n"
    '{"action":"click","x":<int>,"y":<int>}\n'
    '{"action":"double_click","x":<int>,"y":<int>}\n'
    '{"action":"right_click","x":<int>,"y":<int>}\n'
    '{"action":"type","text":"<text to type>"}\n'
    '{"action":"key","keys":"<e.g. ctrl+s, alt+tab, super, enter>"}\n'
    '{"action":"scroll","direction":"up|down","amount":<int>}\n'
    '{"action":"wait","seconds":<int>}\n'
    '{"action":"done","summary":"<what you achieved>"}\n'
    '{"action":"fail","reason":"<why it is not possible>"}\n'
    "Coordinates are pixels in the screenshot (the full screen). Click only what "
    "you can actually see; never guess a coordinate. Prefer keyboard shortcuts "
    "when you know them. Use \"done\" the moment the goal is met."
)

_ACTION_RE = re.compile(r"\{.*\}", re.S)


def available():
    """True when a vision-capable client is configured."""
    return _client is not None


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #

def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _to_jpeg(png, max_width=None):
    """Convert a PNG screenshot to JPEG (optionally downscaled). Keeps it small."""
    out = Path(png).with_suffix(".jpg")
    commands = []
    if desktop.has("convert"):
        # The ">" in Wx> means "shrink only", never upscale.
        args = ["convert", str(png)]
        if max_width:
            args += ["-resize", f"{max_width}x>"]
        args += ["-quality", "82", str(out)]
        commands.append(args)
    if desktop.has("ffmpeg"):
        vf = f"scale='min({max_width},iw)':-2" if max_width else "scale=iw:ih"
        commands.append(["ffmpeg", "-y", "-loglevel", "error", "-i", str(png),
                         "-vf", vf, "-q:v", "4", str(out)])
    for args in commands:
        rc, _ = desktop._run(args, timeout=20)
        try:
            ok = rc == 0 and out.exists() and out.stat().st_size > 0
        except OSError:
            ok = False
        if ok:
            return out
    return Path(png)


def capture_png(max_width=SCREEN_MAX_WIDTH):
    """Screenshot the screen and return a usable image Path, or None."""
    stamp = f"{os.getpid()}-{int(time.time() * 1000)}"
    png = Path(tempfile.gettempdir()) / f"jarvis-screen-{stamp}.png"
    if desktop.capture_screenshot(png) is None:
        return None
    jpg = _to_jpeg(png, max_width)
    if jpg != png:
        _unlink(png)
    return jpg


# --------------------------------------------------------------------------- #
# ask about an image
# --------------------------------------------------------------------------- #

def ask_image(path, question, model=None, max_tokens=500, system=_SYSTEM):
    """Ask the vision model about a local image file. Returns text (never raises)."""
    if _client is None:
        return "I need a Groq API key in .env before I can look at the screen."
    path = Path(path)
    try:
        data = base64.b64encode(path.read_bytes()).decode()
    except OSError as exc:
        return f"I couldn't read the screenshot: {exc}"
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    try:
        resp = _client.chat.completions.create(
            model=model or VISION_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text",
                     "text": question or "What is on the screen?"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime};base64,{data}"}},
                ]},
            ],
            max_tokens=max_tokens,
        )
        # Parse inside the guard too: HTTP 200 with an unusable body is still an
        # API failure, not a crash the user should see.
        return ((resp.choices[0].message.content or "").strip()
                or "I couldn't read the screen.")
    except Exception as exc:                      # network / model errors
        # Full traceback goes to the API log; the spoken reply stays friendly
        # and never exposes provider internals.
        logger.log_api_error("Vision API request failed", exc)
        return ("I cannot access my vision right now, check the API key or "
                "rate limits.")


def look(question=None, max_width=SCREEN_MAX_WIDTH):
    """Capture the screen and answer ``question`` about it.

    Very long answers are condensed by the cheap digest sub-agent before they
    reach the main model, keeping the conversation context small.
    """
    question = (question or "Describe what is on the screen, briefly.").strip()
    shot = capture_png(max_width=max_width)
    if shot is None:
        return "I couldn't capture the screen."
    try:
        answer = ask_image(shot, question)
        return digest_text(
            answer,
            prompt=f"Answer this screen question concisely: {question}",
        )
    finally:
        _unlink(shot)


# --------------------------------------------------------------------------- #
# perceive -> act loop
# --------------------------------------------------------------------------- #

def _parse_action(text):
    """Extract the first JSON object from a model reply, or None."""
    match = _ACTION_RE.search(text or "")
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _do_action(action):
    """Execute one whitelisted screen action. Returns a short status string."""
    kind = str(action.get("action") or "").strip().lower()
    if kind == "click":
        return desktop.click_at(action.get("x", 0), action.get("y", 0))
    if kind == "double_click":
        return desktop.click_at(action.get("x", 0), action.get("y", 0), double=True)
    if kind == "right_click":
        return desktop.click_at(action.get("x", 0), action.get("y", 0), button=3)
    if kind == "move":
        return desktop.move_mouse(action.get("x", 0), action.get("y", 0))
    if kind == "type":
        return desktop.type_text(action.get("text", ""))
    if kind == "key":
        return desktop.press_keys(action.get("keys", ""))
    if kind == "scroll":
        return desktop.scroll(action.get("direction", "down"), action.get("amount", 3))
    if kind == "wait":
        try:
            seconds = max(0, min(5, int(action.get("seconds", 1))))
        except (TypeError, ValueError):
            seconds = 1
        time.sleep(seconds)
        return f"Waited {seconds}s."
    return f"I don't know the screen action '{kind}'."


def act(goal, max_steps=None, on_step=None):
    """Look at the screen and take up to ``max_steps`` actions to reach ``goal``.

    Returns a short spoken-style summary. Bounded so a wrong guess can never loop
    forever. Coordinates are in full-screen pixels (screenshot is not scaled).
    """
    goal = (goal or "").strip()
    if not goal:
        return "What should I do on the screen?"
    if _client is None:
        return "I need a Groq API key before I can control the screen."
    max_steps = max_steps or SCREEN_STEPS
    history = []
    for _ in range(max_steps):
        shot = capture_png(max_width=None)          # full res -> direct coords
        if shot is None:
            return "I couldn't capture the screen."
        prompt = f"Goal: {goal}"
        if history:
            prompt += "\nActions already taken: " + "; ".join(history) + "."
        raw = ask_image(shot, prompt, max_tokens=300,
                        system=_AGENT_SYSTEM)
        _unlink(shot)
        action = _parse_action(raw)
        if action is None:
            history.append("(the model did not return a valid action)")
            continue
        kind = str(action.get("action") or "").strip().lower()
        if kind == "done":
            return action.get("summary") or "Done."
        if kind == "fail":
            return "I couldn't do that: " + str(action.get("reason") or "no reason given")
        note = _do_action(action)
        history.append(f"{kind} ({note})")
        if on_step is not None:
            try:
                on_step(kind, note)
            except Exception:
                pass
        time.sleep(0.4)
    return f"I stopped after {max_steps} steps; the goal may not be finished."
