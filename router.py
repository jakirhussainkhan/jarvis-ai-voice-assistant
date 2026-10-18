"""Routes user input to the appropriate local action or the agent.

``try_local`` handles a fixed set of commands entirely offline (regex only, no
API calls): open/close apps, folders and paths, YouTube, media (including
seeking by seconds and "what's playing"), volume, brightness, screenshot, lock,
clipboard, window listing, terminal commands you dictate, power
(confirmation-gated), time, date and system status. It returns a string reply,
or ``None`` when nothing matches so the agent can decide.

Everything here works without an internet connection. Only screen reading
(:func:`vision.look`) and the agent itself need the network; :data:`CAPABILITIES`
spells out the offline set so Jarvis can explain itself when offline.

Safety rules enforced here and in :mod:`desktop`:

* only whitelisted functions run -- never ``sudo``, never arbitrary shell except
  through :func:`shell.run`, which denies anything that could power off the
  device and asks before anything risky,
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
import shell
import vision

# A short, spoken summary of what Jarvis can do with no internet at all. Used
# for "help" / "what can you do offline" and as the fallback when a network
# request fails, so Jarvis always stays useful.
CAPABILITIES = (
    "Even with no internet I can: open and close apps, open folders and files, "
    "list windows and switch, minimise or close them, control volume, media "
    "(pause, next, seek, what's playing) and brightness, take screenshots, lock "
    "the screen, copy, cut, paste and read the clipboard, tell the time, date, "
    "battery and CPU/RAM/disk use, create notes, and run the terminal commands "
    "you dictate. Web search, live answers, weather and reading the screen need "
    "a connection."
)

# Shown when the agent cannot be reached because the network is down.
OFFLINE_REPLY = "I can't reach the internet right now, so I'm staying offline. " + CAPABILITIES

# --------------------------------------------------------------------------- #
# patterns
# --------------------------------------------------------------------------- #

# A search verb means the user wants information, so the router leaves it to the
# agent (which can call the search tools).
SEARCH_VERB = re.compile(r"\b(search|look ?up|find)\b", re.I)

# A terminal request must reach the agent (it can call fn_run_command). The
# leading-verb match also stops "run ls" from being mistaken for "launch an app
# called ls"; "open terminal" stays local because "terminal" is not hinted here.
RUN_VERB = re.compile(r"^\s*(run|execute)\b", re.I)
TERMINAL_HINT = re.compile(r"\b(command|cmd|shell|bash|script|pipeline)\b", re.I)

# --- offline help / a dictated command -------------------------------------- #
# "help" or any offline question -> the offline capability list (no API call).
HELP_PHRASE = re.compile(
    r"^\s*(?:help|what can you do(?:\s+offline)?|what are your "
    r"(?:capabilities|commands|skills)|what (?:works|can i do) "
    r"(?:offline|without internet)|list (?:your )?(?:commands|capabilities))"
    r"\s*[?.!]*$",
    re.I,
)
OFFLINE_WORD = re.compile(
    r"\b(offline|without internet|without a connection|no internet|no connection)\b",
    re.I,
)

# A command spoken with an explicit marker is run directly, offline -- no model
# needed. "run firefox" deliberately does NOT match (it has no marker), so it
# still reaches the agent and is handled as an app launch.
DIRECT_CMD = re.compile(
    r"^\s*(?:run the command|run this command|execute the command|"
    r"run in the terminal|terminal|shell|bash|command|cmd)\b"
    r"\s*[:,-]?\s*(?P<cmd>.+?)\s*$",
    re.I,
)

# --- windows / now playing / seek / clipboard ------------------------------- #
LIST_WINDOWS = re.compile(
    r"\b(?:list|show|see)\b[^.]{0,15}\bwindows\b"
    r"|\bwhat\b[^.]{0,15}\bwindows\b",
    re.I,
)
NOW_PLAYING = re.compile(
    r"\b(?:what'?s playing|what is playing|now playing|what song is this|"
    r"current(?:ly)? (?:song|track|playing))\b",
    re.I,
)
SEEK = re.compile(
    r"\b(?P<dir>back|backward|rewind|forward|ahead)\b[^.]{0,12}?"
    r"(?P<n>\d{1,3})\b[^.]{0,10}?\b(?:second|sec|seconds|s)\b",
    re.I,
)
CLIP_ACTION = re.compile(
    r"^\s*(?:copy|cut|paste)(?:\s+(?:that|this|it|to clipboard))?\s*[.!]?$",
    re.I,
)
CLIP_READ = re.compile(
    r"\b(?:what'?s|what is|read|show)\b[^.]{0,15}\bclipboard\b"
    r"|\bclipboard\b[^.]{0,10}\b(?:text|content|contents)\b",
    re.I,
)

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

# --- screen reading (vision) ------------------------------------------------ #
# "what's on my screen", "read this", "describe the screen". Answered locally by
# vision.look() so it is fast and deterministic. The negative lookahead keeps
# "open screen recorder" / "screen settings" out of this branch.
SCREEN_READ = re.compile(
    r"\b(?:what'?s on|what is on|read|describe|explain|look at|look on|see|"
    r"check|show me|tell me about|summari[sz]e|scan)\b[^.]{0,30}\b"
    r"(?:screen|monitor|display)\b"
    r"(?!\s*(?:recorder|reader|lock|settings|saver|capture|shot|share|"
    r"keyboard|resolution|brightness|bright|timer|time ?out))"
    r"|\b(?:screen|monitor|display)\b[^.]{0,20}\b(?:shows?|displays?|says?)\b",
    re.I,
)

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

# --- screen actions (vision) ------------------------------------------------ #
# On-screen commands ("play the video on my screen", "click the play button on
# screen", "play the second video") are routed to vision.act() -- behind the
# same destructive-goal confirmation as fn_screen_do -- instead of becoming a
# YouTube search for a literal phrase. A screen *reference* (or a bare deictic /
# ordinal video) is required, so "play lo-fi on youtube" is left untouched.
SCREEN_ACT_VERB = re.compile(
    r"\b(play|watch|click|press|tap|hit|select|choose|open|close|pause|resume|"
    r"toggle|enable|disable|fill|type|enter|search|skip|next|previous|start|"
    r"stop|mute|unmute|scroll|switch)\b",
    re.I,
)
SCREEN_REF = re.compile(
    r"\b(?:on|in|at|from|across)\s+(?:my\s+|the\s+|this\s+|that\s+)?"
    r"(?:screen|display|monitor|desktop)\b",
    re.I,
)
# The whole utterance is just a *deictic* or *ordinal* reference to something
# already visible ("play the second song", "play this video", "play that one",
# "play it") -- never a named song to search for. Bare "the song" (no ordinal,
# no this/that) is left to the play-by-name branch below so it can ask.
_SCREEN_ORD = (r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|"
               r"tenth|eleventh|twelfth|last|next|top|bottom|main|biggest|"
               r"1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th|\d+")
_SCREEN_NOUN = (r"video|videos|song|songs|track|tracks|tune|tunes|clip|clips|"
                r"result|results|item|items|one|ones")
_ADDRESS = r"(?:please\s+|jarvis\s+|hey\s+jarvis\s+|can you\s+|could you\s+)*"
_SCREEN_VERB = r"(?:play|watch|open|start|click|select|choose|hit|tap|press)"
_SCREEN_TAIL = (r"(?:\s+(?:on|in)\s+(?:the\s+|my\s+|this\s+)?"
                r"(?:screen|display|monitor))?\s*[.!?]?\s*$")

SCREEN_ORDINAL_MEDIA = re.compile(
    rf"^\s*{_ADDRESS}{_SCREEN_VERB}\s+(?:the\s+|this\s+|that\s+|my\s+)?"
    rf"(?:{_SCREEN_ORD})\s+(?:{_SCREEN_NOUN}){_SCREEN_TAIL}",
    re.I,
)
SCREEN_DEICTIC_MEDIA = re.compile(
    rf"^\s*{_ADDRESS}{_SCREEN_VERB}\s+(?:(?:this|that)\s+(?:{_SCREEN_NOUN})|it)"
    rf"{_SCREEN_TAIL}",
    re.I,
)

# A play/watch command addressed straight at a song or video name (no "youtube"
# needed): "play kesariya", "watch lofi beats". Deliberately anchored to the
# start so it is a command, and used only after the screen branches, so
# "play the second song" is an on-screen action and not a search for those words.
PLAY_START = re.compile(
    r"^\s*(?:please\s+|jarvis\s+|hey\s+jarvis\s+|can you\s+|could you\s+)*"
    r"(?:play|watch|listen to|listen)\b",
    re.I,
)

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

    # --- a dictated command with an explicit marker runs directly (offline) - #
    # Checked before the offline-help branch so a command that happens to
    # mention, say, "offline" is still run instead of being swallowed.
    command = DIRECT_CMD.match(t)
    if command:
        return shell.run(command.group("cmd").strip())

    # --- offline help: answered locally so it works with no connection ------ #
    if HELP_PHRASE.match(t) or (OFFLINE_WORD.search(t) and len(t.split()) <= 12):
        return CAPABILITIES

    # Search requests belong to the agent (it can call the search tools).
    if SEARCH_VERB.search(t):
        return None

    # Terminal requests belong to the agent (fn_run_command / fn_run_in_terminal).
    if RUN_VERB.match(t) or TERMINAL_HINT.search(t):
        return None

    # --- power: never run straight away, always confirm first -------------- #
    kind = _power_kind(t)
    if kind:
        return confirm.request(
            lambda k=kind: desktop.power(k),
            f"Are you sure you want to {kind}?",
        )

    # --- now playing / seek ------------------------------------------------- #
    if NOW_PLAYING.search(t):
        return desktop.now_playing()
    seek = SEEK.search(t)
    if seek:
        seconds = int(seek.group("n"))
        if seek.group("dir").lower() in ("back", "backward", "rewind"):
            seconds = -seconds
        return desktop.media_seek(seconds)

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

    # --- windows ------------------------------------------------------------ #
    # Before the open branch so "what windows are open" lists windows instead of
    # hunting for an app called "what windows are".
    if LIST_WINDOWS.search(t):
        return desktop.list_windows()

    # --- screen reading (vision) ------------------------------------------- #
    # Handled before the open/close branches so "show me what's on my screen"
    # asks the vision model instead of hunting for an app called "what's on my
    # screen".
    if SCREEN_READ.search(t):
        return vision.look(t)

    # --- screen actions (vision) ------------------------------------------- #
    # After screen reading and before clipboard/close/YouTube: an on-screen
    # action ("play the video on my screen") is a UI action, not a search. The
    # goal goes through tools.screen_do so destructive goals still ask first.
    if (not YT_WORD.search(t) and SCREEN_ACT_VERB.search(t)
            and (SCREEN_REF.search(t) or SCREEN_ORDINAL_MEDIA.match(t)
                 or SCREEN_DEICTIC_MEDIA.match(t))):
        from tools import screen_do                 # imported lazily (heavy deps)
        return screen_do(t)

    # --- clipboard ---------------------------------------------------------- #
    if CLIP_READ.search(t):
        return desktop.clipboard("read")
    if CLIP_ACTION.match(t):
        return desktop.clipboard(t.split()[0].lower())

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

    # --- play a song/video by name (no "youtube" needed) ------------------- #
    # "play kesariya", "watch lofi beats", "play the song kesariya". The screen
    # branches ran first, so "play the second song" never lands here as a search
    # for the literal words; a filler-only request ("play a song") asks instead.
    if PLAY_START.search(t):
        query = desktop.extract_media_query(t)
        if not query:
            return "What should I play?"
        return desktop.play_youtube(query)

    # --- open an application, folder, file or path ------------------------- #
    if OPEN_VERB.search(t):
        target = _clean(t, OPEN_STRIP)
        if target:
            folder = desktop.resolve_folder(target)
            if folder:                       # "open downloads", "open my home folder"
                return desktop.open_path(folder)
            if re.match(r"^(?:~|/|\./|\.\./)", target):   # "open ~/notes.txt"
                return desktop.open_path(target)
        return desktop.open_app(target or t)
    bare = re.sub(r"\s+", "", t.lower()).strip("!?.,")
    if bare in _BARE:
        return desktop.open_app(t)

    return None
