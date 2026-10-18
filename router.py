"""Routes user input to the appropriate local action or the agent.

``try_local`` handles a fixed set of commands entirely offline (regex only, no
API calls): open/close apps, YouTube, media, volume, brightness, screenshot,
lock, power (confirmation-gated), time, date and system status. It returns a
string reply, or ``None`` when nothing matches so the agent can decide.

Safety rules enforced here and in :mod:`desktop`:

* only whitelisted functions run -- never ``sudo``, never arbitrary shell,
* ``close/quit/kill X`` is handled before YouTube so it can never become a
  search query,
* destructive actions (shutdown/restart/logout/suspend) are registered with
  :mod:`confirm` and only run after a spoken "yes",
* a weak app-name match asks "Did you mean <app>?" instead of guessing.
"""

import re
from datetime import datetime

import psutil

import confirm
import desktop

# --------------------------------------------------------------------------- #
# patterns
# --------------------------------------------------------------------------- #

# A search verb means the user wants information, so the router leaves it to the
# agent (which can call the search tools).
SEARCH_VERB = re.compile(r"\b(search|look ?up|find)\b", re.I)

# --- power (confirmation-gated) -------------------------------------------- #
POWER_WORD = re.compile(
    r"\b(shut ?down|power ?off|shut ?off|restart|reboot|log ?out|sign ?out|"
    r"suspend|sleep)\b",
    re.I,
)
POWER_FILLER = re.compile(
    r"\b(the|my|this|that|computer|pc|system|machine|laptop|jarvis|"
    r"now|please|again|session)\b",
    re.I,
)

# --- media (playerctl) ------------------------------------------------------ #
MEDIA_PAUSE = re.compile(r"\b(pause|unpause|resume)\b", re.I)
MEDIA_NEXT = re.compile(r"\b(next|skip)\b", re.I)
MEDIA_PREV = re.compile(r"\b(previous|prev|go back)\b", re.I)
MEDIA_STOP = re.compile(
    r"\bstop\b[^.]{0,20}\b(music|song|video|media|playback|audio|track)\b", re.I
)
MEDIA_NOUN = re.compile(r"\b(song|track|music|video|media|playback|audio)\b", re.I)

# --- volume / brightness ---------------------------------------------------- #
VOL_WORD = re.compile(r"\b(volume|vol|sound|audio)\b", re.I)
VOL_MUTE = re.compile(r"\bmute\b", re.I)
VOL_UNMUTE = re.compile(r"\bunmute\b", re.I)
# "make it louder/quieter" is a volume request even without the word "volume".
VOL_ADJ = re.compile(r"\b(louder|louden|quieter|softer)\b", re.I)
VOL_UP = re.compile(r"\b(up|increase|raise|louder|louden)\b", re.I)
VOL_DOWN = re.compile(r"\b(down|decrease|lower|quieter|softer|soften)\b", re.I)
VOL_SET = re.compile(r"\b(?:to|at)\s+(\d{1,3})\b")
VOL_SET2 = re.compile(r"\b(?:volume|vol)\b[^0-9]{0,12}(\d{1,3})\b", re.I)

# "brighter"/"darker" are separate words (a bare "bright" does not match them).
BRIGHT_WORD = re.compile(
    r"\b(brightness|bright|brighten|brighter|dim|dimmer|darker)\b", re.I
)
BRIGHT_UP = re.compile(r"\b(up|increase|raise|brighter|brighten)\b", re.I)
BRIGHT_DOWN = re.compile(r"\b(down|decrease|lower|dimmer|dim|darker)\b", re.I)

# --- screenshot / lock ------------------------------------------------------ #
SCREENSHOT_WORD = re.compile(
    r"\b(screenshot|screen ?shot|capture the screen|grab the screen)\b", re.I
)
LOCK_WORD = re.compile(r"\block\b", re.I)

# --- system / time / date --------------------------------------------------- #
BATTERY_WORD = re.compile(r"\b(battery|charge|charging)\b", re.I)
# Only real status words match, so "open system settings"/"open system monitor"
# are not mistaken for a status request (bare "system" used to match here).
SYS_STATUS = re.compile(r"\b(cpu|ram|memory|disk|resources?|system status)\b", re.I)
TIME_WORD = re.compile(r"\btime\b", re.I)
DATE_WORD = re.compile(r"\bdate\b|\btoday'?s date\b", re.I)

# --- YouTube ---------------------------------------------------------------- #
YT_WORD = re.compile(r"\byoutube\b", re.I)
YT_PLAY = re.compile(r"\b(play|watch|listen to|listen)\b", re.I)

# --- open / close ----------------------------------------------------------- #
OPEN_VERB = re.compile(r"\b(open|launch|start|run|show)\b", re.I)
OPEN_STRIP = re.compile(
    r"\b(open|launch|start|run|show|the|a|an|my|our|me|us|please|jarvis"
    r"|app|application)\b",
    re.I,
)

CLOSE_WORD = re.compile(r"\b(close|quit|kill|stop|exit)\b", re.I)
# "browser" is deliberately NOT stripped, so "close the browser" targets the
# browser app instead of leaving nothing.
CLOSE_STRIP = re.compile(
    r"\b(close|quit|kill|stop|exit|the|a|an|app|application|window|tab"
    r"|please|jarvis)\b",
    re.I,
)

_BARE = {
    "chrome", "firefox", "vscode", "code", "terminal", "term", "files",
    "calculator", "calc", "vlc", "telegram",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _clean(text, pattern):
    """Remove every match of ``pattern`` and collapse leftover whitespace."""
    if isinstance(pattern, str):
        pattern = re.compile(pattern, re.IGNORECASE)
    return re.sub(r"\s+", " ", pattern.sub(" ", text)).strip(" .,!?")


def _power_kind(text):
    """Return the power action name if the utterance is *only* that request."""
    match = POWER_WORD.search(text)
    if not match:
        return None
    rest = POWER_WORD.sub(" ", POWER_FILLER.sub(" ", text))
    rest = re.sub(r"\s+", " ", rest).strip(" .,!?")
    if rest:
        return None
    word = match.group(0).lower().replace(" ", "")
    return {
        "shutdown": "shutdown", "poweroff": "shutdown", "shutoff": "shutdown",
        "restart": "restart", "reboot": "restart",
        "logout": "logout", "signout": "logout",
        "suspend": "suspend", "sleep": "suspend",
    }.get(word)


def _media_action(text):
    """Map to pause/resume/next/previous/stop, but only with media context."""
    short = len(text.split()) <= 4
    noun = MEDIA_NOUN.search(text)
    if MEDIA_PAUSE.search(text) and (short or noun):
        return "resume" if re.search(r"\b(unpause|resume)\b", text, re.I) else "pause"
    if MEDIA_STOP.search(text):
        return "stop"
    if MEDIA_NEXT.search(text) and (short or noun):
        return "next"
    if MEDIA_PREV.search(text) and (short or noun):
        return "previous"
    return None


def _volume_action(text):
    if VOL_UNMUTE.search(text):
        return "unmute", None
    if VOL_MUTE.search(text):
        return "mute", None
    match = VOL_SET.search(text) or VOL_SET2.search(text)
    if match:
        return "set", int(match.group(1))
    if VOL_UP.search(text):
        return "up", None
    if VOL_DOWN.search(text):
        return "down", None
    return None


def _brightness_action(text):
    match = VOL_SET.search(text) or re.search(r"\b(\d{1,3})\s*(?:percent|%)\b", text, re.I)
    if match:
        return "set", int(match.group(1))
    if BRIGHT_UP.search(text):
        return "up", None
    if BRIGHT_DOWN.search(text):
        return "down", None
    return None


def _battery_text():
    """Report battery level / charging state, or that there is no battery."""
    battery = psutil.sensors_battery()
    if battery is None:
        return "No battery detected."
    state = "plugged in" if battery.power_plugged else "on battery"
    return f"Battery {battery.percent:.0f} percent, {state}."


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #

def try_local(text):
    """Handle a command locally; return a reply string, or ``None`` if unclear."""
    if not text or not text.strip():
        return None
    t = text.strip()

    # Search requests belong to the agent (it can call the search tools).
    if SEARCH_VERB.search(t):
        return None

    # --- power: never run straight away, always confirm first -------------- #
    kind = _power_kind(t)
    if kind:
        return confirm.request(
            lambda k=kind: desktop.power(k),
            f"Are you sure you want to {kind}?",
        )

    # --- media (playerctl) ------------------------------------------------- #
    action = _media_action(t)
    if action:
        return desktop.media_control(action)

    # --- volume ------------------------------------------------------------ #
    # A bare "sound"/"audio" with no action (e.g. "open sound settings") is not
    # a volume command, so fall through instead of swallowing it.
    if (VOL_WORD.search(t) or VOL_MUTE.search(t) or VOL_UNMUTE.search(t)
            or VOL_ADJ.search(t)):
        result = _volume_action(t)
        if result:
            return desktop.volume_control(*result)
        if not OPEN_VERB.search(t):
            return None

    # --- brightness -------------------------------------------------------- #
    if BRIGHT_WORD.search(t):
        act = _brightness_action(t)
        if act:
            return desktop.brightness_control(*act)
        if not OPEN_VERB.search(t):
            return desktop.brightness_control("set", None)

    # --- screenshot / lock ------------------------------------------------- #
    if SCREENSHOT_WORD.search(t):
        return desktop.screenshot()
    # Guard lock with the open verb so "open lock screen settings" opens the app
    # instead of instantly locking the session.
    if LOCK_WORD.search(t) and not OPEN_VERB.search(t):
        return desktop.lock_screen()

    # --- system status / battery / time / date ----------------------------- #
    if BATTERY_WORD.search(t):
        return _battery_text()
    if SYS_STATUS.search(t):
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        return f"CPU {cpu:.0f}%, RAM {ram:.0f}%, Disk {disk:.0f}%"
    if TIME_WORD.search(t):
        return f"Current time: {datetime.now().strftime('%H:%M:%S')}"
    if DATE_WORD.search(t):
        return f"Today is {datetime.now().strftime('%A, %d %B %Y')}"

    # --- close / quit / kill / stop <target> ------------------------------- #
    # Handled before YouTube so "close youtube" can never become a search query.
    if CLOSE_WORD.search(t):
        target = _clean(t, CLOSE_STRIP)
        if target:
            if YT_WORD.search(t):
                return desktop.close_youtube()
            app, score = desktop.match_app(target)
            if app is not None and score >= desktop.STRONG_MATCH:
                return desktop.close_app(app)
            if app is not None and score >= desktop.WEAK_MATCH:
                prompt = f"Did you mean {app.name}?"
                return confirm.request(lambda a=app: desktop.close_app(a), prompt)
            return None  # unknown target -> let the agent decide
        return None

    # --- YouTube: open or play (search defers to the agent) ---------------- #
    if YT_WORD.search(t):
        if YT_PLAY.search(t):
            query = desktop.extract_media_query(t)
            if not query:
                return "What should I play?"
            return desktop.play_youtube(query)
        if OPEN_VERB.search(t):
            return desktop.open_youtube()
        return None  # bare "youtube" -> agent

    # --- open an application ----------------------------------------------- #
    if OPEN_VERB.search(t):
        return desktop.open_app(_clean(t, OPEN_STRIP) or t)
    bare = re.sub(r"\s+", "", t.lower()).strip("!?.,")
    if bare in _BARE:
        return desktop.open_app(t)

    return None
